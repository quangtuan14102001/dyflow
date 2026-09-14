"""Worker pool: N luong claim job tu SQLite -> chay pipeline -> cap nhat tien do.

Nhieu luong chay song song, nhung cac khau nang (Chrome/Whisper/Demucs) da bi
khoa rieng trong locks.py nen khong tranh chap tai nguyen. Job that bai duoc
hoan credit tu dong.

Chay kem web (mac dinh) hoac tach rieng:
    python -m dyflow.web.worker
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from pathlib import Path

from ..main import JobCancelled, process_job
from ..pipeline import build_web_job
from ..utils import load_config, log
from . import billing, models
from . import storage

_POLL_SEC = 1.5
_start_lock = threading.Lock()
_started = False
_stop_event: threading.Event | None = None


def _worker_count(cfg: dict) -> int:
    env = os.getenv("DYFLOW_WEB_WORKERS")
    if env and env.isdigit():
        return max(1, int(env))
    return max(1, int(cfg.get("web", {}).get("workers", 1)))


def _max_concurrent_per_user(cfg: dict) -> int:
    return models.max_concurrent_per_user(
        int(cfg.get("web", {}).get("max_concurrent_per_user", 2))
    )


def _run_one(job_row: dict, cfg: dict) -> None:
    job_id = job_row["id"]

    def _progress(stage: str, percent: int, message: str) -> None:
        if models.is_cancelled(job_id):
            raise JobCancelled("Da huy")
        models.update_progress(job_id, stage, percent, message)

    try:
        models.update_progress(job_id, "starting", 1, "Kiem tra cau hinh...")
        lang = job_row.get("target_lang") or "vi"
        cost = int(job_row.get("credits_cost") or 0)
        # Paid: xu ly theo so credit (1 credit = 2 phut, cap 30 phut).
        # Trial free (cost=0): 2 phut. Owner: full cap.
        uid = job_row.get("user_id")
        user = models.get_user(uid) if uid else None
        if user and models.is_owner_user(user):
            max_dur = billing.MAX_DURATION_SEC
        else:
            max_dur = billing.seconds_for_credits(cost)
        job = build_web_job(
            job_row["source_url"],
            voice=job_row["voice"],
            lang=lang,
            max_duration_sec=max_dur,
            auto_trim=True,
        )
        job["id"] = job_id  # dung UUID da luu trong DB
        # Batch 1000: edge. Job web: engine/model user chon tren form.
        vo = job.setdefault("voiceover", {})
        engine = str(job_row.get("tts_engine") or "edge").strip().lower() or "edge"
        vo["tts_engine"] = engine
        vo["openai_model"] = str(job_row.get("openai_model") or "tts-1").strip() or "tts-1"
        try:
            import json as _json
            from ..export_style import apply_opts_to_job

            raw_opts = job_row.get("output_opts_json") or "{}"
            apply_opts_to_job(job, _json.loads(raw_opts) if raw_opts else {})
        except Exception as e:  # noqa: BLE001
            log.warning("Job %s output_opts bo qua: %s", job_id, e)
        outputs = process_job(job, cfg, progress=_progress)
        if not outputs:
            raise RuntimeError("Pipeline khong tao duoc file output")
        out = Path(outputs[0]).resolve()
        qa_raw = None
        qa_path = Path(cfg["paths"]["work_dir"]) / job_id / "segments_qa.json"
        if qa_path.exists():
            qa_raw = qa_path.read_text(encoding="utf-8")
        if models.is_cancelled(job_id):
            storage.discard_job_work(job_id)
            log.info("WEB JOB %s da huy (truoc khi ghi completed)", job_id)
            return
        models.mark_completed(job_id, str(out), qa_json=qa_raw)
        storage.discard_job_work(job_id)
        log.info("WEB JOB %s hoan tat -> %s", job_id, out.name)
    except JobCancelled:
        log.info("WEB JOB %s da huy", job_id)
        storage.discard_job_work(job_id)
    except Exception as e:  # noqa: BLE001
        if models.is_cancelled(job_id):
            log.info("WEB JOB %s da huy (luc loi): %s", job_id, e)
            storage.discard_job_work(job_id)
            return
        log.error("WEB JOB %s LOI: %s", job_id, e)
        log.debug("%s", traceback.format_exc())
        qa_raw = None
        try:
            qa_path = Path(cfg["paths"]["work_dir"]) / job_id / "segments_qa.json"
            if qa_path.exists():
                qa_raw = qa_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            qa_raw = None
        models.mark_failed(job_id, str(e), qa_json=qa_raw)
        models.refund_job(job_id)  # hoan credit khi that bai
        storage.discard_job_work(job_id)


def _worker_loop(
    worker_no: int,
    cfg: dict,
    stop_event: threading.Event,
    max_per_user: int,
) -> None:
    while not stop_event.is_set():
        # Worker 1 thinh thoang don file cu (mien phi, khong can cloud)
        if worker_no == 1:
            try:
                storage.maybe_cleanup(cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("Retention cleanup loi: %s", e)
        try:
            from ..license_key import gate_ok

            if not gate_ok():
                time.sleep(_POLL_SEC)
                continue
            job = models.claim_next_job(max_concurrent_per_user=max_per_user)
        except Exception as e:  # noqa: BLE001
            log.warning("[W%d] claim_next_job loi: %s", worker_no, e)
            time.sleep(_POLL_SEC)
            continue
        if job is None:
            time.sleep(_POLL_SEC)
            continue
        log.info(
            "[W%d] Nhan job %s (lang=%s giong=%s user=%s)",
            worker_no,
            job["id"],
            job.get("target_lang") or "vi",
            job["voice"],
            job.get("user_id") or "-",
        )
        try:
            _run_one(job, cfg)
        except Exception as e:  # noqa: BLE001
            log.error("[W%d] job %s uncaught: %s", worker_no, job.get("id"), e)


def run_forever(config_path: str = "config.yaml", stop_event: threading.Event | None = None) -> None:
    global _started
    try:
        _run_forever(config_path, stop_event)
    except Exception as e:  # noqa: BLE001
        log.exception("Worker supervisor crash: %s", e)
        print(f"VideosCreator WORKER CRASH: {e}", flush=True)
        with _start_lock:
            _started = False


def _run_forever(config_path: str = "config.yaml", stop_event: threading.Event | None = None) -> None:
    cfg = load_config(config_path)
    models.init_db()
    recovered = models.recover_stale()
    if recovered:
        log.info("Khoi phuc: %d job dang chay dang do -> that bai + hoan credit", recovered)
        print(f"VideosCreator: go {recovered} job running cu bi ket", flush=True)

    n = _worker_count(cfg)
    max_per_user = _max_concurrent_per_user(cfg)
    # Dong bo env de create_job / queue_position dung cung lim
    os.environ["DYFLOW_MAX_CONCURRENT_PER_USER"] = str(max_per_user)
    stop_event = stop_event or threading.Event()
    msg = f"VideosCreator: worker {n} luong, max {max_per_user} job/user"
    log.info(
        "Worker pool dyflow: %d luong, max %d job/user (poll %.1fs)",
        n, max_per_user, _POLL_SEC,
    )
    print(msg, flush=True)

    try:
        from ..speakers import warmup_pyin

        log.info("Worker: warmup librosa.pyin (JIT) truoc job dau...")
        print("VideosCreator: warmup pitch detector...", flush=True)
        warmup_pyin()
    except Exception as e:  # noqa: BLE001
        log.warning("pyin warmup bo qua: %s", e)

    threads = []
    for i in range(n):
        t = threading.Thread(
            target=_worker_loop,
            args=(i + 1, cfg, stop_event, max_per_user),
            name=f"dyflow-worker-{i+1}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    def _cleanup_later() -> None:
        time.sleep(2.0)
        try:
            storage.maybe_cleanup(cfg, force=True)
        except Exception as e:  # noqa: BLE001
            log.warning("Retention cleanup (startup) loi: %s", e)

    threading.Thread(
        target=_cleanup_later, name="dyflow-cleanup-startup", daemon=True,
    ).start()

    if stop_event is None:
        for t in threads:
            t.join()


def start_background(config_path: str = "config.yaml") -> threading.Event:
    """Khoi dong worker pool trong thread nen. Tra ve stop_event de dung."""
    global _started, _stop_event
    with _start_lock:
        if _started and _stop_event is not None and not _stop_event.is_set():
            return _stop_event
        _stop_event = threading.Event()
        _started = True
        t = threading.Thread(
            target=run_forever,
            kwargs={"config_path": config_path, "stop_event": _stop_event},
            name="dyflow-worker-supervisor",
            daemon=True,
        )
        t.start()
        print("VideosCreator: worker thread started", flush=True)
        return _stop_event


def ensure_started(config_path: str | None = None) -> None:
    """Goi an toan nhieu lan. Desktop van start du khi NO_WORKER=1.

    VPS web (NO_WORKER=1, khong desktop) bo qua — worker chay process rieng.
    """
    from ..desktop_mode import enabled as desktop_on

    if os.getenv("DYFLOW_WEB_NO_WORKER") == "1" and not desktop_on():
        return
    start_background(config_path or os.getenv("DYFLOW_CONFIG") or "config.yaml")


if __name__ == "__main__":
    from ..utils import setup_logging

    setup_logging(False)
    # chay standalone: block mai mai
    _stop = threading.Event()
    try:
        run_forever(stop_event=_stop)
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        _stop.set()
