"""Buoc 3: Long tieng bang edge-tts (theo doan, khop nhip goc)."""
from __future__ import annotations
import asyncio
import json
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypedDict

from .dub_constants import (
    DUB_GROUP_MAX_CHARS,
    DUB_GROUP_MAX_DUR_SEC,
    DUB_GROUP_MAX_GAP_SEC,
    DUB_MICRO_OVERLAP_SEC,
    DUB_MIN_SHORTEN_CHARS,
    DUB_OVERFLOW_MAX_MERGES,
    DUB_OVERFLOW_SEMANTIC_MAX_TRIES,
    DUB_SHORTEN_MAX_RETRIES,
    LOUDNORM_TARGET_I,
    SEGMENT_CROSSFADE_MS,
    TARGET_CHARS_PER_SEC,
    TTS_ATEMPO_FLAG_MIN,
    TTS_ATEMPO_MAX,
    TTS_ATEMPO_MIN,
    TTS_ATEMPO_SAFE_MAX,
    TTS_ATEMPO_SEVERE_MAX,
    TTS_EDGE_RATE_MAX_PCT,
)
from .dub_report import write_dub_final_report, write_shorten_flags, sync_spoken_text_to_segment_files
from .dub_timing import (
    decide_fit,
    is_audio_truncated,
    overflow_atempo_plan,
    overflow_semantic_try_budget,
    plan_placement,
    semantic_shorten_budget,
    shorten_attempt_closes_enough,
)
from .dub_tts import (
    apply_atempo_file,
    clamp_openai_api_speed,
    close_file_to_slot,
    drift_ratio,
    loudnorm_twopass,
    mild_atempo_for_residual,
    resolve_openai_speed,
    tts_rate_converge,
)
from .utils import log, run, probe_duration as _probe_duration_raw

_TICKS_PER_SEC = 10_000_000
_TTS_RETRIES = 6
_TTS_RETRY_DELAY = 3.5
_TTS_SEGMENT_GAP = 1.0
_TTS_PREFETCH_CONCURRENCY = 4
_TTS_CALL_TIMEOUT_SEC = 40
_openai_chars_lock = threading.Lock()


def probe_duration(path: str | Path) -> float:
    t0 = time.perf_counter()
    try:
        return _probe_duration_raw(path)
    finally:
        from .job_timing import add_bucket
        add_bucket("ffprobe", time.perf_counter() - t0, log_line=False)


async def _await_tts(coro, *, label: str):
    try:
        return await asyncio.wait_for(coro, timeout=_TTS_CALL_TIMEOUT_SEC)
    except asyncio.TimeoutError as e:
        raise RuntimeError(f"TTS het han {_TTS_CALL_TIMEOUT_SEC}s ({label})") from e


class WordTiming(TypedDict):
    text: str
    start: float
    end: float


def _sanitize_tts_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Edge doi khi fail voi ky tu dieu khien / zero-width
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _tts_kwargs(cfg: dict, *, boundary: str | None, rate: str | None = None, pitch: str | None = None) -> dict:
    from .prosody import sanitize_edge_cfg

    safe = sanitize_edge_cfg(cfg)
    kw = {
        "voice": safe.get("voice", "vi-VN-NamMinhNeural"),
        "rate": rate if rate is not None else safe.get("rate", "+0%"),
        "volume": safe.get("volume", "+0%"),
        "pitch": pitch if pitch is not None else safe.get("pitch", "+0Hz"),
    }
    # sanitize overrides
    kw = sanitize_edge_cfg(kw)
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

    communicate = edge_tts.Communicate(text, **_tts_kwargs(cfg, boundary=boundary))

    async def _collect() -> list[WordTiming]:
        out: list[WordTiming] = []
        with open(out_path, "wb") as f:
            got_audio = False
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                    got_audio = True
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / _TICKS_PER_SEC
                    end = (chunk["offset"] + chunk["duration"]) / _TICKS_PER_SEC
                    out.append({"text": chunk["text"], "start": start, "end": end})
        if not got_audio or out_path.stat().st_size < 100:
            raise RuntimeError("edge-tts khong tra audio (stream)")
        return out

    return await _await_tts(_collect(), label="stream")


async def _synthesize_save(text: str, out_path: Path, cfg: dict) -> list[WordTiming]:
    import edge_tts

    communicate = edge_tts.Communicate(text, **_tts_kwargs(cfg, boundary=None))
    await _await_tts(communicate.save(str(out_path)), label="save")
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
        from .job_timing import timed_async_sleep
        await timed_async_sleep(0.6, "tts_split_gap")

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


def _tts_engine(cfg: dict) -> str:
    return str(cfg.get("tts_engine") or os.getenv("DYFLOW_TTS_ENGINE") or "edge").strip().lower()


def _is_openai_engine(cfg: dict) -> bool:
    return _tts_engine(cfg) in ("openai", "tts-1", "tts-1-hd", "gpt-4o-mini-tts", "openai_tts")


_EMO_INSTRUCTIONS = {
    "excited": (
        "Speak with lively energy and warmth, like a character in a short video. "
        "Natural talking pace — do not rush, shout, or stretch words."
    ),
    "urgent": (
        "Speak with urgency and concern, still clear and human. "
        "Do not double-speed or sound frantic."
    ),
    "emphatic": (
        "Speak firmly, stressing key words. Conversational pace, not faster."
    ),
    "soft": "Speak softly and warmly, unhurried, with care.",
    "calm": "Speak calmly and steadily, measured and natural.",
    "neutral": (
        "Speak naturally and conversationally, with clear feeling matching the line. "
        "Even human rhythm — never double-speed or half-speed."
    ),
}


def _tts_emotion_instructions(seg: dict) -> str:
    emo = str(seg.get("emotion") or "neutral").lower()
    line = _EMO_INSTRUCTIONS.get(emo, _EMO_INSTRUCTIONS["neutral"])
    who = " ".join(
        x for x in (
            str(seg.get("speaker_role") or "").strip(),
            str(seg.get("age") or "").strip(),
            str(seg.get("gender") or "").strip(),
        ) if x
    )
    if who:
        line = f"You are voicing a {who} in a dubbed short video. " + line
    return line


def _seg_tts_cfg(base: dict, seg: dict, *, rate: str | None = None) -> dict:
    """Cfg 1 doan: OpenAI giu nhip cam xuc (~1.0x), khong clamp Edge +12% de tua slot."""
    from . import prosody
    from .dub_tts import rate_to_openai_speed

    out = {
        **base,
        "voice": seg.get("tts_voice") or base.get("voice"),
        "openai_voice": seg.get("openai_voice") or base.get("openai_voice"),
        "rate": rate if rate is not None else (seg.get("tts_rate") or base.get("rate", "+0%")),
        "pitch": seg.get("tts_pitch") or base.get("pitch", "+0Hz"),
        "volume": seg.get("tts_volume") or base.get("volume", "+0%"),
    }
    if _is_openai_engine(out):
        saved = out["rate"]
        sanitized = prosody.sanitize_edge_cfg({**out, "rate": "+0%"})
        sanitized["rate"] = saved
        sanitized["openai_speed"] = rate_to_openai_speed(saved, openai_fit=True)
        sanitized["tts_instructions"] = _tts_emotion_instructions(seg)
        return sanitized
    return prosody.sanitize_edge_cfg(out, rate_hi=TTS_EDGE_RATE_MAX_PCT)


def _openai_voice_id(cfg: dict) -> str:
    explicit = str(cfg.get("openai_voice") or "").strip()
    if explicit:
        return explicit
    raw = str(cfg.get("voice") or "").lower()
    female = (
        "female", "hoaimy", "jenny", "nanami", "francisca", "swara",
        "katja", "gadis", "blessica", "nova", "shimmer", "coral", "alloy",
    )
    if any(x in raw for x in female):
        return str(cfg.get("openai_voice_female") or "nova")
    return str(cfg.get("openai_voice_male") or "onyx")


