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
    # Desktop: tren may >= 4 core cho phep 2 worker chay gối đầu de dich hang loat nhanh hon
    try:
        from ..desktop_mode import enabled as desktop_on

        if desktop_on():
            cpu = os.cpu_count() or 2
            return 2 if cpu >= 4 else 1
    except Exception:  # noqa: BLE001
        pass
    return max(1, int(cfg.get("web", {}).get("workers", 1)))


def _max_concurrent_per_user(cfg: dict) -> int:
    w_count = _worker_count(cfg)
    m = models.max_concurrent_per_user(
        int(cfg.get("web", {}).get("max_concurrent_per_user", 2))
    )
    return max(w_count, m)


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
            opts_obj = _json.loads(raw_opts) if raw_opts else {}
            apply_opts_to_job(job, opts_obj)
            if opts_obj.get("chrome_fallback"):
                job["chrome_fallback"] = True
        except Exception as e:  # noqa: BLE001
            log.warning("Job %s output_opts bo qua: %s", job_id, e)
        outputs = process_job(job, cfg, progress=_progress)
        if not outputs:
            raise RuntimeError("Pipeline khong tao duoc file output")
        from ..utils import verify_output_mp4

        work_root = cfg["paths"].get("work_dir")
        out = verify_output_mp4(
            Path(outputs[0]),
            label=f"WEB JOB {job_id} output",
        )
        qa_raw = None
        qa_path = Path(cfg["paths"]["work_dir"]) / job_id / "segments_qa.json"
        if qa_path.exists():
            qa_raw = qa_path.read_text(encoding="utf-8")
        if models.is_cancelled(job_id):
            storage.discard_job_work(job_id, work_root=work_root)
            log.info("WEB JOB %s da huy (truoc khi ghi completed)", job_id)
            return
        # Xoa work TRUOC mark — neu (sai) output nam trong work/ thi se bi phat hien o verify sau.
        storage.discard_job_work(job_id, work_root=work_root)
        out = verify_output_mp4(
            out,
            label=f"WEB JOB {job_id} sau khi xoa work",
        )
        # Copy sang Save folder NGAY — truoc mark / truoc retention co the xoa output noi bo.
        delivered: Path | None = None
        try:
            from .export_delivery import copy_to_save_folder, title_from_source_path

            fresh = models.get_job(job_id) or job_row
            title = (
                title_from_source_path(str(fresh.get("source_url") or ""))
                or str(fresh.get("source_title") or "").strip()
            )
            job_dest: Path | None = None
            # 1. Uu tien hang dau: Thu muc duoc chon cua chinh job nay khi tao (khong bi de boi tab khac)
            if opts_obj and str(opts_obj.get("export_dir") or "").strip():
                p_custom = Path(str(opts_obj["export_dir"]).strip())
                if p_custom.is_dir() or p_custom.parent.is_dir():
                    job_dest = p_custom
            # 2. Neu job khong co thu muc rieng: fallback ve thu muc mac dinh cua Desktop
            if not job_dest:
                from ..desktop_mode import enabled as desktop_on
                if desktop_on():
                    from .export_delivery import load_export_dir
                    cur_pref = load_export_dir()
                    if cur_pref and (cur_pref.is_dir() or cur_pref.parent.is_dir()):
                        job_dest = cur_pref
            delivered = copy_to_save_folder(out, job_id=job_id, title=title, dest_dir=job_dest)
            models.mark_downloaded(job_id)
            log.info("WEB JOB %s delivered -> %s", job_id, delivered)
        except Exception as e:  # noqa: BLE001
            # Desktop: fail closed — user phai co file trong Save folder.
            from ..desktop_mode import enabled as desktop_on

            if desktop_on():
                raise RuntimeError(
                    f"Compose xong nhung khong luu duoc vao Save folder: {e}"
                ) from e
            log.warning("WEB JOB %s deliver Save folder that bai (web): %s", job_id, e)

        models.mark_completed(
            job_id,
            str(out),
            qa_json=qa_raw,
            message=(f"Hoan tat! Da luu: {delivered}" if delivered is not None else None),
        )
        log.info(
            "WEB JOB %s hoan tat -> %s (%d byte)%s",
            job_id,
            out.name,
            out.stat().st_size,
            f" | saved={delivered}" if delivered else "",
        )
    except JobCancelled:
        log.info("WEB JOB %s da huy", job_id)
        storage.discard_job_work(job_id, work_root=cfg["paths"].get("work_dir"))
    except Exception as e:  # noqa: BLE001
        if models.is_cancelled(job_id):
            log.info("WEB JOB %s da huy (luc loi): %s", job_id, e)
            storage.discard_job_work(job_id, work_root=cfg["paths"].get("work_dir"))
            return
        log.error("WEB JOB %s LOI: %s", job_id, e)
        log.debug("%s", traceback.format_exc())
        from ..dl_errors import DyDownloadError, classify_download_error

        err = e if isinstance(e, DyDownloadError) else classify_download_error(e)
        fail_msg = str(err)
        if err.code == DyDownloadError.NEED_LOGIN:
            fail_msg = f"[need_login] {fail_msg}"
        elif err.code == DyDownloadError.NOT_FOUND:
            fail_msg = f"[not_found] {fail_msg}"
        elif err.code == DyDownloadError.NETWORK:
            fail_msg = f"[network] {fail_msg}"
        qa_raw = None
        try:
            qa_path = Path(cfg["paths"]["work_dir"]) / job_id / "segments_qa.json"
            if qa_path.exists():
                qa_raw = qa_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            qa_raw = None
        models.mark_failed(job_id, fail_msg, qa_json=qa_raw)
        models.refund_job(job_id)  # hoan credit khi that bai
        storage.discard_job_work(job_id, work_root=cfg["paths"].get("work_dir"))


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
    from ..utils import ensure_media_bins

    ensure_media_bins()
    cfg = load_config(config_path)
    models.init_db()
    # Chi go RUNNING — GIU queued (tranh worker process moi pha hang doi UI vua tao).
    recovered = models.recover_stale(include_queued=False)
    if recovered:
        log.info("Khoi phuc: %d job dang chay dang do -> that bai + hoan credit", recovered)
        print(f"VideosCreator: go {recovered} job running cu bi ket", flush=True)

    n = _worker_count(cfg)
    max_per_user = _max_concurrent_per_user(cfg)
    # Dong bo env de create_job / queue_position dung cung lim
    os.environ["DYFLOW_MAX_CONCURRENT_PER_USER"] = str(max_per_user)
    # stop_event=None = process worker-only: join de process khong exit.
    own_stop = stop_event is None
    stop_event = stop_event or threading.Event()
    msg = f"VideosCreator: worker {n} luong, max {max_per_user} job/user"
    log.info(
        "Worker pool dyflow: %d luong, max %d job/user (poll %.1fs)",
        n, max_per_user, _POLL_SEC,
    )
    print(msg, flush=True)

    # BAT DAU worker TRUOC warmup — neu warmup pyin treo/cham, job van duoc nhan.
    threads = []
    for i in range(n):
        t = threading.Thread(
            target=_worker_loop,
            args=(i + 1, cfg, stop_event, max_per_user),
            name=f"dyflow-worker-{i+1}",
            daemon=not own_stop,
        )
        t.start()
        threads.append(t)
    print("VideosCreator: worker threads ready", flush=True)
    log.info("Worker threads ready (%d)", n)
    try:
        from ..desktop_mode import data_dir

        p = data_dir() / "startup.log"
        with p.open("a", encoding="utf-8") as f:
            f.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}  worker threads ready ({n})\n"
            )
    except Exception:  # noqa: BLE001
        pass

    def _warmup_bg() -> None:
        try:
            from ..speakers import warmup_pyin

            log.info("Worker: warmup librosa.pyin (JIT) nen...")
            print("VideosCreator: warmup pitch detector (background)...", flush=True)
            warmup_pyin()
        except Exception as e:  # noqa: BLE001
            log.warning("pyin warmup bo qua: %s", e)

    threading.Thread(
        target=_warmup_bg, name="dyflow-pyin-warmup", daemon=True,
    ).start()

    def _cleanup_later() -> None:
        time.sleep(2.0)
        try:
            storage.maybe_cleanup(cfg, force=True)
        except Exception as e:  # noqa: BLE001
            log.warning("Retention cleanup (startup) loi: %s", e)

    threading.Thread(
        target=_cleanup_later, name="dyflow-cleanup-startup", daemon=True,
    ).start()

    if own_stop:
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
    """Goi an toan nhieu lan.
    Neu da set DYFLOW_WEB_NO_WORKER=1 (vi du: desktop frozen da spawn process worker rieng, hoac VPS multi-worker) -> bo qua.
    """
    if os.getenv("DYFLOW_WEB_NO_WORKER") == "1":
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
