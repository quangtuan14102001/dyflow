"""Luu tru local + tu dong xoa file cu (mien phi, khong can S3).

Sau moi job: xoa work/<job_id> (3-7GB/clip: raw, Demucs, TTS). Chi giu MP4
trong output/ dung de tai.

Purge (2 nhánh tách biệt):
1) RETENTION theo thời gian — chỉ xoá khi đủ tuổi + (đã download HOẶC quá max_keep).
2) DISK — chỉ khi dung lượng TRỐNG thực sự thấp (< disk_min_free_gb), KHÔNG dùng %
   (C: còn 30GB vẫn có thể >80% → trước đây xoá nhầm).
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from ..utils import log
from . import models
from .media import THUMB_DIR

_MIN_INTERVAL = 600  # 10 phut
_last_run = 0.0
_WEB_JOB_DIR = re.compile(r"^web_[0-9a-f]{32}$")

# Khong xoa output moi hon 48h (tru khi o thuc su < min free GB).
OUTPUT_GRACE_SEC = 48 * 3600
# Luon giu it nhat N job gan nhat co MP4.
KEEP_RECENT_OUTPUTS = 5
# Chi coi "het o" khi con it GB trong (khong dung % — tranh C: 30GB trong van bi purge).
DEFAULT_DISK_MIN_FREE_GB = 5.0


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


def _output_root(cfg: dict | None = None) -> Path:
    try:
        raw = str(((cfg or {}).get("paths") or {}).get("output_dir") or "").strip()
        if raw:
            return Path(raw).expanduser()
    except Exception:  # noqa: BLE001
        pass
    return Path("output")


def discard_job_work(job_id: str, work_root: str | Path | None = None) -> None:
    """Xoa thu muc trung gian ngay sau khi job xong/that bai. MP4 output giu nguyen."""
    if not job_id or job_id.startswith("_"):
        return
    root = Path(work_root) if work_root else Path("work")
    work_job = root / job_id
    if not work_job.exists() or not work_job.is_dir():
        return
    keep_dir = Path("output") / "_reports"
    try:
        if root.parent.is_dir():
            keep_dir = root.parent / "output" / "_reports"
    except OSError:
        pass
    keep_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "timing.txt", "timing.json", "segments_qa.json", "tier45_review.json",
        "dub_final_report.json", "mix_recovery.json", "tts_lifecycle.jsonl",
    ):
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


def _disk_usage(path: Path) -> tuple[float, int, int]:
    u = shutil.disk_usage(path if path.exists() else Path("."))
    pct = 100.0 * u.used / u.total if u.total else 0.0
    return pct, u.used, u.free


def _disk_critical(cfg: dict | None, root: Path) -> tuple[bool, float, float]:
    """Chi critical khi GB trong < nguong. Tra (critical, pct, free_gb)."""
    pct, _, free = _disk_usage(root)
    free_gb = free / (1024 ** 3)
    min_free = _web_num(cfg, "disk_min_free_gb", DEFAULT_DISK_MIN_FREE_GB)
    return free_gb < min_free, pct, free_gb


def maybe_cleanup(cfg: dict, *, force: bool = False) -> int:
    """Xoa file qua han + cache + orphan. Disk purge chi khi con it GB trong."""
    global _last_run
    now = time.time()
    root = _output_root(cfg)
    critical, pct, free_gb = _disk_critical(cfg, root)
    if not force and not critical and (now - _last_run) < _MIN_INTERVAL:
        return 0
    _last_run = now

    days = retention_days(cfg)
    n = 0
    if days > 0:
        n += purge_expired_files(days, cfg=cfg)
    cache_days = int(_web_num(cfg, "dl_cache_days", 2))
    cache_gb = _web_num(cfg, "dl_cache_max_gb", 8)
    n += _purge_old_dl_cache(max(1, cache_days))
    n += _purge_dl_cache_size(max(1.0, cache_gb))
    n += _purge_orphans()
    n += _purge_failed_work()
    if critical:
        log.warning(
            "Disk CRITICAL: %.1f GB trong (%.0f%% used) tren %s — purge MP4 cu",
            free_gb, pct, root,
        )
        n += _purge_until_disk(cfg, free_gb=free_gb)
    if n:
        log.info(
            "Retention: da don %d muc (giu MP4 %d ngay, disk %.0f%% / %.1f GB trong)",
            n, days, pct, free_gb,
        )
    return n


def purge_expired_files(days: int, cfg: dict | None = None) -> int:
    """Xoa output qua han.

    Quy tac:
    - Chua du grace 48h: KHONG xoa (truong hop thuong).
    - Da download + qua retention_days: xoa duoc.
    - Chua download: chi xoa khi qua max(retention_days, 7) ngay (force).
    """
    if days <= 0:
        return 0
    now = time.time()
    grace_cut = now - OUTPUT_GRACE_SEC
    retention_cut = now - days * 86400
    force_undl_days = max(days, 7)
    force_undl_cut = now - force_undl_days * 86400
    n = 0
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT id, output_path, finished_at, created_at, "
            "COALESCE(downloaded,0) AS downloaded FROM jobs "
            "WHERE status=? AND output_path IS NOT NULL AND output_path != ''",
            (models.STATUS_COMPLETED,),
        ).fetchall()
        for r in rows:
            ts = float(r["finished_at"] or r["created_at"] or 0)
            # Moi hon 48h hoac thieu timestamp → khong xoa.
            if ts <= 0 or ts > grace_cut:
                continue
            downloaded = int(r["downloaded"] or 0) == 1
            if downloaded and ts <= retention_cut:
                reason = (
                    f"Het han luu tru (da qua {days} ngay, user da tai ve). "
                    f"Ban copy van o Save folder neu da luu luc xong."
                )
                if _forget_job_files(conn, r["id"], r["output_path"], reason=reason):
                    n += 1
            elif (not downloaded) and ts <= force_undl_cut:
                reason = (
                    f"Het han luu tru (da qua {force_undl_days} ngay, chua bam Download). "
                    f"Kiem tra Save folder — neu khong thay, chay lai video."
                )
                if _forget_job_files(conn, r["id"], r["output_path"], reason=reason):
                    n += 1
        conn.commit()
    return n


def _forget_job_files(
    conn,
    job_id: str,
    output_path: str | None,
    *,
    reason: str,
) -> bool:
    out = (output_path or "").strip()
    if out:
        p = Path(out)
        try:
            if p.exists() and p.is_file():
                p.unlink()
        except OSError as e:
            log.warning("Xoa output %s that bai: %s", p, e)
    work_job = Path("work") / job_id
    if work_job.exists() and work_job.is_dir():
        try:
            shutil.rmtree(work_job, ignore_errors=True)
        except OSError as e:
            log.warning("Xoa work/%s that bai: %s", job_id, e)
    thumb = THUMB_DIR / f"{job_id}.jpg"
    if thumb.exists():
        try:
            thumb.unlink()
        except OSError:
            pass
    msg = (reason or "File noi bo da duoc don dep.").strip()[:2000]
    conn.execute(
        "UPDATE jobs SET output_path='', message=? WHERE id=?",
        (msg, job_id),
    )
    return True


def _purge_until_disk(cfg: dict, *, free_gb: float) -> int:
    """Chi goi khi o thuc su it GB trong. Van ton trong grace + KEEP_RECENT."""
    min_free = _web_num(cfg, "disk_min_free_gb", DEFAULT_DISK_MIN_FREE_GB)
    n = 0
    root = _output_root(cfg)
    for _ in range(400):
        critical, pct, cur_free = _disk_critical(cfg, root)
        if not critical:
            break
        with models._connect() as conn:
            kept = conn.execute(
                "SELECT COUNT(*) AS c FROM jobs "
                "WHERE status=? AND output_path IS NOT NULL AND output_path != ''",
                (models.STATUS_COMPLETED,),
            ).fetchone()
            if kept and int(kept["c"] or 0) <= KEEP_RECENT_OUTPUTS:
                log.warning(
                    "Disk %.1f GB trong nhung chi con %d job co MP4 — khong xoa them",
                    cur_free, int(kept["c"] or 0),
                )
                break
            grace_cut = time.time() - OUTPUT_GRACE_SEC
            # Uu tien xoa job DA download, cu nhat, ngoai grace.
            row = conn.execute(
                "SELECT id, output_path, finished_at, created_at, "
                "COALESCE(downloaded,0) AS downloaded FROM jobs "
                "WHERE status=? AND output_path IS NOT NULL AND output_path != '' "
                "AND COALESCE(finished_at, created_at) < ? "
                "ORDER BY COALESCE(downloaded,0) DESC, "
                "COALESCE(finished_at, created_at) ASC LIMIT 1",
                (models.STATUS_COMPLETED, grace_cut),
            ).fetchone()
            if not row:
                log.warning(
                    "Disk %.1f GB trong — khong con job cu hon %dh de xoa",
                    cur_free, OUTPUT_GRACE_SEC // 3600,
                )
                break
            reason = (
                f"Het dung luong o dia (con {cur_free:.1f} GB trong, nguong {min_free:g} GB). "
                f"Da xoa file noi bo job cu — kiem tra Save folder."
            )
            if _forget_job_files(conn, row["id"], row["output_path"], reason=reason):
                n += 1
            conn.commit()
        log.warning(
            "Disk %.1f GB trong (%.0f%%) — xoa output job cu %s (downloaded=%s)",
            cur_free, pct, row["id"], row["downloaded"],
        )
    return n


def _active_job_ids() -> set[str]:
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status IN (?, ?)",
            (models.STATUS_QUEUED, models.STATUS_RUNNING),
        ).fetchall()
    return {str(r["id"]) for r in rows}


def _purge_failed_work() -> int:
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
    if not cache.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for p in cache.rglob("*"):
        if not p.is_file():
            continue
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


def _purge_dl_cache_size(max_gb: float) -> int:
    cache = Path("work") / "_dl_cache"
    if not cache.is_dir():
        return 0
    files = []
    total = 0
    for p in cache.rglob("*"):
        if not p.is_file():
            continue
        try:
            sz = p.stat().st_size
            files.append((p.stat().st_mtime, sz, p))
            total += sz
        except OSError:
            pass
    limit = int(max_gb * (1024 ** 3))
    if total <= limit:
        return 0
    files.sort()
    n = 0
    for _, sz, p in files:
        if total <= limit:
            break
        try:
            p.unlink()
            total -= sz
            n += 1
        except OSError:
            pass
    return n
