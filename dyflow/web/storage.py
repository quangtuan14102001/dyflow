"""Luu tru local + tu dong xoa file cu (mien phi, khong can S3).

Giu metadata job trong DB de lich su van hien; chi xoa MP4/raw/thumb.
retention_days=0 → khong xoa.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..utils import log
from . import models
from .media import THUMB_DIR

# Chay cleanup toi da 1 lan / khoang nay (giay)
_MIN_INTERVAL = 3600
_last_run = 0.0


def retention_days(cfg: dict | None = None) -> int:
    if cfg is None:
        return 7
    web = cfg.get("web") or {}
    try:
        return max(0, int(web.get("retention_days", 7)))
    except (TypeError, ValueError):
        return 7


def maybe_cleanup(cfg: dict, *, force: bool = False) -> int:
    """Xoa file qua han. Tra ve so job da don. Rate-limit 1h tru force."""
    global _last_run
    now = time.time()
    if not force and (now - _last_run) < _MIN_INTERVAL:
        return 0
    _last_run = now
    days = retention_days(cfg)
    if days <= 0:
        return 0
    n = purge_expired_files(days)
    n += _purge_old_dl_cache(days)
    if n:
        log.info("Retention: da don %d muc (giu %d ngay)", n, days)
    return n


def purge_expired_files(days: int) -> int:
    """Xoa output + work/<job_id> + thumb cho job completed qua han."""
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT id, output_path, finished_at, created_at FROM jobs "
            "WHERE status=? AND output_path IS NOT NULL AND output_path != ''",
            (models.STATUS_COMPLETED,),
        ).fetchall()
        for r in rows:
            ts = r["finished_at"] or r["created_at"] or 0
            if float(ts or 0) > cutoff:
                continue
            job_id = r["id"]
            out = (r["output_path"] or "").strip()
            removed = False
            if out:
                p = Path(out)
                try:
                    if p.exists() and p.is_file():
                        p.unlink()
                        removed = True
                except OSError as e:
                    log.warning("Xoa output %s that bai: %s", p, e)
            # work dir cua job
            work_job = Path("work") / job_id
            if work_job.exists() and work_job.is_dir():
                try:
                    shutil.rmtree(work_job, ignore_errors=True)
                    removed = True
                except OSError as e:
                    log.warning("Xoa work/%s that bai: %s", job_id, e)
            thumb = THUMB_DIR / f"{job_id}.jpg"
            if thumb.exists():
                try:
                    thumb.unlink()
                    removed = True
                except OSError:
                    pass
            if removed:
                # Giữ lịch sử: xóa path để UI biết file hết hạn
                conn.execute(
                    "UPDATE jobs SET output_path='' WHERE id=?",
                    (job_id,),
                )
                n += 1
        conn.commit()
    return n


def _purge_old_dl_cache(days: int) -> int:
    """Xoa cache download cu hon retention (tiet kiem o dia)."""
    cache = Path("work") / "_dl_cache"
    if not cache.exists() or days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for p in cache.iterdir():
        try:
            if not p.is_file():
                continue
            if p.stat().st_mtime > cutoff:
                continue
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
