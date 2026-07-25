"""Buoc 3: Long tieng Viet bang edge-tts."""
from __future__ import annotations
import asyncio
import re
import time
import unicodedata
from pathlib import Path
from typing import TypedDict

from .utils import log, run, probe_duration

_TICKS_PER_SEC = 10_000_000
_TTS_RETRIES = 6
_TTS_RETRY_DELAY = 2.5
_TTS_SEGMENT_GAP = 1.0


class WordTiming(TypedDict):
    text: str
    start: float
    end: float


def _sanitize_tts_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _tts_kwargs(cfg: dict, *, boundary: str | None, rate: str | None = None, pitch: str | None = None) -> dict:
    kw = {
        "voice": cfg.get("voice", "vi-VN-NamMinhNeural"),
        "rate": rate if rate is not None else cfg.get("rate", "+10%"),
        "volume": cfg.get("volume", "+0%"),
        "pitch": pitch if pitch is not None else cfg.get("pitch", "+0Hz"),
    }
    if boundary:
        kw["boundary"] = boundary
    return kw


def _make_silence_mp3(out_path: Path, duration: float = 0.5) -> Path:
    dur = max(0.12, duration)
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r=24000:cl=mono:d={dur:.3f}",
        "-c:a", "libmp3lame", "-b:a", "64k", str(out_path),
    ])
    return out_path


async def _synthesize_stream(
    text: str, out_path: Path, cfg: dict, boundary: str | None,
) -> list[WordTiming]:
    import edge_tts

    words: list[WordTiming] = []
    communicate = edge_tts.Communicate(text, **_tts_kwargs(cfg, boundary=boundary))
    with open(out_path, "wb") as f:
        got_audio = False
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
                got_audio = True
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / _TICKS_PER_SEC
                end = (chunk["offset"] + chunk["duration"]) / _TICKS_PER_SEC
                words.append({"text": chunk["text"], "start": start, "end": end})
    if not got_audio or out_path.stat().st_size < 100:
        raise RuntimeError("edge-tts khong tra audio (stream)")
    return words


async def _synthesize_save(text: str, out_path: Path, cfg: dict) -> list[WordTiming]:
    import edge_tts

    communicate = edge_tts.Communicate(text, **_tts_kwargs(cfg, boundary=None))
    await communicate.save(str(out_path))
    if not out_path.exists() or out_path.stat().st_size < 100:
        raise RuntimeError("edge-tts khong tra audio (save)")
    return []


async def _synthesize_split(text: str, out_path: Path, cfg: dict) -> list[WordTiming]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?。！？])\s+", text) if p.strip()]
    if len(parts) <= 1 and len(text) > 100:
        parts = [text[i:i + 100].strip() for i in range(0, len(text), 100)]
        parts = [p for p in parts if p]
    if len(parts) <= 1:
        return await _synthesize_save(text, out_path, cfg)

    tmp_dir = out_path.parent / "_tts_parts"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    part_files: list[Path] = []
    for i, part in enumerate(parts):
        part_path = tmp_dir / f"part_{i:03d}.mp3"
        await _synthesize_save(part, part_path, cfg)
        part_files.append(part_path)
        await asyncio.sleep(0.6)

    cmd = ["ffmpeg", "-y"]
    for pf in part_files:
        cmd += ["-i", str(pf)]
    n = len(part_files)
    cmd += [
        "-filter_complex", f"{''.join(f'[{i}:a]' for i in range(n))}concat=n={n}:v=0:a=1[aout]",
        "-map", "[aout]", "-c:a", "libmp3lame", "-b:a", "192k", str(out_path),
    ]
    run(cmd)
    return []


def synthesize(script: str, out_path: str | Path, cfg: dict) -> tuple[Path, list[WordTiming]]:
    """Tao file audio .mp3 tu text + timing tung tu (mode lien tuc)."""
    try:
        import edge_tts  # noqa: F401
    except ImportError as e:
        raise RuntimeError("Chua cai edge-tts. Chay: pip install edge-tts") from e

    out_path = Path(out_path)
    text = _sanitize_tts_text(script)
    if not text:
        raise RuntimeError("Script trong, khong the long tieng")

    async def _run() -> list[WordTiming]:
        strategies = [
            ("stream+timing", lambda: _synthesize_stream(text, out_path, cfg, "WordBoundary")),
            ("save", lambda: _synthesize_save(text, out_path, cfg)),
            ("save-plain", lambda: _synthesize_save(
                text, out_path, {**cfg, "rate": "+0%", "pitch": "+0Hz"},
            )),
            ("split+save", lambda: _synthesize_split(text, out_path, cfg)),
        ]
        last_err: Exception | None = None
        for attempt in range(_TTS_RETRIES):
            name, fn = strategies[min(attempt, len(strategies) - 1)]
            try:
                return await fn()
            except Exception as e:  # noqa: BLE001
                last_err = e
                if out_path.exists():
                    out_path.unlink(missing_ok=True)
                if attempt + 1 < _TTS_RETRIES:
                    wait = _TTS_RETRY_DELAY * (attempt + 1)
                    log.warning(
                        "TTS loi (lan %d/%d, %s), thu lai sau %.0fs: %s",
                        attempt + 1, _TTS_RETRIES, name, wait, e,
                    )
                    await asyncio.sleep(wait)
        raise RuntimeError(f"TTS that bai sau {_TTS_RETRIES} lan: {last_err}") from last_err

    words = asyncio.run(_run())
    log.info("Long tieng xong: %s (%d tu)", out_path.name, len(words))
    return out_path, words


