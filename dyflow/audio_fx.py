"""Tach giong (vocal) khoi audio goc — giu nhac/FX, bo loi noi."""
from __future__ import annotations
from pathlib import Path

from .utils import log, run, probe_duration


def extract_wav(video: str | Path, out_wav: str | Path) -> Path:
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le",
        str(out_wav),
    ])
    return out_wav


def strip_vocals_demucs(
    audio_wav: str | Path,
    out_dir: str | Path,
    *,
    model: str = "htdemucs",
) -> Path:
    """Demucs two-stems -> tra ve file no_vocals.wav (nhac + FX)."""
    import sys
    try:
        import demucs  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "Chua cai demucs. Chay: pip install demucs"
        ) from e

    audio_wav = Path(audio_wav)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from .web.locks import DEMUCS_LOCK
    except Exception:  # noqa: BLE001
        DEMUCS_LOCK = None

    if DEMUCS_LOCK is not None:
        DEMUCS_LOCK.acquire()
    try:
        run([
            sys.executable, "-m", "demucs",
            "-n", model,
            "--two-stems", "vocals",
            "-o", str(out_dir),
            str(audio_wav),
        ])
    finally:
        if DEMUCS_LOCK is not None:
            DEMUCS_LOCK.release()

    candidates = list(out_dir.glob(f"{model}/*/no_vocals.wav"))
    if not candidates:
        candidates = list(out_dir.rglob("no_vocals.wav"))
    if not candidates:
        raise RuntimeError(f"Demucs khong tao no_vocals.wav trong {out_dir}")
    best = max(candidates, key=lambda p: p.stat().st_mtime)
    log.info("Da tach vocal -> %s (%.1fs)", best.name, probe_duration(best))
    return best


def make_ambient_bed(
    video: str | Path,
    work_dir: str | Path,
    *,
    enabled: bool = True,
    model: str = "htdemucs",
) -> Path | None:
    """Tao track nen khong giong noi. None neu tat / that bai."""
    if not enabled:
        return None
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cached = work_dir / "ambient_no_vocals.wav"
    if cached.exists() and cached.stat().st_size > 1000:
        # cache hop le neu do dai gan bang video
        try:
            if abs(probe_duration(cached) - probe_duration(video)) < 1.5:
                log.info("Dung ambient cache: %s", cached.name)
                return cached
        except Exception:  # noqa: BLE001
            pass

    wav = extract_wav(video, work_dir / "full_audio.wav")
    try:
        no_voc = strip_vocals_demucs(wav, work_dir / "demucs", model=model)
    except Exception as e:  # noqa: BLE001
        log.warning("Tach vocal that bai (%s) — fallback gate thoai", e)
        return None

    run([
        "ffmpeg", "-y", "-i", str(no_voc),
        "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
        str(cached),
    ])
    return cached
