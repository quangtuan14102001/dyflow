"""TTS file I/O: temp duy nhat, fsync, atomic replace, lifecycle log, retry doc."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .utils import log

# MP3 ~192kbps -> ~24KB/s. Nguong an toan ~30% de tranh false-positive.
_MP3_BYTES_PER_SEC = 24_000
_MIN_CLIP_BYTES = 100


def unique_tmp(dest: Path, *, tag: str = "tts") -> Path:
    """Ten tam duy nhat theo stem + pid + uuid — khong dung chung giua luong."""
    dest = Path(dest)
    token = uuid.uuid4().hex[:10]
    return dest.with_name(f"{dest.stem}._{tag}_{os.getpid()}_{token}.mp3")


def fsync_file(path: Path) -> None:
    path = Path(path)
    with open(path, "rb+") as f:
        f.flush()
        os.fsync(f.fileno())
    if hasattr(os, "sync"):
        try:
            os.sync()
        except OSError:
            pass


def atomic_replace(src: Path, dest: Path) -> None:
    """fsync src roi os.replace — Windows/AV an toan hon Path.replace thuan."""
    src = Path(src)
    dest = Path(dest)
    if not src.exists():
        raise FileNotFoundError(f"atomic_replace: thieu src {src}")
    fsync_file(src)
    os.replace(str(src), str(dest))
    # Best-effort fsync dest (mot so FS khong cho rb+ ngay sau replace).
    try:
        fsync_file(dest)
    except OSError:
        pass


def write_bytes_atomic(dest: Path, data: bytes) -> Path:
    dest = Path(dest)
    tmp = unique_tmp(dest, tag="wb")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        atomic_replace(tmp, dest)
        return dest
    finally:
        tmp.unlink(missing_ok=True)


def min_bytes_for_duration(expected_dur_sec: float) -> int:
    """Size toi thieu hop ly theo duration ky vong (MP3)."""
    dur = max(0.0, float(expected_dur_sec or 0))
    if dur < 0.05:
        return _MIN_CLIP_BYTES
    return max(_MIN_CLIP_BYTES, int(dur * _MP3_BYTES_PER_SEC * 0.30))


def file_size_or_0(path: Path) -> int:
    try:
        p = Path(path)
        return int(p.stat().st_size) if p.exists() else 0
    except OSError:
        return 0


def wait_file_ready(
    path: Path,
    *,
    min_bytes: int = _MIN_CLIP_BYTES,
    retries: int = 5,
    base_delay_sec: float = 0.12,
) -> bool:
    """Retry doc file (AV lock / sharing violation / size=0 tạm)."""
    path = Path(path)
    last_err = ""
    for attempt in range(max(1, int(retries))):
        try:
            if path.exists():
                sz = path.stat().st_size
                if sz >= int(min_bytes):
                    return True
                last_err = f"size={sz}<{min_bytes}"
            else:
                last_err = "missing"
        except OSError as e:
            last_err = str(e)
        if attempt + 1 < retries:
            time.sleep(float(base_delay_sec) * (attempt + 1))
    log.warning("wait_file_ready FAIL %s after %d tries (%s)", path.name, retries, last_err)
    return False


def clip_integrity_ok(
    path: Path,
    *,
    expected_dur_sec: float | None = None,
    retries: int = 5,
) -> bool:
    min_b = (
        min_bytes_for_duration(expected_dur_sec)
        if expected_dur_sec is not None
        else _MIN_CLIP_BYTES
    )
    return wait_file_ready(path, min_bytes=min_b, retries=retries)


def lifecycle_log(
    work_dir: Path | None,
    event: str,
    path: Path | str | None = None,
    **extra: Any,
) -> None:
    """Ghi 1 dong JSONL: timestamp wall + monotonic + size + event."""
    now = time.time()
    mono = time.perf_counter()
    p = Path(path) if path else None
    row: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "epoch": round(now, 3),
        "mono": round(mono, 4),
        "event": event,
        "file": p.name if p else None,
        "size": file_size_or_0(p) if p else None,
    }
    row.update(extra)
    line = json.dumps(row, ensure_ascii=False)
    log.info("TTS_LIFE %s", line)
    if work_dir is None:
        return
    try:
        out = Path(work_dir) / "tts_lifecycle.jsonl"
        with open(out, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except OSError as e:
        log.debug("tts_lifecycle ghi loi: %s", e)


def finalize_synthesized(
    tmp: Path,
    dest: Path,
    *,
    work_dir: Path | None = None,
    seg_i: int | None = None,
    stage: str = "tts_write",
) -> bool:
    """Sau khi engine ghi tmp: fsync + replace + verify size."""
    tmp = Path(tmp)
    dest = Path(dest)
    lifecycle_log(
        work_dir, f"{stage}_begin", tmp,
        seg_i=seg_i, dest=dest.name,
    )
    if not tmp.exists() or tmp.stat().st_size < _MIN_CLIP_BYTES:
        lifecycle_log(
            work_dir, f"{stage}_empty_tmp", tmp,
            seg_i=seg_i, dest=dest.name,
        )
        tmp.unlink(missing_ok=True)
        return False
    try:
        t0 = time.perf_counter()
        atomic_replace(tmp, dest)
        ok = wait_file_ready(dest, min_bytes=_MIN_CLIP_BYTES, retries=5)
        lifecycle_log(
            work_dir, f"{stage}_done", dest,
            seg_i=seg_i,
            rename_ms=round((time.perf_counter() - t0) * 1000, 1),
            ok=ok,
        )
        return ok
    except OSError as e:
        lifecycle_log(
            work_dir, f"{stage}_replace_fail", dest,
            seg_i=seg_i, err=str(e),
        )
        tmp.unlink(missing_ok=True)
        return False
    finally:
        tmp.unlink(missing_ok=True)