def synthesize_preview(lang: str, voice_id: str, out_path: str | Path) -> Path:
    """Tao file MP3 nghe thu ngan (dung cho UI). Cache neu da co."""
    from .voices import preview_sentence, resolve_preset

    out_path = Path(out_path)
    if out_path.exists() and out_path.stat().st_size > 500:
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    preset = resolve_preset(lang, voice_id)
    cfg = {
        "voice": preset["voice"],
        "rate": preset.get("rate", "+0%"),
        "pitch": preset.get("pitch", "+0Hz"),
        "volume": preset.get("volume", "+0%"),
    }
    text = preview_sentence(lang)
    synthesize(text, out_path, cfg)
    return out_path


def _atempo_chain(speed: float) -> str:
    chain, s = [], speed
    while s > 2.0:
        chain.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        chain.append("atempo=0.5")
        s *= 2.0
    chain.append(f"atempo={s:.4f}")
    return ",".join(chain)


def _fit_to_slot_filter(input_label: str, tts_dur: float, slot: float, out_label: str) -> str:
    """Chi tang toc khi TTS dai hon slot; neu ngan hon thi giu toc do + pad im lang."""
    slot = max(0.12, slot)
    if tts_dur < 0.05:
        return f"{input_label}anull[{out_label}]"
    if tts_dur > slot * 1.08:
        ratio = min(tts_dur / slot, 2.5)
        chain = _atempo_chain(ratio)
        return (
            f"{input_label}{chain},atrim=0:{slot:.3f},asetpts=PTS-STARTPTS,"
            f"apad=pad_dur={slot:.3f}[{out_label}]"
        )
    # TTS ngan hon slot: KHONG lam cham (tranh tieng re/cham nhu 0.5x)
    return (
        f"{input_label}atrim=0:{min(tts_dur, slot):.3f},asetpts=PTS-STARTPTS,"
        f"apad=pad_dur={slot:.3f}[{out_label}]"
    )


def synthesize_segments(
    segments: list[dict],
    work_dir: str | Path,
    out_path: str | Path,
    cfg: dict,
    video_dur: float | None = None,
) -> Path:
    """Long tieng tung doan, dat dung thoi diem + khop do dai noi goc."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(out_path)

    if not segments:
        raise RuntimeError("Khong co doan thoai de long tieng")

    total_dur = max(max(s["end"] for s in segments) + 0.5, video_dur or 0.0)
    refresh = bool(cfg.get("refresh_tts", True))
    skip_failed = bool(cfg.get("skip_failed_segments", False))
    segment_gap = float(cfg.get("tts_segment_gap", _TTS_SEGMENT_GAP))
    clips: list[tuple[float, float, Path]] = []

    for i, seg in enumerate(segments):
        text = _sanitize_tts_text(seg.get("text", ""))
        if not text:
            continue
        clip = work_dir / f"seg_{i:04d}.mp3"
        if refresh or not (clip.exists() and clip.stat().st_size > 100):
            try:
                synthesize(text, clip, cfg)
            except RuntimeError as e:
                if skip_failed:
                    slot = max(0.12, float(seg["end"]) - float(seg["start"]))
                    log.warning("Bo qua doan %d (TTS loi), dung im lang %.1fs: %s", i, slot, e)
                    _make_silence_mp3(clip, slot)
                else:
                    raise
            time.sleep(segment_gap)
        else:
            log.info("Bo qua (da co): %s", clip.name)
        clips.append((seg["start"], seg["end"], clip))

    if not clips:
        raise RuntimeError("Khong co doan thoai hop le de long tieng")

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={total_dur:.3f}"]
    for _, _, clip in clips:
        cmd += ["-i", str(clip)]

    filters = []
    mix_inputs = ["[0:a]"]
    for i, (start, end, clip_path) in enumerate(clips):
        slot = max(0.12, end - start)
        tts_dur = probe_duration(clip_path)
        label = f"a{i}"
        fit = _fit_to_slot_filter(f"[{i + 1}:a]", tts_dur, slot, f"f{i}")
        delay_ms = int(start * 1000)
        filters.append(
            f"{fit};[f{i}]volume=2.0,adelay={delay_ms}|{delay_ms}[{label}]"
        )
        mix_inputs.append(f"[{label}]")

    n = len(mix_inputs)
    filters.append(
        f"{''.join(mix_inputs)}amix=inputs={n}:duration=longest:dropout_transition=0[aout]"
    )

    cmd += ["-filter_complex", ";".join(filters), "-map", "[aout]",
            "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)]
    run(cmd)
    log.info(
        "Long tieng dub (fit slot): %s (%d doan, %.1fs)",
        out_path.name, len(clips), total_dur,
    )
    return out_path


def auto_script(video_path: str | Path, work_dir: str | Path, cfg_auto: dict):
    from . import transcribe
    return transcribe.auto_script(video_path, work_dir, cfg_auto)
