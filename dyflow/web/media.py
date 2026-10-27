"""Thumbnail + metadata cho thu vien video."""
from __future__ import annotations

import time
from pathlib import Path

from ..utils import log, probe_duration, run

THUMB_DIR = Path("work") / "thumbs"


def format_duration(seconds: float) -> str:
    sec = max(0, int(round(seconds or 0)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def relative_time(ts: float | None, *, now: float | None = None) -> str:
    """Chuoi thoi gian tuong doi (tieng Viet ngan) — UI i18n co the override sau."""
    if not ts:
        return ""
    now = now or time.time()
    diff = max(0, int(now - ts))
    if diff < 60:
        return "just_now"
    if diff < 3600:
        return f"m:{diff // 60}"
    if diff < 86400:
        return f"h:{diff // 3600}"
    if diff < 86400 * 30:
        return f"d:{diff // 86400}"
    return f"mo:{diff // (86400 * 30)}"


def ensure_thumbnail(job_id: str, video_path: str | Path) -> Path | None:
    """Tao (hoac lay cache) anh thumbnail JPEG tu video."""
    video_path = Path(video_path)
    if not video_path.exists():
        return None
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    thumb = THUMB_DIR / f"{job_id}.jpg"
    if thumb.exists() and thumb.stat().st_size > 500:
        return thumb
    try:
        run([
            "ffmpeg", "-y",
            "-ss", "1",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "4",
            "-vf", "scale=640:-2",
            str(thumb),
        ])
    except Exception as e:  # noqa: BLE001
        log.warning("Thumbnail fail %s: %s", job_id, e)
        return None
    if thumb.exists() and thumb.stat().st_size > 500:
        return thumb
    return None


def enrich_library_item(job: dict) -> dict:
    """Them duration / title / thumb flag cho 1 job completed."""
    out = dict(job)
    path = job.get("output_path") or ""
    p = Path(path) if path else None
    dur = probe_duration(p) if p and p.exists() else 0.0
    out["duration_sec"] = dur
    out["duration_label"] = format_duration(dur)
    out["has_file"] = bool(p and p.exists())
    out["expired"] = (job.get("status") == "completed") and not out["has_file"]
    # Tieu de ro rang cho video (xu ly Windows backslash + strip prefix upload)
    clean_title = ""
    src = str(job.get("source_url") or "").strip()
    if src:
        try:
            from .export_delivery import title_from_source_path
            clean_title = title_from_source_path(src)
        except Exception:
            clean_title = ""
    if not clean_title and job.get("source_title"):
        clean_title = str(job["source_title"]).strip()
    if not clean_title and src:
        fname = src.replace("\\", "/").rstrip("/").split("/")[-1]
        if fname.lower().startswith("local_") and len(fname) > 15 and fname[14] == "_":
            fname = fname[15:]
        clean_title = fname
    out["title"] = clean_title or job.get("id", "video")
    if len(out["title"]) > 55:
        out["title"] = out["title"][:52] + "…"
    out["rel_key"] = relative_time(job.get("finished_at") or job.get("created_at"))
    return out
