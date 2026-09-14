"""Luu tru local + tu dong xoa file cu (mien phi, khong can S3).

Sau moi job: xoa work/<job_id> (3-7GB/clip: raw, Demucs, TTS). Chi giu MP4
trong output/ dung de tai. Het retention_days thi xoa MP4, lich su van con.
Disk >= high_pct: xoa output cu nhat cho den target_pct — tranh 500 vi het o.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from ..utils import log
from . import models
from .media import THUMB_DIR

_MIN_INTERVAL = 600  # 10 phut (truoc 1h — de kip truoc khi full)
_last_run = 0.0
_WEB_JOB_DIR = re.compile(r"^web_[0-9a-f]{32}$")


def retention_days(cfg: dict | None = None) -> int:
    if cfg is None:
        return 7
    web = cfg.get("web") or {}
    try:
        return max(0, int(web.get("retention_days", 7)))
    except (TypeError, ValueError):
        return 7


def _web_num(cfg: dict | None, key: str, default: float) -> float:
    web = (cfg or {}).get("web") or {}
    try:
        return float(web.get(key, default))
    except (TypeError, ValueError):
        return default


def discard_job_work(job_id: str) -> None:
    """Xoa thu muc trung gian ngay sau khi job xong/that bai. MP4 output giu nguyen.

    Giu timing + QA de so toc do / thu tu doan (khong can giu raw/Demucs/TTS).
    """
    if not job_id or job_id.startswith("_"):
        return
    work_job = Path("work") / job_id
    if not work_job.exists() or not work_job.is_dir():
        return
    keep_dir = Path("output") / "_reports"
    keep_dir.mkdir(parents=True, exist_ok=True)
    for name in ("timing.txt", "timing.json", "segments_qa.json"):
        src = work_job / name
        if src.exists() and src.stat().st_size > 0:
            dest = keep_dir / f"{job_id}_{name}"
            try:
                shutil.copy2(src, dest)
                log.info("Giu %s -> %s", name, dest)
            except OSError as e:
                log.warning("Khong copy %s: %s", name, e)
    try:
        shutil.rmtree(work_job)
        log.info("Da xoa work/%s (trung gian)", job_id)
    except OSError as e:
        log.warning("Xoa work/%s that bai: %s", job_id, e)


def maybe_cleanup(cfg: dict, *, force: bool = False) -> int:
    """Xoa file qua han + cache + orphan. Rate-limit; bo qua neu disk sap full."""
    global _last_run
    now = time.time()
    high = _web_num(cfg, "disk_high_pct", 80)
    pct, _, _ = _disk_usage(Path("."))
    pressure = pct >= high
    if not force and not pressure and (now - _last_run) < _MIN_INTERVAL:
        return 0
    _last_run = now

    days = retention_days(cfg)
    n = 0
    if days > 0:
        n += purge_expired_files(days)
    cache_days = int(_web_num(cfg, "dl_cache_days", 2))
    cache_gb = _web_num(cfg, "dl_cache_max_gb", 8)
    n += _purge_old_dl_cache(max(1, cache_days))
    n += _purge_dl_cache_size(max(1.0, cache_gb))
    n += _purge_orphans()
    n += _purge_failed_work()
    if pressure or pct >= high:
        n += _purge_until_disk(cfg, target_pct=_web_num(cfg, "disk_target_pct", 70))
    if n:
        log.info("Retention: da don %d muc (giu MP4 %d ngay, disk %.0f%%)", n, days, pct)
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
            if _forget_job_files(conn, r["id"], r["output_path"]):
                n += 1
        conn.commit()
    return n


def _forget_job_files(conn, job_id: str, output_path: str | None) -> bool:
    removed = False
    out = (output_path or "").strip()
    if out:
        p = Path(out)
        try:
            if p.exists() and p.is_file():
                p.unlink()
                removed = True
        except OSError as e:
            log.warning("Xoa output %s that bai: %s", p, e)
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
    conn.execute("UPDATE jobs SET output_path='' WHERE id=?", (job_id,))
    return True


def _active_job_ids() -> set[str]:
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status IN (?, ?)",
            (models.STATUS_QUEUED, models.STATUS_RUNNING),
        ).fetchall()
    return {str(r["id"]) for r in rows}


def _purge_failed_work() -> int:
    """Job fail van de lai 3-7GB — xoa work, khong dung toi output."""
    n = 0
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status=?",
            (models.STATUS_FAILED,),
        ).fetchall()
    for r in rows:
        p = Path("work") / r["id"]
        if p.exists() and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            n += 1
    return n


def _purge_orphans() -> int:
    """work/web_* khong con job dang chay — rac tu crash / copy tay."""
    work = Path("work")
    if not work.exists():
        return 0
    active = _active_job_ids()
    n = 0
    for p in work.iterdir():
        if not p.is_dir() or not _WEB_JOB_DIR.match(p.name):
            continue
        if p.name in active:
            continue
        shutil.rmtree(p, ignore_errors=True)
        n += 1
    return n


def _purge_old_dl_cache(days: int) -> int:
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


def _purge_dl_cache_size(max_gb: float) -> int:
    cache = Path("work") / "_dl_cache"
    if not cache.exists() or max_gb <= 0:
        return 0
    files: list[tuple[float, int, Path]] = []
    total = 0
    for p in cache.iterdir():
        try:
            if not p.is_file():
                continue
            st = p.stat()
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        except OSError:
            pass
    limit = int(max_gb * 1024 ** 3)
    if total <= limit:
        return 0
    files.sort()  # cu nhat truoc
    n = 0
    for _mtime, size, p in files:
        if total <= limit:
            break
        try:
            p.unlink()
            total -= size
            n += 1
        except OSError:
            pass
    return n


def _disk_usage(path: Path) -> tuple[float, int, int]:
    u = shutil.disk_usage(path)
    pct = 100.0 * u.used / u.total if u.total else 0.0
    return pct, u.used, u.free


def _purge_until_disk(cfg: dict, *, target_pct: float) -> int:
    """Xoa MP4 completed cu nhat cho den khi duoi target_pct. Job dang chay khong dung."""
    n = 0
    for _ in range(400):
        pct, _, _ = _disk_usage(Path("."))
        if pct < target_pct:
            break
        with models._connect() as conn:
            row = conn.execute(
                "SELECT id, output_path, finished_at, created_at FROM jobs "
                "WHERE status=? AND output_path IS NOT NULL AND output_path != '' "
                "ORDER BY COALESCE(finished_at, created_at) ASC LIMIT 1",
                (models.STATUS_COMPLETED,),
            ).fetchone()
            if not row:
                break
            if _forget_job_files(conn, row["id"], row["output_path"]):
                n += 1
            conn.commit()
        log.warning("Disk %.0f%% — xoa output job cu %s", pct, row["id"])
    return n
