"""Cache file tai Douyin da co audio — tranh tai lai / fail ngau nhien.

Khoa cache theo aweme/video id (on dinh). Short link duoc resolve truoc khi tai.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .utils import log, probe_has_audio

CACHE_DIR = Path("work") / "_dl_cache"


def short_key(url: str) -> str | None:
    """Lay ma short link Douyin (vd a5M0Y6uJuXI)."""
    m = re.search(r"v\.douyin\.com/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else None


def remember_short(url: str, video_id: str | None) -> None:
    sk = short_key(url)
    if not sk or not video_id:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"short_{sk}.txt").write_text(video_id, encoding="utf-8")


def resolve_from_short(url: str) -> str | None:
    sk = short_key(url)
    if not sk:
        return None
    p = CACHE_DIR / f"short_{sk}.txt"
    if p.exists():
        vid = p.read_text(encoding="utf-8").strip()
        return vid or None
    return None


def video_id_from_text(*texts: str) -> str | None:
    """Lay aweme/video id tu URL hoac HTML."""
    for text in texts:
        if not text:
            continue
        for pat in (
            r"/video/(\d{10,})",
            r"modal_id=(\d{10,})",
            r"aweme[_-]?id[\"'=\s:]+(\d{10,})",
            r"\"group_id\"\s*:\s*\"?(\d{10,})",
            r"\"aweme_id\"\s*:\s*\"?(\d{10,})",
        ):
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1)
    return None


def cache_path(video_id: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{video_id}.mp4"


def try_reuse(video_id: str | None, dest_dir: Path) -> Path | None:
    """Copy tu cache neu file hop le (co audio)."""
    if not video_id:
        return None
    src = cache_path(video_id)
    if not src.exists() or src.stat().st_size < 10_000:
        return None
    if not probe_has_audio(src):
        log.warning("Cache %s khong co audio — xoa", src.name)
        src.unlink(missing_ok=True)
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{video_id}.mp4"
    shutil.copy2(src, dest)
    log.info("Tai dung lai cache: %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


def save(video_id: str | None, file_path: Path) -> None:
    """Luu ban thanh cong vao cache."""
    if not video_id or not file_path.exists():
        return
    if not probe_has_audio(file_path):
        return
    dest = cache_path(video_id)
    try:
        if dest.resolve() == file_path.resolve():
            return
        shutil.copy2(file_path, dest)
        log.info("Da cache download: %s", dest.name)
    except Exception as e:  # noqa: BLE001
        log.warning("Cache download that bai: %s", e)


def find_local_good_copy(video_id: str | None) -> Path | None:
    """Tim ban mp4+audio da tai thanh cong trong work/*/raw (lan truoc)."""
    if not video_id:
        return None
    work = Path("work")
    if not work.exists():
        return None
    cands: list[Path] = []
    for p in work.glob(f"*/raw/{video_id}.mp4"):
        if ".video." in p.name:
            continue
        if p.stat().st_size > 10_000:
            cands.append(p)
    # uu tien moi nhat
    cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    for p in cands:
        if probe_has_audio(p):
            return p
    # trimmed ban cung duoc neu co audio
    for p in work.glob(f"*/raw/{video_id}_trim*.mp4"):
        if probe_has_audio(p) and p.stat().st_size > 10_000:
            return p
    return None
