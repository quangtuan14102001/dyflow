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

from ..main import process_job
from ..pipeline import build_web_job
from ..utils import load_config, log
from . import models
from . import storage

_POLL_SEC = 1.5


def _worker_count(cfg: dict) -> int:
    env = os.getenv("DYFLOW_WEB_WORKERS")
    if env and env.isdigit():
        return max(1, int(env))
    return max(1, int(cfg.get("web", {}).get("workers", 1)))


def _run_one(job_row: dict, cfg: dict) -> None:
    job_id = job_row["id"]

    def _progress(stage: str, percent: int, message: str) -> None:
        models.update_progress(job_id, stage, percent, message)

    try:
        lang = job_row.get("target_lang") or "vi"
        job = build_web_job(job_row["source_url"], voice=job_row["voice"], lang=lang)
        job["id"] = job_id  # dung UUID da luu trong DB
        outputs = process_job(job, cfg, progress=_progress)
        if not outputs:
            raise RuntimeError("Pipeline khong tao duoc file output")
        out = Path(outputs[0]).resolve()
        models.mark_completed(job_id, str(out))
        log.info("WEB JOB %s hoan tat -> %s", job_id, out.name)
    except Exception as e:  # noqa: BLE001
        log.error("WEB JOB %s LOI: %s", job_id, e)
        log.debug("%s", traceback.format_exc())
        models.mark_failed(job_id, str(e))
        models.refund_job(job_id)  # hoan credit khi that bai


def _worker_loop(worker_no: int, cfg: dict, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        # Worker 1 thinh thoang don file cu (mien phi, khong can cloud)
        if worker_no == 1:
            try:
                storage.maybe_cleanup(cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("Retention cleanup loi: %s", e)
        job = models.claim_next_job()
        if job is None:
            time.sleep(_POLL_SEC)
            continue
        log.info(
            "[W%d] Nhan job %s (lang=%s giong=%s)",
            worker_no, job["id"], job.get("target_lang") or "vi", job["voice"],
        )
        _run_one(job, cfg)


def run_forever(config_path: str = "config.yaml", stop_event: threading.Event | None = None) -> None:
    cfg = load_config(config_path)
    models.init_db()
    recovered = models.recover_stale()
    if recovered:
        log.info("Khoi phuc: %d job dang chay dang do -> that bai + hoan credit", recovered)

    try:
        storage.maybe_cleanup(cfg, force=True)
    except Exception as e:  # noqa: BLE001
        log.warning("Retention cleanup (startup) loi: %s", e)

    n = _worker_count(cfg)
    stop_event = stop_event or threading.Event()
    log.info("Worker pool dyflow: %d luong (poll %.1fs)", n, _POLL_SEC)

    threads = []
    for i in range(n):
        t = threading.Thread(
            target=_worker_loop,
            args=(i + 1, cfg, stop_event),
            name=f"dyflow-worker-{i+1}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    if stop_event is None:
        for t in threads:
            t.join()


def start_background(config_path: str = "config.yaml") -> threading.Event:
    """Khoi dong worker pool trong thread nen. Tra ve stop_event de dung."""
    stop_event = threading.Event()
    t = threading.Thread(
        target=run_forever,
        kwargs={"config_path": config_path, "stop_event": stop_event},
        name="dyflow-worker-supervisor",
        daemon=True,
    )
    t.start()
    return stop_event


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