def _synthesize_openai(text: str, out_path: Path, cfg: dict) -> None:
    from .user_openai import load_tts_key

    key = load_tts_key()
    if not key:
        raise RuntimeError(
            "Chua co key OpenAI TTS. Dan key sk-... cua ban trong app "
            "(khong dung key cua chu app)."
        )
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Chua cai openai. pip install openai") from e

    model = str(cfg.get("openai_model") or "tts-1")
    voice = _openai_voice_id(cfg)
    speed = clamp_openai_api_speed(resolve_openai_speed(cfg))
    client = OpenAI(api_key=key, timeout=45.0, max_retries=1)
    kwargs: dict = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": "mp3",
        "speed": speed,
    }
    instr = str(cfg.get("tts_instructions") or "").strip()
    if instr and "gpt-4o" in model.lower():
        kwargs["instructions"] = instr
    try:
        r = client.audio.speech.create(**kwargs)
    except Exception as e:
        if "instructions" not in kwargs:
            raise
        log.warning("OpenAI TTS instructions bo qua: %s", e)
        kwargs.pop("instructions", None)
        r = client.audio.speech.create(**kwargs)
    data = r.content if hasattr(r, "content") else bytes(r)
    out_path.write_bytes(data)
    if out_path.stat().st_size < 100:
        raise RuntimeError("OpenAI TTS khong tra audio")
    nchars = len(text)
    log.info(
        "OpenAI TTS %s voice=%s speed=%.2f chars=%d -> %s",
        model, voice, speed, nchars, out_path.name,
    )
    acc = Path(out_path).parent.parent / "openai_tts_chars.txt"
    try:
        with _openai_chars_lock:
            prev = int(acc.read_text(encoding="utf-8").strip()) if acc.exists() else 0
            acc.write_text(str(prev + nchars), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def synthesize(script: str, out_path: str | Path, cfg: dict) -> tuple[Path, list[WordTiming]]:
    """Tao file audio .mp3 tu text (OpenAI tts-1, fallback Edge)."""
    from .prosody import sanitize_edge_cfg

    out_path = Path(out_path)
    text = _sanitize_tts_text(script)
    if not text:
        raise RuntimeError("Script trong, khong the long tieng")

    is_oa = _tts_engine(cfg) in ("openai", "tts-1", "tts-1-hd", "gpt-4o-mini-tts", "openai_tts")
    if is_oa:
        saved_rate = cfg.get("rate")
        saved_speed = cfg.get("openai_speed")
        cfg = sanitize_edge_cfg(cfg)
        if saved_rate is not None:
            cfg["rate"] = saved_rate
        if saved_speed is not None:
            cfg["openai_speed"] = saved_speed
    else:
        cfg = sanitize_edge_cfg(cfg, rate_hi=TTS_EDGE_RATE_MAX_PCT)

    if is_oa:
        try:
            _synthesize_openai(text, out_path, cfg)
            return out_path, []
        except Exception as e:  # noqa: BLE001
            log.warning("OpenAI TTS loi, fallback Edge: %s", e)
            cfg = sanitize_edge_cfg(cfg)

    try:
        import edge_tts  # noqa: F401
    except ImportError as e:
        raise RuntimeError("Chua cai edge-tts. Chay: pip install edge-tts") from e

    async def _run() -> list[WordTiming]:
        plain = {**cfg, "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"}
        strategies = [
            ("stream+timing", lambda: _synthesize_stream(text, out_path, cfg, "WordBoundary")),
            ("save", lambda: _synthesize_save(text, out_path, cfg)),
            ("save-plain", lambda: _synthesize_save(text, out_path, plain)),
            ("split+save", lambda: _synthesize_split(text, out_path, plain)),
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
                    from .job_timing import timed_async_sleep
                    await timed_async_sleep(wait, "tts_retry_backoff")
        raise RuntimeError(f"TTS that bai sau {_TTS_RETRIES} lan: {last_err}") from last_err

    words = asyncio.run(_run())
    log.info("Long tieng xong: %s (%d tu)", out_path.name, len(words))
    return out_path, words


def _prefetch_tts_clips(
    segs: list[dict],
    work_dir: Path,
    cfg: dict,
    *,
    refresh: bool,
) -> None:
    """Goi TTS lan dau song song (4 luong). Overflow/rut gon van tuan tu sau do."""
    cjk_re = re.compile(r"[\u4e00-\u9fff]")
    n = max(1, int(cfg.get("tts_prefetch_concurrency") or _TTS_PREFETCH_CONCURRENCY))
    n = max(1, min(n, 5))
    jobs: list[tuple[int, str, Path, dict]] = []
    for i, seg in enumerate(segs):
        text = _sanitize_tts_text(seg.get("text", ""))
        if not text or cjk_re.search(text):
            continue
        clip = work_dir / f"seg_{i:04d}.mp3"
        if not refresh and clip.exists() and clip.stat().st_size > 100:
            continue
        jobs.append((i, text, clip, _seg_tts_cfg(cfg, seg)))
    if not jobs:
        return
    if sys.platform == "win32":
        log.info("Windows: bo TTS prefetch song song (edge-tts de treo)")
        return
    log.info("TTS prefetch %d doan, concurrency=%d", len(jobs), n)

    def _one(item: tuple[int, str, Path, dict]) -> None:
        i, text, clip, seg_cfg = item
        from .job_timing import timed_sleep
        timed_sleep(0.12 * (i % n), "tts_prefetch_stagger")
        try:
            if clip.exists():
                clip.unlink(missing_ok=True)
            synthesize(text, clip, seg_cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("TTS prefetch doan %d loi: %s", i, e)

    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(_one, job) for job in jobs]
        for fut in as_completed(futs):
            fut.result()


def synthesize_preview(
    lang: str,
    voice_id: str,
    out_path: str | Path,
    *,
    tts_engine: str = "openai",
    openai_model: str = "tts-1",
) -> Path:
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
        "tts_engine": tts_engine or "openai",
        "openai_model": openai_model or "tts-1",
        "openai_voice": preset.get("openai_voice") or "",
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


def _fit_to_slot_filter(
    input_label: str,
    tts_dur: float,
    slot: float,
    out_label: str,
    *,
    max_stretch: float = 1.12,
    min_stretch: float = 0.92,
    natural: bool = True,
) -> str:
    """Dat audio vao timeline.

    natural=True: giu nguyen file TTS, khong atrim/tua. Tran xu ly o voiceover
    (semantic / micro-overlap / fail-closed), khong cat o mix.
    natural=False: keo/tua de lap day slot (de gay giong robot).
    """
    slot = max(0.12, float(slot))
    if tts_dur < 0.05:
        return f"{input_label}anull[{out_label}]"

    if natural:
        # Dat du file TTS. Khong atrim — cat vat ly an thau QA (khong_tran).
        return f"{input_label}asetpts=PTS-STARTPTS[{out_label}]"

    fill_target = slot * 0.92
    floor = max(0.35, min(float(min_stretch), 0.95))
    if tts_dur < fill_target * 0.88:
        slow = max(floor, min(1.0, tts_dur / fill_target))
        if slow < 0.995:
            chain = _atempo_chain(slow)
            stretched = tts_dur / slow
            pad_to = max(stretched, min(slot, fill_target))
            return (
                f"{input_label}{chain},asetpts=PTS-STARTPTS,"
                f"apad=whole_dur={pad_to:.3f}[{out_label}]"
            )
    if tts_dur <= slot * 1.02:
        return (
            f"{input_label}asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={slot:.3f}[{out_label}]"
        )
    cap = max(1.01, min(float(max_stretch), 1.15))
    ratio = max(1.01, min(tts_dur / slot, cap))
    chain = _atempo_chain(ratio)
    return (
        f"{input_label}{chain},asetpts=PTS-STARTPTS,"
        f"apad=whole_dur={slot:.3f}[{out_label}]"
    )


def _effective_play_dur(
    tts_dur: float,
    slot: float,
    *,
    max_stretch: float = 1.08,
    natural: bool = True,
) -> tuple[float, float]:
    """Tinh thoi luong phat va he so toc."""
    slot = max(0.12, float(slot))
    if natural:
        return max(0.0, float(tts_dur)), 1.0
    fill_target = slot * 0.92
    floor = 0.35
    if tts_dur < fill_target * 0.88:
        slow = max(floor, min(1.0, tts_dur / fill_target))
        play = min(slot, tts_dur / slow)
        return play, slow
    if tts_dur <= slot * 1.02:
        return min(slot, max(tts_dur, fill_target * 0.5)), 1.0
    play = min(slot, tts_dur)
    return play, tts_dur / max(play, 0.12)


def _edge_match_slot_rate(
    i: int,
    *,
    seg_cfg: dict,
    clip: Path,
    tts_dur: float,
    max_avail: float,
    _tts_once,
    prosody,
    segment_gap: float,
    max_iters: int = 3,
) -> tuple[dict, float]:
    """Edge TTS: lap rate de do dai gan slot (doc cham/nhanh, KHONG ffmpeg robot)."""
    target = max(max_avail * 0.88, 0.15)
    cur_dur = float(tts_dur)
    for attempt in range(max_iters):
        if target * 0.92 <= cur_dur <= max_avail * 1.02:
            break
        cur = prosody._parse_signed(str(seg_cfg.get("rate", "+0%")))
        if cur_dur > max_avail * 1.03:
            factor = cur_dur / max(max_avail * 0.98, 0.12)
            need_rate = (1.0 + cur / 100.0) * factor * 100.0 - 100.0
            need_rate = max(cur + 2, min(22.0, need_rate))
            if need_rate <= cur + 1.5:
                break
            label = "nhanh"
        elif cur_dur < target * 0.92:
            factor = cur_dur / target
            need_rate = (1.0 + cur / 100.0) * factor * 100.0 - 100.0
            need_rate = min(cur - 2, max(-15.0, need_rate))
            if need_rate >= cur - 0.5:
                break
            label = "cham"
        else:
            break
        newer = prosody.sanitize_edge_cfg(
            {**seg_cfg, "rate": f"{need_rate:+.0f}%"}, rate_hi=22,
        )
        log.info(
            "Doan %d Edge fit (%s) %.2fs / slot %.2fs — rate %s -> %s",
            i, label, cur_dur, max_avail, seg_cfg.get("rate"), newer.get("rate"),
        )
        if not _tts_once(newer):
            break
        seg_cfg = newer
        cur_dur = probe_duration(clip)
        from .job_timing import timed_sleep
        timed_sleep(min(segment_gap, 0.5), "tts_rate_fit_gap")
    return seg_cfg, cur_dur


def _normalize_tts_clip(path: Path) -> None:
    """Can am luong tung doan (tranh dau nho, sau to) + cat im lang dau."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < 100:
        return
    tmp = path.with_name(path.stem + "._ln.mp3")
    try:
        run([
            "ffmpeg", "-y", "-i", str(path),
            "-af",
            "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB:detection=peak,"
            "loudnorm=I=-16:TP=-1.5:LRA=8:linear=true",
            "-ar", "24000", "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(tmp),
        ])
        if tmp.exists() and tmp.stat().st_size > 100:
            tmp.replace(path)
    except Exception as e:  # noqa: BLE001
        log.warning("Chuan hoa am luong %s loi: %s", path.name, e)
        tmp.unlink(missing_ok=True)


def _mean_volume_db(path: Path) -> float | None:
    import subprocess
    try:
        p = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, check=False,
        )
        m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", (p.stderr or "") + (p.stdout or ""))
        return float(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _apply_safe_atempo(
    *,
    i: int,
    clip: Path,
    tts_dur: float,
    tts_dur_raw: float,
    max_avail: float,
    orig_slot: float,
    start: float,
    next_start: float,
    dub_natural: bool,
) -> tuple[float, float, dict]:
    """Tua <= 1.15x neu TTS de len cau sau. Khong cat audio."""
    atempo_applied = 1.0
    fit = decide_fit(
        tts_dur=tts_dur,
        hard_limit=max_avail,
        speech_slot=orig_slot,
        source_start=start,
        next_start=next_start,
    )
    actual_ov = _file_overlaps_next(start, next_start, tts_dur)
    needed = float(tts_dur) / max(float(max_avail), 0.12)
    if not dub_natural:
        atempo_applied = mild_atempo_for_residual(
            tts_dur, max_avail,
            skip=False,
            force=True,
        )
    elif actual_ov and needed > 1.0:
        atempo_applied = min(needed, TTS_ATEMPO_SAFE_MAX)
    elif fit["action"] == "atempo" or (
        fit["action"] == "overflow" and fit["tempo"] > 1.0
    ):
        atempo_applied = fit["tempo"]
    if atempo_applied != 1.0:
        apply_atempo_file(clip, atempo_applied)
        tts_dur = probe_duration(clip)
        log.info(
            "Doan %d atempo nhe %.3fx (%.2fs -> %.2fs / hard %.2fs)",
            i, atempo_applied, tts_dur_raw, tts_dur, max_avail,
        )
        fit = decide_fit(
            tts_dur=tts_dur,
            hard_limit=max_avail,
            speech_slot=orig_slot,
            source_start=start,
            next_start=next_start,
        )
    return tts_dur, atempo_applied, fit


def _file_overlaps_next(start: float, next_start: float, tts_dur: float) -> bool:
    return bool(
        plan_placement(
            source_start=start,
            source_end=start,
            next_start=next_start,
            tts_dur=tts_dur,
        )["would_overlap"]
    )


def _force_atempo_to_hard(
    *,
    i: int,
    clip: Path,
    tts_dur: float,
    max_avail: float,
    start: float,
    next_start: float,
    tag: str,
    attempt: int | None = None,
    shorten_effective: bool = True,
) -> tuple[float, float, bool, dict]:
    """Tua file TTS hien tai de vua hard_limit. Tra (tts, tempo, van_de, plan).

    tempo = min(tts/slot, cap). Lan 1-2 cap 1.15. Lan 3 (rut DAT) cap SEVERE_MAX.
    Khong tua hon muc can. Khong cat audio.
    """
    hard = max(0.12, float(max_avail))
    tts = max(0.0, float(tts_dur))
    plan = overflow_atempo_plan(
        tts_dur=tts,
        slot=hard,
        attempt=int(attempt or 1),
        shorten_effective=shorten_effective,
    )
    if not _file_overlaps_next(start, next_start, tts):
        keep = {**plan, "tempo": 1.0, "action": "keep", "fits": True}
        return tts, 1.0, False, keep
    tempo = float(plan["tempo"])
    lan = f" lan {attempt}" if attempt is not None else ""
    apply_atempo_file(clip, tempo)
    new_dur = probe_duration(clip) if clip.exists() else (tts / tempo if tempo else tts)
    still = _file_overlaps_next(start, next_start, new_dur)
    log.info(
        "%s doan %d%s atempo_applied=%.3f needed=%.3f cap=%.3f "
        "(tts %.2fs -> %.2fs / hard %.2fs actual_overlap=%s)",
        tag, i, lan, tempo, plan["needed"], plan["cap"], tts, new_dur, hard, still,
    )
    return new_dur, tempo, still, plan


def synthesize_segments(
    segments: list[dict],
    work_dir: str | Path,
    out_path: str | Path,
    cfg: dict,
    video_dur: float | None = None,
    source_video: str | Path | None = None,
    progress=None,
) -> tuple[Path, list[dict]]:
    """Long tieng tung doan dung nhip goc.

    Tra ve (voice.mp3, segments_da_chuan_hoa) — sub phai dung cung mang timestamp nay.
    """
    from .job_timing import where

    with where("Voiceover"):
        return _synthesize_segments_impl(
            segments, work_dir, out_path, cfg,
            video_dur=video_dur, source_video=source_video, progress=progress,
        )


def _synthesize_segments_impl(
    segments: list[dict],
    work_dir: str | Path,
    out_path: str | Path,
    cfg: dict,
    video_dur: float | None = None,
    source_video: str | Path | None = None,
    progress=None,
) -> tuple[Path, list[dict]]:
    from . import prosody

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(out_path)

    if not segments:
        raise RuntimeError("Khong co doan thoai de long tieng")

    match_prosody = bool(cfg.get("match_original_prosody", True))
    max_stretch = float(cfg.get("max_atempo_stretch", TTS_ATEMPO_MAX))
    max_stretch = max(TTS_ATEMPO_MIN, min(max_stretch, TTS_ATEMPO_MAX))
    is_openai = _is_openai_engine(cfg)
    dub_natural = str(cfg.get("dub_timing", "natural")).lower() not in (
        "fill", "fill_slot", "slot", "stretch",
    )
    job_work = work_dir.parent if work_dir.name == "seg_tts" else work_dir

    vd = float(video_dur or 0.0)
    if not vd and segments:
        try:
            vd = max(float(s.get("end") or 0) for s in segments)
        except (TypeError, ValueError):
            vd = 0.0
    from .dub_merge import group_utterances

    cfg_chars = cfg.get("merge_max_chars")
    if cfg_chars is None:
        cfg_chars = (cfg.get("auto") or {}).get("merge_max_chars")
    if cfg_chars is not None and int(cfg_chars) != int(DUB_GROUP_MAX_CHARS):
        log.info(
            "Bo qua merge_max_chars=%s trong config (so cu cat manh cau) — dung %d",
            cfg_chars, DUB_GROUP_MAX_CHARS,
        )
    n_before = len(segments)
    segments = group_utterances(
        segments,
        max_gap=DUB_GROUP_MAX_GAP_SEC,
        max_dur=DUB_GROUP_MAX_DUR_SEC,
        max_chars=DUB_GROUP_MAX_CHARS,
    )
    if len(segments) != n_before:
        log.info(
            "Gop %d -> %d cau cung nhan vat (phan bo giay trong cu m, khong khoa tung shot Trung)",
            n_before, len(segments),
        )

    def _prog(pct: int, msg: str) -> None:
        if progress is None:
            return
        progress("synthesizing", int(pct), msg)

    _prog(65, "Chuan hoa nhip / cam xuc...")
    from .job_timing import add_bucket, timed_sleep
    t_pr = time.perf_counter()
    # --- 1 luot check: timestamp + cam xuc / rate fit ---
    if match_prosody:
        segs = prosody.analyze_segments(
            segments,
            source_video=source_video,
            work_dir=job_work,
            base_rate=str(cfg.get("rate", "+0%")),
            base_pitch=str(cfg.get("pitch", "+0Hz")),
            base_volume=str(cfg.get("volume", "+0%")),
            video_dur=video_dur,
        )
    else:
        segs = prosody.normalize_segment_timings(segments, video_dur)
    add_bucket("prosody_analyze", time.perf_counter() - t_pr, n=len(segments or []))

    if not segs:
        raise RuntimeError("Sau khi chuan hoa khong con doan thoai hop le")

    try:
        (job_work / "segments_prosody.json").write_text(
            json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass

    total_dur = max(max(float(s["end"]) for s in segs) + 0.5, float(video_dur or 0.0))
    refresh = bool(cfg.get("refresh_tts", True))
    t_pf = time.perf_counter()
    _prefetch_tts_clips(segs, work_dir, cfg, refresh=refresh)
    add_bucket("tts_prefetch", time.perf_counter() - t_pf, n=len(segs))
    skip_failed = bool(cfg.get("skip_failed_segments", False))
    if is_openai:
        skip_failed = False
    segment_gap = float(cfg.get("tts_segment_gap", _TTS_SEGMENT_GAP))

    clips: list[tuple[float, float, Path]] = []
    cue_segs: list[dict] = []
    qa_rows: list[dict] = []
    shorten_flags: list[dict] = []
    semantic_reviews: list[dict] = []
    semantic_by_i: dict[int, dict] = {}
    cjk_re = re.compile(r"[\u4e00-\u9fff]")
    skip_idx: set[int] = set()

    def _live_next_start(idx: int) -> float:
        j = idx + 1
        while j < len(segs) and j in skip_idx:
            j += 1
        if j < len(segs):
            return float(segs[j]["start"])
        return float(video_dur or 0.0) or (float(segs[idx].get("end") or 0) + 1.5)

    prev_sid = None
    n_segs = max(1, len(segs))
    for i, seg in enumerate(segs):
        if i in skip_idx:
            continue
        text = _sanitize_tts_text(seg.get("text", ""))
        if not text:
            continue
        if cjk_re.search(text):
            raise RuntimeError(
                f"Doan {i} van con chu Trung/Han, khong TTS clip loi: {text[:80]}"
            )
        t_seg = time.perf_counter()
        tts_wall = 0.0
        tts_calls = 0
        start = float(seg["start"])
        hard_end = float(seg["end"])
        next_start = _live_next_start(i)
        place0 = plan_placement(
            source_start=start, source_end=hard_end,
            next_start=next_start, tts_dur=0.0,
        )
        max_avail = place0["hard_limit"]
        orig_slot = place0["speech_slot"]
        # Ngan sach rut gon: cua thoai + spill. Mix dat dung source_start.
        fit_slot = max_avail

        clip = work_dir / f"seg_{i:04d}.mp3"
        pct = 66 + int(15 * i / n_segs)
        _prog(pct, f"TTS {i + 1}/{len(segs)}")
        sid = seg.get("speaker_id") or seg.get("voice_id") or ""
        if sid and sid != prev_sid:
            log.info("Giong %s (%s) tu doan %d", sid, seg.get("tts_voice") or cfg.get("voice"), i)
            prev_sid = sid
        seg_cfg = _seg_tts_cfg(cfg, seg)

        auto_shorten = bool(cfg.get("dub_auto_shorten", True))
        chars_per_sec = float(
            cfg.get("target_chars_per_sec")
            or cfg.get("dub_chars_per_sec")
            or (cfg.get("auto") or {}).get("target_chars_per_sec")
            or (cfg.get("auto") or {}).get("dub_chars_per_sec")
            or TARGET_CHARS_PER_SEC
        )
        max_shorten = int(cfg.get("dub_shorten_max_retries", DUB_SHORTEN_MAX_RETRIES))
        shorten_used = 0
        overflow_merges = 0
        overflow_action = "keep"
        overflow_why = "-"

        def _tts_once(c: dict) -> bool:
            nonlocal tts_wall, tts_calls
            tts_calls += 1
            t0 = time.perf_counter()
            try:
                if clip.exists():
                    clip.unlink(missing_ok=True)
                synthesize(text, clip, c)
                return clip.exists() and clip.stat().st_size > 100
            except RuntimeError as e:
                log.warning("TTS doan %d loi: %s", i, e)
                return False
            finally:
                tts_wall += time.perf_counter() - t0

        have_clip = clip.exists() and clip.stat().st_size > 100
        if have_clip:
            log.info("TTS da co (prefetch/cache): %s", clip.name)
        else:
            ok = _tts_once(seg_cfg)
            if not ok:
                plain = prosody.sanitize_edge_cfg({
                    **cfg,
                    "voice": seg.get("tts_voice") or cfg.get("voice"),
                    "openai_voice": seg.get("openai_voice") or cfg.get("openai_voice"),
                    "rate": "+0%", "pitch": "+0Hz", "volume": "+0%",
                })
                ok = _tts_once(plain)
                if not ok:
                    if skip_failed:
                        log.warning("Bo qua doan %d (TTS loi), im lang", i)
                        _make_silence_mp3(clip, min(max_avail, max(0.2, hard_end - start)))
                    else:
                        raise RuntimeError(f"TTS doan {i} that bai")
            timed_sleep(min(segment_gap, 0.4), "tts_after_synth_gap")

        tts_dur_raw = probe_duration(clip) if clip.exists() else max_avail
        tts_dur = tts_dur_raw
        rate_iters = 0
        atempo_applied = 1.0
        atempo_needed_seg = 1.0
        atempo_cap_seg = float(TTS_ATEMPO_SAFE_MAX)
        match_flags: list[str] = []

        for shorten_pass in range(max_shorten + 6):
            tts_dur_raw = probe_duration(clip) if clip.exists() else max_avail
            tts_dur = tts_dur_raw
            rate_iters = 0
            atempo_applied = 1.0
            atempo_needed_seg = 1.0
            atempo_cap_seg = float(TTS_ATEMPO_SAFE_MAX)
            match_flags = []
            needs_shorten = False

            if text and not dub_natural:
                seg_cfg, tts_dur, match_res = tts_rate_converge(
                    i,
                    slot_dur=fit_slot,
                    tts_dur=tts_dur,
                    seg_cfg=seg_cfg,
                    clip=clip,
                    tts_once=_tts_once,
                    prosody=prosody,
                    segment_gap=segment_gap,
                    openai=is_openai,
                )
                rate_iters = match_res.iters
                match_flags = list(match_res.flags)
                needs_shorten = match_res.needs_shorten
            elif text:
                place = plan_placement(
                    source_start=start, source_end=hard_end,
                    next_start=next_start, tts_dur=tts_dur,
                )
                needs_shorten = place["needs_shorten"]
                match_flags = ["qua_dai"] if needs_shorten else []
                if tts_dur < orig_slot * 0.85:
                    match_flags.append("qua_ngan")
                rate_iters = 0

            if needs_shorten and auto_shorten and shorten_pass < max_shorten:
                from .translate_llm import dub_target_lang, hard_shorten_text, shorten_for_slot

                target_dur = max_avail
                shrink = 0.88 * (0.82 ** shorten_pass)
                raw_budget = int(len(text) * target_dur / max(tts_dur, 0.05) * shrink)
                floor = max(DUB_MIN_SHORTEN_CHARS, int(len(text) * 0.55))
                if raw_budget < floor:
                    log.info(
                        "Doan %d giu cau du nghia (%d ky tu, ngan sach %d) — noi vao khoang lang",
                        i, len(text), raw_budget,
                    )
                    shorter = None
                else:
                    max_chars = max(floor, raw_budget)
                    from .translate_llm import dub_target_lang
                    shorter = shorten_for_slot(
                        text,
                        slot_sec=max_avail,
                        source_text=str(seg.get("source_text") or ""),
                        target=dub_target_lang(cfg),
                        chars_per_sec=chars_per_sec,
                        max_chars=max_chars,
                        i=i,
                    )
                    if not shorter or shorter == text:
                        hard = hard_shorten_text(text, max_chars)
                        if hard and hard != text and len(hard) >= DUB_MIN_SHORTEN_CHARS:
                            shorter = hard
                            log.info(
                                "Rut gon cuc bo #%d: %d->%d chars (slot %.1fs)",
                                i, len(text), len(hard), fit_slot,
                            )
                if shorter and shorter != text:
                    text = _sanitize_tts_text(shorter)
                    shorten_used += 1
                    log.info(
                        "Rut gon loi #%d: slot goc %.1fs | %s",
                        i, orig_slot, text[:60],
                    )
                    seg_cfg = _seg_tts_cfg(cfg, seg)
                    if _tts_once(seg_cfg):
                        timed_sleep(min(segment_gap, 0.5), "tts_after_shorten_gap")
                        continue

            t_at = time.perf_counter()
            tts_dur, atempo_applied, fit = _apply_safe_atempo(
                i=i,
                clip=clip,
                tts_dur=tts_dur,
                tts_dur_raw=tts_dur_raw,
                max_avail=max_avail,
                orig_slot=orig_slot,
                start=start,
                next_start=next_start,
                dub_natural=dub_natural,
            )
            add_bucket("atempo", time.perf_counter() - t_at, log_line=False)
            overflow_action = fit["action"]
            max_avail = fit["hard_limit"]
            atempo_needed_seg = round(
                float(tts_dur_raw) / max(float(max_avail), 0.12), 3,
            )
            atempo_cap_seg = float(TTS_ATEMPO_SAFE_MAX)
            # would_overlap cua decide_fit tinh tren fitted (tua them gia dinh),
            # khong phai duration file that. i=11/18/23: action=atempo +
            # fit.would_overlap=False roi fail QA, khong bao gio toi semantic.
            actual_place = plan_placement(
                source_start=start, source_end=hard_end,
                next_start=next_start, tts_dur=tts_dur,
            )
            actual_overlap = bool(actual_place["would_overlap"])
            log.info(
                "ATEMPO doan %d: fit.action=%s fit.would_overlap=%s "
                "actual_overlap=%s tts=%.2fs hard=%.2fs atempo=%.3fx "
                "overflow_why=%s",
                i, fit.get("action"), fit.get("would_overlap"), actual_overlap,
                tts_dur, max_avail, atempo_applied, overflow_why,
            )
            if actual_overlap:
                closed = close_file_to_slot(
                    clip, max_avail,
                    start=start, next_start=next_start,
                    atempo_already=max(float(atempo_applied), 1.0),
                    cap=TTS_ATEMPO_SAFE_MAX,
                    allow_speech_trim=False,
                    allow_micro_overlap=False,
                    label=f"doan {i}",
                )
                tts_dur = float(closed["dur"])
                atempo_applied *= float(closed.get("tempo_applied") or 1.0)
                actual_overlap = bool(closed["still_overlap"])
                if closed.get("pad_trim"):
                    log.info(
                        "ATEMPO doan %d: cat pad encoder, tts=%.2fs hard=%.2fs overlap=%s",
                        i, tts_dur, max_avail, actual_overlap,
                    )

            match_flags = [
                f for f in match_flags
                if f not in ("qua_dai", "qua_ngan", "can_rut_gon", "chong_doan", "cat_khop")
            ]

            if not actual_overlap:
                overflow_why = "khong_tran"
                log.info(
                    "ATEMPO doan %d xong: overflow_why=%s (file khong de cau sau)",
                    i, overflow_why,
                )
                if fit["action"] == "keep_short":
                    match_flags.append("qua_ngan")
                if (
                    atempo_applied - 1.0 >= TTS_ATEMPO_FLAG_MIN
                    and "atempo" not in match_flags
                ):
                    match_flags.append("atempo")
                break

            # File that van de cau sau sau 1.15x — gop, roi semantic. Moi merge_why.
            overflow_why = "atempo_con_tran"
            from .dub_merge import overflow_merge_plan

            nxt_i, merge_why = overflow_merge_plan(
                segs, i,
                skip_idx=skip_idx,
                overflow_merges=overflow_merges,
                max_merges=DUB_OVERFLOW_MAX_MERGES,
            )
            overflow_why = merge_why or overflow_why
            log.info(
                "OVERFLOW doan %d sau atempo: overflow_why=%s nxt=%s "
                "tts=%.2fs hard=%.2fs",
                i, overflow_why, nxt_i, tts_dur, max_avail,
            )
            if nxt_i is not None:
                nxt = segs[nxt_i]
                extra = _sanitize_tts_text(nxt.get("text") or "")
                if extra:
                    text = _sanitize_tts_text(f"{text} {extra}".strip())
                src_a = str(seg.get("source_text") or "")
                src_b = str(nxt.get("source_text") or "")
                if src_b:
                    seg["source_text"] = f"{src_a} {src_b}".strip()
                hard_end = float(nxt["end"])
                overflow_why = "gop"
                skip_idx.add(nxt_i)
                overflow_merges += 1
                next_start = _live_next_start(i)
                place0 = plan_placement(
                    source_start=start, source_end=hard_end,
                    next_start=next_start, tts_dur=tts_dur,
                )
                max_avail = place0["hard_limit"]
                orig_slot = place0["speech_slot"]
                fit_slot = max_avail
                log.warning(
                    "OVERFLOW doan %d: tts=%.2fs hard=%.2fs can=%.2fx — "
                    "gop doan %d (lan %d/%d, overlap truoc %.2fs)",
                    i, tts_dur, fit["hard_limit"], fit["required_tempo"],
                    nxt_i, overflow_merges, DUB_OVERFLOW_MAX_MERGES,
                    fit["overlap_sec"],
                )
                seg_cfg = _seg_tts_cfg(cfg, seg)
                if _tts_once(seg_cfg):
                    timed_sleep(min(segment_gap, 0.5), "tts_after_merge_gap")
                continue

            why_txt = {
                "cuoi_clip": "cuoi clip, khong co cau sau de gop",
                "khac_nhan_vat": "cau sau khac nhan vat, khong gop",
                "max_gop": f"da gop {DUB_OVERFLOW_MAX_MERGES} lan, dung day chuyen",
                "atempo_con_tran": "tua toi cap xong file van de cau sau",
                "gop": "con cau sau de gop",
            }.get(merge_why or overflow_why, merge_why or overflow_why)
            log.info(
                "SEMANTIC_SHORTEN doan %d BAT DAU: overflow_why=%s tts=%.2fs hard=%.2fs",
                i, overflow_why, tts_dur, max_avail,
            )

            # Gop het muc: rut gon ngu nghia (2 lan, +1 neu severe >120%). Do TTS that, roi moi fail.
            from .translate_llm import (
                dub_target_lang, locked_cast_payload, shorten_semantic_overflow,
            )

            vi_before = text
            zh_src = str(seg.get("source_text") or "")
            attempts_log: list[dict] = []
            fitted_semantic = False
            best_text = text
            best_dur = tts_dur
            best_raw = clip.read_bytes() if clip.exists() else b""
            best_atempo = atempo_applied

            def _nb(step: int) -> str:
                j = i + step
                while 0 <= j < len(segs) and j in skip_idx:
                    j += step
                if not (0 <= j < len(segs)):
                    return ""
                return str(segs[j].get("source_text") or segs[j].get("text") or "")

            max_tries = DUB_OVERFLOW_SEMANTIC_MAX_TRIES
            attempt = 0
            last_ineffective = False
            ineffective_hint = ""
            cast_lock = locked_cast_payload(segs)

            while attempt < max_tries:
                attempt += 1
                aggressive = attempt >= 3 or last_ineffective
                bud = semantic_shorten_budget(
                    text=text,
                    tts_dur=tts_dur,
                    available_slot=max_avail,
                    chars_per_sec=chars_per_sec,
                    attempt=attempt,
                )
                rec = {
                    "attempt": attempt,
                    "target_chars": bud["target_chars"],
                    "target_dur": bud["target_dur"],
                    "deficit_sec": bud["deficit_sec"],
                    "measured_cps": bud["measured_cps"],
                    "text_before": text,
                    "text_after": "",
                    "tts_before": round(tts_dur, 3),
                    "tts_after": None,
                    "aggressive": aggressive,
                }
                shorter = shorten_semantic_overflow(
                    text,
                    source_text=zh_src,
                    target=dub_target_lang(cfg),
                    target_chars=bud["target_chars"],
                    target_dur=bud["target_dur"],
                    deficit_sec=bud["deficit_sec"],
                    attempt=attempt,
                    chars_per_sec=bud["measured_cps"],
                    prev_context=_nb(-1),
                    next_context=_nb(1),
                    source_lang=str(cfg.get("translate_from") or "zh"),
                    locked_relation=str(seg.get("speaker_relation") or ""),
                    locked_pronoun=str(seg.get("pronoun_pair_used") or ""),
                    locked_cast=cast_lock,
                    speaker_id=str(seg.get("speaker_id") or seg.get("voice_id") or ""),
                    aggressive=aggressive,
                    ineffective_hint=ineffective_hint,
                    i=i,
                )
                if not shorter or shorter == text:
                    rec["error"] = "llm_none"
                    rec["text_after"] = ""
                    log.warning(
                        "SEMANTIC_SHORTEN doan %d lan %d/%d khong nhan ban rut hop le "
                        "(sai ngon ngu / khong ngan hon / JSON None — xem log LLM o tren) | "
                        "target_chars=%d deficit=%.2fs slot=%.2fs tts_truoc=%.2fs | GOC: %s",
                        i, attempt, max_tries,
                        bud["target_chars"], bud["deficit_sec"], max_avail, tts_dur,
                        text,
                    )
                    attempts_log.append(rec)
                    last_ineffective = True
                    ineffective_hint = (
                        "lan truoc LLM tra None/khong rut; lan nay PHAI cat 1 menh de phu"
                    )
                    cov_ratio = tts_dur / max(max_avail, 0.12)
                    max_tries = max(max_tries, overflow_semantic_try_budget(cov_ratio, attempt))
                    continue
                prev_text = text
                prev_bytes = clip.read_bytes() if clip.exists() else b""
                text = _sanitize_tts_text(shorter)
                rec["text_after"] = text
                log.warning(
                    "SEMANTIC_SHORTEN doan %d lan %d/%d | target_chars=%d "
                    "deficit=%.2fs slot=%.2fs tts_truoc=%.2fs aggressive=%s | GOC: %s | RUT: %s",
                    i, attempt, max_tries,
                    bud["target_chars"], bud["deficit_sec"], max_avail, rec["tts_before"],
                    aggressive, rec["text_before"], text,
                )
                seg_cfg = _seg_tts_cfg(cfg, seg)
                if not _tts_once(seg_cfg):
                    rec["error"] = "tts_fail"
                    attempts_log.append(rec)
                    log.warning(
                        "SEMANTIC_SHORTEN doan %d lan %d TTS loi — giu cau truoc | GOC: %s",
                        i, attempt, rec["text_before"],
                    )
                    text = prev_text
                    if prev_bytes:
                        clip.write_bytes(prev_bytes)
                    continue
                timed_sleep(min(segment_gap, 0.5), "tts_after_semantic_gap")
                tts_dur_raw = probe_duration(clip)
                closed = rec["tts_before"] - tts_dur_raw
                if not shorten_attempt_closes_enough(
                    rec["tts_before"], tts_dur_raw, bud["deficit_sec"],
                ):
                    rec["ineffective"] = True
                    rec["error"] = "ineffective_shorten"
                    rec["tts_after"] = None
                    rec["tts_raw_after_shorten"] = round(tts_dur_raw, 3)
                    rec["closed_sec"] = round(closed, 3)
                    log.warning(
                        "SEMANTIC_SHORTEN doan %d lan %d KHONG DAT: tts %.3f->%.3f "
                        "(giam %.3fs) vs deficit %.3fs — bo lan rut hoi hot, thu cat menh de | "
                        "GOC: %s | RUT: %s",
                        i, attempt, rec["tts_before"], tts_dur_raw, closed,
                        bud["deficit_sec"], rec["text_before"], text,
                    )
                    text = prev_text
                    if prev_bytes:
                        clip.write_bytes(prev_bytes)
                    tts_dur = rec["tts_before"]
                    attempts_log.append(rec)
                    last_ineffective = True
                    ineffective_hint = (
                        f"lan truoc chi giam {closed:.3f}s (deficit "
                        f"{bud['deficit_sec']:.3f}s), chi bo vai tu; "
                        "PHAI cat 1 menh de phu khong phai cau hoi/tra loi/so lieu"
                    )
                    cov_ratio = rec["tts_before"] / max(max_avail, 0.12)
                    max_tries = max(max_tries, overflow_semantic_try_budget(cov_ratio, attempt))
                    continue
                last_ineffective = False
                ineffective_hint = ""
                # TTS moi la 1.0x — tua theo tts_dur MOI, khong dung duration truoc rut gon.
                tts_dur, atempo_applied, still_ov, atempo_plan = _force_atempo_to_hard(
                    i=i,
                    clip=clip,
                    tts_dur=tts_dur_raw,
                    max_avail=max_avail,
                    start=start,
                    next_start=next_start,
                    tag="SEMANTIC_SHORTEN",
                    attempt=attempt,
                    shorten_effective=True,
                )
                fit = decide_fit(
                    tts_dur=tts_dur,
                    hard_limit=max_avail,
                    speech_slot=orig_slot,
                    source_start=start,
                    next_start=next_start,
                    atempo_cap=float(atempo_plan["cap"]),
                )
                overflow_action = fit["action"]
                rec["tts_after"] = round(tts_dur, 3)
                rec["tts_raw_after_shorten"] = round(tts_dur_raw, 3)
                rec["atempo"] = round(atempo_applied, 3)
                rec["atempo_applied"] = round(atempo_applied, 3)
                rec["atempo_needed"] = atempo_plan["needed"]
                rec["atempo_cap"] = atempo_plan["cap"]
                rec["overlap_sec"] = round(
                    max(0.0, start + tts_dur - (next_start - 0.02)), 3,
                )
                rec["actual_overlap"] = still_ov
                rec["needed_tempo"] = round(tts_dur_raw / max(max_avail, 0.12), 3)
                rec["closed_sec"] = round(closed, 3)
                attempts_log.append(rec)
                atempo_needed_seg = float(atempo_plan["needed"])
                atempo_cap_seg = float(atempo_plan["cap"])
                log.warning(
                    "SEMANTIC_SHORTEN doan %d lan %d/%d DO | target_chars=%d "
                    "tts_raw %.2fs atempo_applied=%.3f needed=%.3f cap=%.3f "
                    "tts %.2fs slot=%.2fs actual_overlap=%s | GOC: %s | RUT: %s",
                    i, attempt, max_tries,
                    bud["target_chars"], tts_dur_raw, atempo_applied,
                    atempo_plan["needed"], atempo_plan["cap"], tts_dur,
                    max_avail, still_ov,
                    rec["text_before"], text,
                )
                if tts_dur < best_dur - 0.01:
                    best_text = text
                    best_dur = tts_dur
                    best_raw = clip.read_bytes() if clip.exists() else best_raw
                    best_atempo = atempo_applied
                if not still_ov:
                    fitted_semantic = True
                    overflow_action = "semantic_shorten"
                    match_flags.append("shortened_semantic")
                    if (
                        atempo_applied - 1.0 >= TTS_ATEMPO_FLAG_MIN
                        and "atempo" not in match_flags
                    ):
                        match_flags.append("atempo")
                    if atempo_plan.get("severe") and "atempo_severe" not in match_flags:
                        match_flags.append("atempo_severe")
                    break
                cov_ratio = tts_dur_raw / max(max_avail, 0.12)
                max_tries = max(max_tries, overflow_semantic_try_budget(cov_ratio, attempt))
                if attempt < max_tries:
                    log.info(
                        "SEMANTIC_SHORTEN doan %d lan %d chua vua (actual_overlap cov=%.1f%%) "
                        "-> thu lan %d%s",
                        i, attempt, cov_ratio * 100, attempt + 1,
                        " (severe, cat manh hon)" if cov_ratio > 1.20 else "",
                    )

            review = {
                "i": i,
                "source_text": zh_src,
                "text_before": vi_before,
                "text_after": text,
                "ok": fitted_semantic,
                "merge_why": overflow_why,
                "slot": round(max_avail, 3),
                "speech_slot": round(orig_slot, 3),
                "tts_dur": round(tts_dur, 3),
                "atempo_applied": round(atempo_applied, 3),
                "atempo_needed": round(float(atempo_needed_seg), 3),
                "atempo_cap": round(float(atempo_cap_seg), 3),
                "attempts": attempts_log,
            }
            semantic_reviews.append(review)
            semantic_by_i[i] = review

            if fitted_semantic:
                log.info(
                    "SEMANTIC_SHORTEN doan %d OK sau rut gon | slot=%.2fs tts=%.2fs | "
                    "GOC: %s | RUT: %s",
                    i, max_avail, tts_dur, vi_before, text,
                )
                break

            if best_text != vi_before and best_raw:
                text = best_text
                clip.write_bytes(best_raw)
                tts_dur = best_dur
                atempo_applied = best_atempo
                fit = decide_fit(
                    tts_dur=tts_dur,
                    hard_limit=max_avail,
                    speech_slot=orig_slot,
                    source_start=start,
                    next_start=next_start,
                )
                overflow_action = fit["action"]
                review["text_after"] = text
                review["tts_dur"] = round(tts_dur, 3)

            closed = close_file_to_slot(
                clip, max_avail,
                start=start, next_start=next_start,
                atempo_already=max(float(atempo_applied), 1.0),
                cap=TTS_ATEMPO_SEVERE_MAX,
                allow_speech_trim=False,
                allow_micro_overlap=True,
                label=f"doan {i} last-mile",
            )
            tts_dur = float(closed["dur"])
            extra_t = float(closed.get("tempo_applied") or 1.0)
            atempo_applied *= extra_t
            if extra_t > 1.001:
                atempo_cap_seg = max(float(atempo_cap_seg), float(TTS_ATEMPO_SEVERE_MAX))
                atempo_needed_seg = max(float(atempo_needed_seg), extra_t)
            if closed.get("micro_overlap") or not closed["still_overlap"]:
                overflow_action = str(closed.get("action") or "close_slot")
                if closed.get("micro_overlap"):
                    match_flags.append("micro_overlap")
                    overflow_why = "micro_overlap"
                if (
                    atempo_applied - 1.0 >= TTS_ATEMPO_FLAG_MIN
                    and "atempo" not in match_flags
                ):
                    match_flags.append("atempo")
                if (
                    atempo_applied > TTS_ATEMPO_SAFE_MAX + 1e-9
                    and "atempo_severe" not in match_flags
                ):
                    match_flags.append("atempo_severe")
                log.warning(
                    "OVERFLOW doan %d khop last-mile action=%s tts=%.2fs hard=%.2fs "
                    "atempo=%.3f micro_overlap=%s | GOC: %s | RUT: %s",
                    i, overflow_action, tts_dur, max_avail, atempo_applied,
                    closed.get("micro_overlap"), vi_before, text,
                )
                break

            match_flags.append("qua_dai")
            match_flags.append("chong_doan")
            match_flags.append("overrun_fallback")
            match_flags.append("shortened_semantic_fail")
            overflow_action = "keep_and_flag"
            log.error(
                "OVERFLOW doan %d: tts=%.2fs hard=%.2fs can=%.2fx overlap=%.2fs — "
                "%s. Da rut gon ngu nghia %d lan, van de. GIU cau rut tot nhat "
                "(%d ky tu, khong phai cau goc dai), KHONG cat audio, "
                "KHONG doi start cau sau (timeline_start=%.2f). "
                "atempo_applied=%.3f needed=%.3f cap=%.3f — tran la ranh gioi cuoi, "
                "KHONG tua them. QA FAIL chong_doan; job web se hoan credit. "
                "| GOC: %s | RUT: %s",
                i, tts_dur, max_avail, fit["required_tempo"], fit["overlap_sec"],
                why_txt, max_tries, len(text), start,
                atempo_applied, atempo_needed_seg, atempo_cap_seg,
                vi_before, text,
            )
            shorten_flags.append({
                "i": i,
                "start": round(start, 3),
                "end": round(hard_end, 3),
                "slot_sec": round(max_avail, 3),
                "tts_dur_sec": round(tts_dur, 3),
                "overlap_sec": fit["overlap_sec"],
                "required_tempo": fit["required_tempo"],
                "overflow_action": "keep_and_flag",
                "overflow_reason": merge_why,
                "text": text,
                "text_before": vi_before,
                "source_text": zh_src,
                "shorten_attempts": shorten_used,
                "semantic_attempts": attempts_log,
            })
            break

        t_ln = time.perf_counter()
        mean_lufs = loudnorm_twopass(clip)
        add_bucket("loudnorm", time.perf_counter() - t_ln, log_line=False)
        post_dur = probe_duration(clip) if clip.exists() else tts_dur
        ln_place = plan_placement(
            source_start=start, source_end=hard_end,
            next_start=next_start, tts_dur=post_dur,
        )
        if ln_place["would_overlap"]:
            closed_ln = close_file_to_slot(
                clip, max_avail,
                start=start, next_start=next_start,
                atempo_already=max(float(atempo_applied), 1.0),
                cap=TTS_ATEMPO_SEVERE_MAX,
                allow_speech_trim=False,
                allow_micro_overlap=True,
                label=f"doan {i} loudnorm",
            )
            post_dur = float(closed_ln["dur"])
            extra_ln = float(closed_ln.get("tempo_applied") or 1.0)
            atempo_applied *= extra_ln
            if closed_ln.get("micro_overlap") and "micro_overlap" not in match_flags:
                match_flags.append("micro_overlap")
                overflow_why = "micro_overlap"
            if extra_ln > 1.001:
                atempo_cap_seg = max(float(atempo_cap_seg), float(TTS_ATEMPO_SEVERE_MAX))
        tts_dur = post_dur
        place = plan_placement(
            source_start=start, source_end=hard_end,
            next_start=next_start, tts_dur=post_dur,
        )
        play_slot, speed = post_dur, 1.0
        if not dub_natural:
            play_slot, speed = _effective_play_dur(
                post_dur, max_avail, max_stretch=TTS_ATEMPO_MAX, natural=False,
            )

        play_end = start + play_slot
        voice_end = round(play_end, 3)
        clips.append((start, play_end, clip))
        cue_segs.append({
            **seg,
            "start": round(start, 3),
            "end": voice_end,
            "display_end": round(max(hard_end, voice_end), 3),
            "timeline_start": round(start, 3),
            "timeline_end": voice_end,
            "text": text,
            "tts_rate": seg_cfg.get("rate", seg.get("tts_rate")),
        })
        mean_db = mean_lufs if mean_lufs is not None else _mean_volume_db(clip)
        leftover = bool(cjk_re.search(text))
        flags = [
            f for f in match_flags
            if f not in ("qua_dai", "qua_ngan", "can_rut_gon", "chong_doan", "cat_khop")
        ]
        overlap_now = max(0.0, play_end - (next_start - 0.02))
        micro_ok = overlap_now <= float(DUB_MICRO_OVERLAP_SEC) + 0.02
        micro = (
            ("micro_overlap" in flags or overflow_why == "micro_overlap")
            and micro_ok
        )
        if place["would_overlap"] and not micro:
            flags.append("qua_dai")
            flags.append("chong_doan")
            flags = [f for f in flags if f != "micro_overlap"]
        elif play_slot < orig_slot * 0.85:
            flags.append("qua_ngan")
        if leftover:
            flags.append("chua_dich")
        if speed > TTS_ATEMPO_SEVERE_MAX + 0.02:
            flags.append("qua_nhanh")
        if atempo_applied - 1.0 >= TTS_ATEMPO_FLAG_MIN and "atempo" not in flags:
            flags.append("atempo")
        if atempo_cap_seg > TTS_ATEMPO_SAFE_MAX + 1e-9 and "atempo_severe" not in flags:
            flags.append("atempo_severe")
        if mean_db is not None and abs(mean_db - LOUDNORM_TARGET_I) > 2.0:
            flags.append("vol_lech")
        if play_slot < orig_slot * 0.55:
            flags.append("im_lang")
        if shorten_used:
            flags.append(f"shortened_x{shorten_used}")
        if is_audio_truncated(tts_dur_raw, tts_dur, atempo_applied) or is_audio_truncated(
            tts_dur_raw, post_dur, atempo_applied,
        ):
            if "audio_truncated" not in flags:
                flags.append("audio_truncated")
            if overflow_why in ("khong_tran", "-", "keep"):
                overflow_why = "audio_truncated"
        if micro and "micro_overlap" not in flags:
            flags.append("micro_overlap")
        overlap_ms = round(max(0.0, play_end - (next_start - 0.02)) * 1000, 1)
        qa_rows.append({
            "i": i,
            "start": round(start, 2),
            "source_start": round(start, 3),
            "source_end": round(hard_end, 3),
            "slot": round(orig_slot, 2),
            "hard_limit": round(max_avail, 2),
            "tts_dur_raw": round(tts_dur_raw, 2),
            "tts_dur": round(tts_dur, 2),
            "post_dur": round(post_dur, 2),
            "play": round(play_slot, 2),
            "timeline_start": round(start, 3),
            "timeline_end": voice_end,
            "drift_pct": round(max(0.0, (play_slot - max_avail) / max(max_avail, 0.12) * 100), 1),
            "drift_file_pct": round(max(0.0, (post_dur - max_avail) / max(max_avail, 0.12) * 100), 1),
            "seg_offset_ms": overlap_ms,
            "cum_offset_ms": 0.0,
            "rate_iters": rate_iters,
            "atempo": round(atempo_applied, 3),
            "atempo_applied": round(atempo_applied, 3),
            "atempo_needed": round(float(atempo_needed_seg), 3),
            "atempo_cap": round(float(atempo_cap_seg), 3),
            "openai_speed": round(resolve_openai_speed(seg_cfg), 3),
            "speed": round(speed, 2),
            "mean_db": mean_db,
            "target_lufs": LOUDNORM_TARGET_I,
            "chars": len(text),
            "shorten_used": shorten_used,
            "overflow_action": overflow_action,
            "overflow_reason": overflow_why,
            "overlap_sec": round(max(0.0, play_end - (next_start - 0.02)), 3),
            "required_tempo": round(
                play_slot / max(max_avail, 0.12) if play_slot > max_avail else 1.0, 3,
            ),
            "flags": flags,
            "text": text,
            "source_text": str(seg.get("source_text") or ""),
            "speaker_id": str(seg.get("speaker_id") or seg.get("voice_id") or ""),
            "speaker_relation": str(seg.get("speaker_relation") or ""),
            "pronoun_pair_used": str(seg.get("pronoun_pair_used") or ""),
            "voice_profile": str(seg.get("voice_profile") or ""),
            "tts_voice_name": str(
                seg.get("tts_voice_name") or seg.get("tts_voice") or ""
            ),
            "median_f0_hz": seg.get("median_f0_hz"),
            "semantic_shorten": semantic_by_i.get(i),
        })
        from .job_timing import add_segment, add_tts
        add_tts(i, tts_wall, n_calls=tts_calls)
        add_segment(i, time.perf_counter() - t_seg, n_tts=tts_calls)
        log.info(
            "QA doan %d: source %.2f->%.2f speech=%.1fs hard=%.1fs tts=%.1fs "
            "timeline=%.2f->%.2f atempo=%.2f overflow_why=%s flags=%s | %s",
            i, start, hard_end, orig_slot, max_avail, post_dur,
            start, voice_end, atempo_applied, overflow_why,
            ",".join(flags) or "ok",
            text[:60],
        )

    if not clips:
        raise RuntimeError("Khong co doan thoai hop le de long tieng")

    # Ghi QA ngay khi du data doan — fail-closed TRUOC mix/Demucs/mux.
    (job_work / "segments_qa.json").write_text(
        json.dumps(qa_rows, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (job_work / "segments_cues.json").write_text(
        json.dumps(cue_segs, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    write_shorten_flags(job_work, shorten_flags)
    if semantic_reviews:
        (job_work / "overflow_semantic.json").write_text(
            json.dumps(semantic_reviews, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    if str(cfg.get("audio_mode", "dub")).lower() == "dub":
        from .dub_audit import raise_if_dub_qa_fail
        raise_if_dub_qa_fail(job_work, cfg)

    voice_end_max = 0.0
    for s0, _, cpath in clips:
        voice_end_max = max(voice_end_max, s0 + probe_duration(cpath))
    if video_dur:
        total_dur = float(video_dur)
    else:
        total_dur = max(voice_end_max, total_dur)

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={total_dur:.3f}"]
    for _, _, clip in clips:
        cmd += ["-i", str(clip)]

    filters = []
    mix_inputs = ["[0:a]"]
    fade_s = SEGMENT_CROSSFADE_MS / 1000.0
    max_xfade = float(DUB_MICRO_OVERLAP_SEC)
    for i, (start, play_end, clip_path) in enumerate(clips):
        nxt = clips[i + 1][0] if i + 1 < len(clips) else total_dur
        tts_dur = probe_duration(clip_path)
        overlap_out = max(0.0, (start + tts_dur) - nxt)
        overlap_in = max(0.0, clips[i - 1][1] - start) if i > 0 else 0.0
        fade_in = fade_s
        fade_out = fade_s
        if overlap_in > 0.01:
            fade_in = min(max(fade_s, overlap_in), max_xfade, max(0.02, tts_dur * 0.45))
        if overlap_out > 0.01:
            fade_out = min(max(fade_s, overlap_out), max_xfade, max(0.02, tts_dur * 0.45))
        label = f"a{i}"
        fit = _fit_to_slot_filter(
            f"[{i + 1}:a]", tts_dur, max(0.12, tts_dur), f"f{i}",
            max_stretch=TTS_ATEMPO_MAX,
            natural=True,
        )
        fade_out_st = max(0.0, tts_dur - fade_out)
        delay_ms = int(round(start * 1000))
        filters.append(
            f"{fit};[f{i}]afade=t=in:st=0:d={fade_in:.4f},"
            f"afade=t=out:st={fade_out_st:.4f}:d={fade_out:.4f},"
            f"volume=1.0,adelay={delay_ms}|{delay_ms}[{label}]"
        )
        mix_inputs.append(f"[{label}]")

    n = len(mix_inputs)
    filters.append(
        f"{''.join(mix_inputs)}amix=inputs={n}:duration=first:dropout_transition=0[aout]"
    )

    cmd += ["-filter_complex", ";".join(filters), "-map", "[aout]",
            "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)]
    run(cmd)

    try:
        (job_work / "segments_cues.json").write_text(
            json.dumps(cue_segs, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (job_work / "segments_qa.json").write_text(
            json.dumps(qa_rows, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        write_shorten_flags(job_work, shorten_flags)
        sync_spoken_text_to_segment_files(job_work, cue_segs)
        write_dub_final_report(job_work, qa_rows, video_dur=video_dur or total_dur)
    except Exception:  # noqa: BLE001
        pass

    bad = [r for r in qa_rows if r.get("flags")]
    if bad:
        log.warning("QA: %d/%d doan can xem — %s", len(bad), len(qa_rows),
                    "; ".join(f"#{r['i']} {','.join(r['flags'])}" for r in bad))
        leftover = [r for r in bad if "chua_dich" in (r.get("flags") or [])]
        if leftover:
            raise RuntimeError(
                f"QA: {len(leftover)} doan sot chu Trung/Han — khong giao clip loi"
            )
    else:
        log.info("QA: %d doan ok (khong sot chu, toc <= %.2fx)", len(qa_rows), max_stretch)

    log.info(
        "Long tieng dub sync: %s (%d doan, giu het chu, %.1fs) — sub dung chung timestamp",
        out_path.name, len(clips), total_dur,
    )
    return out_path, cue_segs


def auto_script(video_path: str | Path, work_dir: str | Path, cfg_auto: dict, progress=None):
    from . import transcribe
    return transcribe.auto_script(video_path, work_dir, cfg_auto, progress=progress)
