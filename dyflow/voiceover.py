"""Buoc 3: Long tieng bang edge-tts (theo doan, khop nhip goc)."""
from __future__ import annotations
import asyncio
import json
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypedDict

from .dub_constants import (
    DUB_GROUP_MAX_CHARS,
    DUB_GROUP_MAX_DUR_SEC,
    DUB_GROUP_MAX_GAP_SEC,
    DUB_LARGE_OVERLAP_CROSSFADE_MAX,
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
    borrow_time_from_next,
    decide_fit,
    is_audio_truncated,
    large_overlap_crossfade,
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
from .tts_file_io import (
    atomic_replace,
    clip_integrity_ok,
    finalize_synthesized,
    lifecycle_log,
    unique_tmp,
    wait_file_ready,
    write_bytes_atomic,
)
from .utils import log, run, probe_duration as _probe_duration_raw, probe_duration_retry, win_hidden_kwargs

_TICKS_PER_SEC = 10_000_000
_TTS_RETRIES = 3
_TTS_RETRY_DELAY = 0.5
_TTS_SEGMENT_GAP = 0.3
_TTS_PREFETCH_CONCURRENCY = 1
_TTS_CALL_TIMEOUT_SEC = 45
_openai_chars_lock = threading.Lock()
# Thread-local: work_dir / seg index cho lifecycle log trong synthesize().
_tts_ctx = threading.local()


def _tts_ctx_set(*, work_dir: Path | None = None, seg_i: int | None = None) -> None:
    _tts_ctx.work_dir = work_dir
    _tts_ctx.seg_i = seg_i


def _tts_ctx_get() -> tuple[Path | None, int | None]:
    return getattr(_tts_ctx, "work_dir", None), getattr(_tts_ctx, "seg_i", None)


def probe_duration(path: str | Path) -> float:
    t0 = time.perf_counter()
    try:
        return _probe_duration_raw(path)
    finally:
        from .job_timing import add_bucket
        add_bucket("ffprobe", time.perf_counter() - t0, log_line=False)


async def _await_tts(coro, *, label: str, timeout: float | None = None):
    to = float(timeout or _TTS_CALL_TIMEOUT_SEC)
    try:
        return await asyncio.wait_for(coro, timeout=to)
    except asyncio.TimeoutError as e:
        raise RuntimeError(f"TTS het han {to:.0f}s ({label})") from e


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
    work_dir, seg_i = _tts_ctx_get()
    tmp = unique_tmp(out_path, tag="stream")

    async def _collect() -> list[WordTiming]:
        out: list[WordTiming] = []
        got_audio = False
        lifecycle_log(work_dir, "stream_write_start", tmp, seg_i=seg_i)
        try:
            with open(tmp, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                        got_audio = True
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / _TICKS_PER_SEC
                        end = (chunk["offset"] + chunk["duration"]) / _TICKS_PER_SEC
                        out.append({"text": chunk["text"], "start": start, "end": end})
                f.flush()
                os.fsync(f.fileno())
            lifecycle_log(work_dir, "stream_write_end", tmp, seg_i=seg_i)
            if not got_audio or tmp.stat().st_size < 100:
                raise RuntimeError("edge-tts khong tra audio (stream)")
            if not finalize_synthesized(
                tmp, out_path, work_dir=work_dir, seg_i=seg_i, stage="stream",
            ):
                raise RuntimeError("edge-tts stream finalize that bai")
            return out
        finally:
            tmp.unlink(missing_ok=True)

    t_limit = max(_TTS_CALL_TIMEOUT_SEC, len(text) * 0.22)
    return await _await_tts(_collect(), label="stream", timeout=t_limit)


async def _synthesize_save(text: str, out_path: Path, cfg: dict) -> list[WordTiming]:
    import edge_tts

    communicate = edge_tts.Communicate(text, **_tts_kwargs(cfg, boundary=None))
    work_dir, seg_i = _tts_ctx_get()
    tmp = unique_tmp(out_path, tag="save")
    lifecycle_log(work_dir, "save_write_start", tmp, seg_i=seg_i)
    try:
        t_limit = max(_TTS_CALL_TIMEOUT_SEC, len(text) * 0.22)
        await _await_tts(communicate.save(str(tmp)), label="save", timeout=t_limit)
        lifecycle_log(work_dir, "save_write_end", tmp, seg_i=seg_i)
        if not tmp.exists() or tmp.stat().st_size < 100:
            raise RuntimeError("edge-tts khong tra audio (save)")
        if not finalize_synthesized(
            tmp, out_path, work_dir=work_dir, seg_i=seg_i, stage="save",
        ):
            raise RuntimeError("edge-tts save finalize that bai")
        return []
    finally:
        tmp.unlink(missing_ok=True)


async def _synthesize_split(text: str, out_path: Path, cfg: dict) -> list[WordTiming]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?。！？])\s+", text) if p.strip()]
    if len(parts) <= 1 and len(text) > 100:
        parts = [text[i:i + 100].strip() for i in range(0, len(text), 100)]
        parts = [p for p in parts if p]
    if len(parts) <= 1:
        return await _synthesize_save(text, out_path, cfg)

    # CRITICAL: moi out_path (moi doan/luong) co thu muc part rieng —
    # truoc day dung chung _tts_parts/part_000.mp3 -> race khi prefetch 4 luong.
    run_id = uuid.uuid4().hex[:8]
    tmp_dir = out_path.parent / f"_tts_parts_{out_path.stem}_{os.getpid()}_{run_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    work_dir, seg_i = _tts_ctx_get()
    part_files: list[Path] = []
    concat_tmp = unique_tmp(out_path, tag="split")
    try:
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
            "-filter_complex",
            f"{''.join(f'[{i}:a]' for i in range(n))}concat=n={n}:v=0:a=1[aout]",
            "-map", "[aout]", "-c:a", "libmp3lame", "-b:a", "192k", str(concat_tmp),
        ]
        run(cmd)
        if not finalize_synthesized(
            concat_tmp, out_path, work_dir=work_dir, seg_i=seg_i, stage="split",
        ):
            raise RuntimeError("edge-tts split finalize that bai")
        return []
    finally:
        concat_tmp.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _tts_engine(cfg: dict) -> str:
    return str(cfg.get("tts_engine") or os.getenv("DYFLOW_TTS_ENGINE") or "edge").strip().lower()


def _is_openai_engine(cfg: dict) -> bool:
    return _tts_engine(cfg) in ("openai", "tts-1", "tts-1-hd", "gpt-4o-mini-tts", "openai_tts")


def _is_tvoice_engine(cfg: dict) -> bool:
    return _tts_engine(cfg) in ("tvoice", "tvoiceai", "t_voice")


def _synthesize_tvoice(text: str, out_path: Path, cfg: dict) -> None:
    from .tvoice import synthesize_tvoice

    voice = cfg.get("tvoice_voice") or cfg.get("voice_id") or cfg.get("voice") or "Ngọc Huyền"
    speed = cfg.get("speed") or cfg.get("tvoice_speed") or cfg.get("rate") or 1.0
    synthesize_tvoice(text, out_path, voice=voice, speed=speed)





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

    cast_on = bool(
        base.get("cast_speakers", False)
        if "cast_speakers" in base
        else (base.get("voice_mode") == "multi")
    )
    voice = (seg.get("tts_voice") if cast_on else None) or base.get("voice")
    oa_voice = (seg.get("openai_voice") if cast_on else None) or base.get("openai_voice")
    tv_voice = (seg.get("tvoice_voice") if cast_on else None) or base.get("tvoice_voice")

    out = {
        **base,
        "voice": voice,
        "tts_voice": voice,
        "openai_voice": oa_voice,
        "tvoice_voice": tv_voice,
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
    work_dir, seg_i = _tts_ctx_get()
    lifecycle_log(work_dir, "openai_write_start", out_path, seg_i=seg_i, nbytes=len(data))
    write_bytes_atomic(out_path, data)
    lifecycle_log(work_dir, "openai_write_done", out_path, seg_i=seg_i)
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
    """Tao file audio .mp3 tu text (T-Voice AI, OpenAI tts-1, fallback Edge)."""
    from .prosody import sanitize_edge_cfg

    out_path = Path(out_path)
    text = _sanitize_tts_text(script)
    if not text:
        raise RuntimeError("Script trong, khong the long tieng")

    is_tv = _is_tvoice_engine(cfg)
    if is_tv:
        _synthesize_tvoice(text, out_path, cfg)
        return out_path, []


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
        cur_voice = str(cfg.get("voice") or "")
        alt_voice = cur_voice
        pairs = [
            ("NamMinh", "HoaiMy"),
            ("Guy", "Jenny"),
            ("Andrew", "Aria"),
            ("Keita", "Nanami"),
            ("Antonio", "Francisca"),
            ("Madhur", "Swara"),
            ("Conrad", "Katja"),
            ("Ardi", "Gadis"),
            ("Angelo", "Blessica"),
        ]
        for v1, v2 in pairs:
            if v1 in cur_voice:
                alt_voice = cur_voice.replace(v1, v2)
                break
            if v2 in cur_voice:
                alt_voice = cur_voice.replace(v2, v1)
                break
        alt_plain = {**plain, "voice": alt_voice} if alt_voice != cur_voice else plain
        strategies = [
            ("save", lambda: _synthesize_save(text, out_path, cfg)),
            ("save-plain", lambda: _synthesize_save(text, out_path, plain)),
            ("stream", lambda: _synthesize_stream(text, out_path, cfg, None)),
            ("stream+timing", lambda: _synthesize_stream(text, out_path, cfg, "WordBoundary")),
            ("split+save", lambda: _synthesize_split(text, out_path, plain)),
            ("alt-voice-save", lambda: _synthesize_save(text, out_path, alt_plain)),
        ]
        if len(text) > 200:
            strategies.insert(1, ("split+save", lambda: _synthesize_split(text, out_path, plain)))
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
    """Goi TTS lan dau. OpenAI 4 luong; Edge-TTS 1 luong (tuan tu cach nhe 0.15s) de khong bi Microsoft rate-limit."""
    cjk_re = re.compile(r"[\u4e00-\u9fff]")
    n = max(1, int(cfg.get("tts_prefetch_concurrency") or _TTS_PREFETCH_CONCURRENCY))
    if _is_openai_engine(cfg):
        if sys.platform == "win32" and "tts_prefetch_concurrency" not in cfg:
            n = min(n, 4)
    elif _is_tvoice_engine(cfg):
        n = 3
    else:
        n = 1
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
    log.info("TTS prefetch %d doan, concurrency=%d", len(jobs), n)

    def _one(item: tuple[int, str, Path, dict]) -> None:
        i, text, clip, seg_cfg = item
        from .job_timing import timed_sleep
        stagger = 0.55 if _is_tvoice_engine(cfg) else 0.15
        timed_sleep(stagger, "tts_prefetch_stagger")
        tmp = unique_tmp(clip, tag="prefetch")
        _tts_ctx_set(work_dir=work_dir, seg_i=i)
        lifecycle_log(work_dir, "prefetch_start", clip, seg_i=i, tmp=tmp.name)
        try:
            synthesize(text, tmp, seg_cfg)
            if not finalize_synthesized(
                tmp, clip, work_dir=work_dir, seg_i=i, stage="prefetch",
            ):
                raise RuntimeError("TTS prefetch file rong")
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            lifecycle_log(work_dir, "prefetch_fail", clip, seg_i=i, err=str(e))
            log.warning("TTS prefetch doan %d loi: %s", i, e)
        finally:
            _tts_ctx_set(work_dir=None, seg_i=None)

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
        "tvoice_voice": preset.get("tvoice_voice") or "",
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
    tmp = unique_tmp(path, tag="ln")
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
            atomic_replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        log.warning("Chuan hoa am luong %s loi: %s", path.name, e)
    finally:
        tmp.unlink(missing_ok=True)


def _mean_volume_db(path: Path) -> float | None:
    import subprocess
    try:
        p = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, check=False,
            **win_hidden_kwargs(),
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


def _mix_timeline_clips(
    clips: list[tuple[float, float, Path | str]],
    total_dur: float,
    out_path: Path,
    job_work: Path,
) -> None:
    """Mix audio clips into a full timeline audio file.

    Batches ffmpeg operations and uses -filter_complex_script to avoid
    Windows command-line length limits (WinError 206) on long videos (300+ clips).
    """
    if not clips:
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"anullsrc=r=24000:cl=mono:d={total_dur:.3f}",
            "-c:a", "libmp3lame", "-b:a", "192k", str(out_path),
        ]
        run(cmd)
        return

    fade_s = SEGMENT_CROSSFADE_MS / 1000.0
    max_xfade = float(DUB_LARGE_OVERLAP_CROSSFADE_MAX)

    # Precompute clip timing and fades
    clip_items = []
    for i, (start, play_end, clip_path) in enumerate(clips):
        cpath = Path(clip_path)
        nxt = clips[i + 1][0] if i + 1 < len(clips) else total_dur
        stored = max(0.12, float(play_end) - float(start))
        tts_dur = probe_duration_retry(cpath, fallback=stored, retries=4)
        overlap_out = max(0.0, (start + tts_dur) - nxt)
        overlap_in = max(0.0, clips[i - 1][1] - start) if i > 0 else 0.0
        fade_in = fade_s
        fade_out = fade_s
        if overlap_in > 0.01:
            fade_in = min(max(fade_s, overlap_in), max_xfade, max(0.02, tts_dur * 0.45))
        if overlap_out > 0.01:
            fade_out = min(max(fade_s, overlap_out), max_xfade, max(0.02, tts_dur * 0.45))
        fade_out_st = max(0.0, tts_dur - fade_out)
        clip_items.append({
            "idx": i,
            "start": float(start),
            "play_end": float(play_end),
            "clip_path": cpath,
            "tts_dur": tts_dur,
            "fade_in": fade_in,
            "fade_out": fade_out,
            "fade_out_st": fade_out_st,
            "end_time": float(start) + tts_dur,
        })

    # Partition clips into batches (target ~25 clips per batch to stay well under command line limits)
    TARGET_BATCH_SIZE = 25
    batches: list[list[dict]] = []
    cur_batch: list[dict] = []

    for i, item in enumerate(clip_items):
        cur_batch.append(item)
        is_last_item = (i == len(clip_items) - 1)
        if is_last_item:
            batches.append(cur_batch)
            break
        nxt_start = clip_items[i + 1]["start"]
        has_gap = (item["end_time"] <= nxt_start)
        if len(cur_batch) >= TARGET_BATCH_SIZE and (has_gap or len(cur_batch) >= 40):
            batches.append(cur_batch)
            cur_batch = []

    log.info(
        "Mix timeline: %d clips -> %d batches (%s)",
        len(clip_items), len(batches), [len(b) for b in batches],
    )

    tmp_dir = job_work / "_mix_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # If single batch, write directly to out_path
        if len(batches) == 1:
            script_file = tmp_dir / "filter_single.txt"
            filters = []
            mix_inputs = ["[0:a]"]
            b_items = batches[0]
            for j, it in enumerate(b_items):
                label = f"a{j}"
                fit = _fit_to_slot_filter(
                    f"[{j + 1}:a]", it["tts_dur"], max(0.12, it["tts_dur"]), f"f{j}",
                    max_stretch=TTS_ATEMPO_MAX,
                    natural=True,
                )
                delay_ms = int(round(it["start"] * 1000))
                filters.append(
                    f"{fit};[f{j}]afade=t=in:st=0:d={it['fade_in']:.4f},"
                    f"afade=t=out:st={it['fade_out_st']:.4f}:d={it['fade_out']:.4f},"
                    f"volume=1.0,adelay={delay_ms}|{delay_ms}[{label}]"
                )
                mix_inputs.append(f"[{label}]")
            n = len(mix_inputs)
            filters.append(
                f"{''.join(mix_inputs)}amix=inputs={n}:duration=first:dropout_transition=0[aout]"
            )
            script_file.write_text(";\n".join(filters), encoding="utf-8")

            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={total_dur:.3f}",
            ]
            for it in b_items:
                cmd += ["-i", str(it["clip_path"])]
            cmd += [
                "-filter_complex_script", str(script_file),
                "-map", "[aout]",
                "-c:a", "libmp3lame", "-b:a", "192k",
                str(out_path),
            ]
            run(cmd)
            return

        # Multiple batches: mix each batch to intermediate wav, then concat
        wav_parts: list[Path] = []
        t_batch_start = 0.0

        for b_idx, b_items in enumerate(batches):
            is_last_batch = (b_idx == len(batches) - 1)
            if is_last_batch:
                t_batch_end = total_dur
            else:
                next_batch_first = batches[b_idx + 1][0]
                t_batch_end = next_batch_first["start"]

            b_dur = max(0.1, t_batch_end - t_batch_start)
            script_file = tmp_dir / f"filter_batch_{b_idx}.txt"
            filters = []
            mix_inputs = ["[0:a]"]

            for j, it in enumerate(b_items):
                label = f"a{j}"
                fit = _fit_to_slot_filter(
                    f"[{j + 1}:a]", it["tts_dur"], max(0.12, it["tts_dur"]), f"f{j}",
                    max_stretch=TTS_ATEMPO_MAX,
                    natural=True,
                )
                delay_ms = int(round(max(0.0, it["start"] - t_batch_start) * 1000))
                filters.append(
                    f"{fit};[f{j}]afade=t=in:st=0:d={it['fade_in']:.4f},"
                    f"afade=t=out:st={it['fade_out_st']:.4f}:d={it['fade_out']:.4f},"
                    f"volume=1.0,adelay={delay_ms}|{delay_ms}[{label}]"
                )
                mix_inputs.append(f"[{label}]")

            n = len(mix_inputs)
            filters.append(
                f"{''.join(mix_inputs)}amix=inputs={n}:duration=first:dropout_transition=0[aout]"
            )
            script_file.write_text(";\n".join(filters), encoding="utf-8")

            wav_part = tmp_dir / f"batch_{b_idx:04d}.wav"
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={b_dur:.3f}",
            ]
            for it in b_items:
                cmd += ["-i", str(it["clip_path"])]
            cmd += [
                "-filter_complex_script", str(script_file),
                "-map", "[aout]",
                "-c:a", "pcm_s16le",
                str(wav_part),
            ]
            run(cmd)
            wav_parts.append(wav_part)
            t_batch_start = t_batch_end

        # Concat all wav parts into final MP3
        concat_list = tmp_dir / "concat_list.txt"
        lines = [f"file '{p.as_posix()}'\n" for p in wav_parts]
        concat_list.write_text("".join(lines), encoding="utf-8")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(out_path),
        ]
        run(cmd)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
    # Tang 3: timeline_start doan sau bi tre (muon thoi gian). >= source_start.
    timeline_overrides: dict[int, float] = {}
    cjk_re = re.compile(r"[\u4e00-\u9fff]")
    skip_idx: set[int] = set()

    def _next_live_idx(idx: int) -> int | None:
        j = idx + 1
        while j < len(segs) and j in skip_idx:
            j += 1
        return j if j < len(segs) else None

    def _live_next_start(idx: int) -> float:
        j = _next_live_idx(idx)
        if j is not None:
            src = float(segs[j]["start"])
            return float(timeline_overrides.get(j, src))
        return float(video_dur or 0.0) or (float(segs[idx].get("end") or 0) + 1.5)

    def _following_start_after(j: int) -> float:
        k = _next_live_idx(j)
        if k is not None:
            return float(timeline_overrides.get(k, float(segs[k]["start"])))
        return float(video_dur or 0.0) or (float(segs[j].get("end") or 0) + 1.5)

    prev_sid = None
    n_segs = max(1, len(segs))
    tts_fail_count = 0
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
        source_start = float(seg["start"])
        # Tang 3: neu doan truoc muon thoi gian, start tre hon source_start.
        start = float(timeline_overrides.get(i, source_start))
        if start < source_start - 1e-6:
            start = source_start
        hard_end = float(seg["end"])
        next_start = _live_next_start(i)
        place0 = plan_placement(
            source_start=start, source_end=hard_end,
            next_start=next_start, tts_dur=0.0,
        )
        max_avail = place0["hard_limit"]
        orig_slot = place0["speech_slot"]
        # Ngan sach rut gon: cua thoai + spill. Mix dat dung timeline_start.
        fit_slot = max_avail
        tier3_borrowed_sec = 0.0
        tier5_overlap_sec = 0.0
        tier5_crossfade_sec = 0.0
        used_tier4 = False
        used_tier5 = False
        used_tier3 = False

        clip = work_dir / f"seg_{i:04d}.mp3"
        pct = 66 + int(15 * i / n_segs)
        _prog(pct, f"TTS {i + 1}/{len(segs)}")
        sid = seg.get("speaker_id") or seg.get("voice_id") or ""
        if sid and sid != prev_sid:
            log.info("Giong %s (%s) tu doan %d", sid, seg.get("tts_voice") or cfg.get("voice"), i)
            prev_sid = sid
        seg_cfg = _seg_tts_cfg(cfg, seg)

        auto_shorten = bool(cfg.get("dub_auto_shorten", False))
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

        last_tts_err = ""

        def _tts_once(c: dict) -> bool:
            """TTS vao file tam duy nhat roi fsync+replace — khong de clip trong khi fail."""
            nonlocal tts_wall, tts_calls, last_tts_err
            tts_calls += 1
            t0 = time.perf_counter()
            tmp = unique_tmp(clip, tag="once")
            _tts_ctx_set(work_dir=job_work, seg_i=i)
            lifecycle_log(job_work, "tts_once_start", clip, seg_i=i, tmp=tmp.name)
            try:
                synthesize(text, tmp, c)
                ok = finalize_synthesized(
                    tmp, clip, work_dir=job_work, seg_i=i, stage="tts_once",
                )
                return ok
            except RuntimeError as e:
                last_tts_err = str(e)
                log.warning("TTS doan %d loi: %s", i, e)
                lifecycle_log(job_work, "tts_once_fail", clip, seg_i=i, err=str(e))
                tmp.unlink(missing_ok=True)
                return False
            finally:
                _tts_ctx_set(work_dir=None, seg_i=None)
                tts_wall += time.perf_counter() - t0

        match_flags: list[str] = []
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
                    tts_fail_count += 1
                    err_low = last_tts_err.lower()
                    if any(kw in err_low for kw in [
                        "số dư không đủ", "so du khong du", "không đủ token", "hết token",
                        "token", "không đủ số dư", "insufficient_quota", "out of credit", "credit", "balance"
                    ]):
                        raise RuntimeError(
                            f"Tài khoản TTS đã hết Token (Số dư không đủ cho đoạn {i+1}). "
                            "Vui lòng nạp thêm Token trên tvoiceai.com hoặc chọn giọng (Free) trên đầu danh sách."
                        )
                    if _is_tvoice_engine(cfg):
                        raise RuntimeError(
                            f"Lỗi tạo giọng đọc T-Voice ở đoạn {i+1}: {last_tts_err}. "
                            "Hệ thống đã dừng lại để tránh xuất ra clip bị mất tiếng lồng tiếng."
                        )
                    if tts_fail_count >= 3 or not skip_failed:
                        raise RuntimeError(
                            f"TTS thất bại ({last_tts_err}). "
                            "Hệ thống đã dừng lại để tránh xuất ra clip bị mất tiếng lồng tiếng."
                        )
                    log.warning("Bo qua doan %d (TTS loi), im lang: %s", i, last_tts_err)
                    _make_silence_mp3(clip, min(max_avail, max(0.2, hard_end - start)))
                    match_flags.append("tts_silence_fallback")
                else:
                    tts_fail_count = 0
            else:
                tts_fail_count = 0
            timed_sleep(min(segment_gap, 0.4), "tts_after_synth_gap")

        tts_dur_raw = probe_duration(clip) if clip.exists() else max_avail
        tts_dur = tts_dur_raw
        rate_iters = 0
        atempo_applied = 1.0
        atempo_needed_seg = 1.0
        atempo_cap_seg = float(TTS_ATEMPO_SAFE_MAX)

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
                from .translate_llm import (
                    dub_target_lang, hard_shorten_text, load_term_lock, shorten_for_slot,
                )
                from .genre_profiles import load_genre_lock

                _gid, genre_topic = load_genre_lock(work_dir)
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
                    shorter = shorten_for_slot(
                        text,
                        slot_sec=max_avail,
                        source_text=str(seg.get("source_text") or ""),
                        target=dub_target_lang(cfg),
                        chars_per_sec=chars_per_sec,
                        max_chars=max_chars,
                        locked_relation=str(seg.get("speaker_relation") or ""),
                        locked_pronoun=str(seg.get("pronoun_pair_used") or ""),
                        locked_terms=load_term_lock(segs),
                        topic_context=genre_topic,
                        speaker_id=str(seg.get("speaker_id") or seg.get("voice_id") or ""),
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
                    bak = clip.read_bytes() if clip.exists() and clip.stat().st_size > 100 else b""
                    if _tts_once(seg_cfg):
                        timed_sleep(min(segment_gap, 0.5), "tts_after_shorten_gap")
                        continue
                    # TTS fail: khoi phuc file cu, khong de clip trong/mat.
                    if bak:
                        clip.write_bytes(bak)
                    log.warning(
                        "Rut gon doan %d TTS fail — giu file truoc, khong continue",
                        i,
                    )

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
                nxt_clip = job_work / f"seg_{nxt_i:04d}.mp3"
                merged_locally = False
                if clip.exists() and nxt_clip.exists() and nxt_clip.stat().st_size > 100:
                    tmp_concat = unique_tmp(clip, tag="mrg_concat")
                    try:
                        run([
                            "ffmpeg", "-y", "-i", str(clip), "-i", str(nxt_clip),
                            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[aout]",
                            "-map", "[aout]", "-c:a", "libmp3lame", "-b:a", "192k", str(tmp_concat),
                        ])
                        if finalize_synthesized(tmp_concat, clip, work_dir=job_work, seg_i=i, stage="merge_concat"):
                            tts_dur = probe_duration(clip)
                            merged_locally = True
                            log.info("Gop audio cuc bo %s + %s xong (0.05s, khong can goi TTS lai)", clip.name, nxt_clip.name)
                    except Exception as e:  # noqa: BLE001
                        log.warning("Gop audio cuc bo doan %d va %d loi: %s, fallback TTS", i, nxt_i, e)
                        tmp_concat.unlink(missing_ok=True)
                if not merged_locally:
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

            # --- Tang 2: micro-overlap <=250ms (truoc khi muon/rut) ---
            closed_t2 = close_file_to_slot(
                clip, max_avail,
                start=start, next_start=next_start,
                atempo_already=max(float(atempo_applied), 1.0),
                cap=TTS_ATEMPO_SEVERE_MAX,
                allow_speech_trim=False,
                allow_micro_overlap=True,
                allow_large_overlap=False,
                label=f"doan {i} tier2-micro",
            )
            tts_dur = float(closed_t2["dur"])
            extra_t2 = float(closed_t2.get("tempo_applied") or 1.0)
            atempo_applied *= extra_t2
            if closed_t2.get("micro_overlap") or not closed_t2["still_overlap"]:
                overflow_action = str(closed_t2.get("action") or "close_slot")
                if closed_t2.get("micro_overlap"):
                    match_flags.append("micro_overlap")
                    match_flags.append("chong_doan_micro")
                    overflow_why = "micro_overlap"
                if (
                    atempo_applied - 1.0 >= TTS_ATEMPO_FLAG_MIN
                    and "atempo" not in match_flags
                ):
                    match_flags.append("atempo")
                log.warning(
                    "OVERFLOW doan %d tang2 micro/ok action=%s tts=%.2fs hard=%.2fs "
                    "overlap_sec=%.3f | %s",
                    i, overflow_action, tts_dur, max_avail,
                    float(closed_t2.get("overlap_sec") or 0), text[:50],
                )
                break

            # --- Tang 3: muon thoi gian tu khoang cho sau doan ke ---
            need_borrow = max(0.0, start + tts_dur - (next_start - 0.02))
            nxt_live = _next_live_idx(i)
            if need_borrow > 0.02 and nxt_live is not None:
                nxt_seg = segs[nxt_live]
                nxt_src = float(nxt_seg["start"])
                nxt_tl = float(timeline_overrides.get(nxt_live, nxt_src))
                borrow = borrow_time_from_next(
                    need_sec=need_borrow,
                    next_source_start=nxt_src,
                    next_source_end=float(nxt_seg["end"]),
                    next_timeline_start=nxt_tl,
                    following_start=_following_start_after(nxt_live),
                )
                if borrow.get("applied"):
                    timeline_overrides[nxt_live] = float(borrow["new_next_start"])
                    tier3_borrowed_sec = float(borrow["borrowed_sec"])
                    used_tier3 = True
                    next_start = _live_next_start(i)
                    place_b = plan_placement(
                        source_start=start, source_end=hard_end,
                        next_start=next_start, tts_dur=tts_dur,
                    )
                    max_avail = place_b["hard_limit"]
                    fit_slot = max_avail
                    match_flags.append("tier3_borrowed_time")
                    overflow_why = "tier3_borrow"
                    log.warning(
                        "TIER3 doan %d muon %.3fs tu doan %d "
                        "(next_start %.3f->%.3f, room=%.3f) — overflow con %.3fs",
                        i, tier3_borrowed_sec, nxt_live, nxt_tl,
                        borrow["new_next_start"], borrow["room_sec"],
                        max(0.0, start + tts_dur - (next_start - 0.02)),
                    )
                    if not place_b["would_overlap"]:
                        overflow_action = "tier3_borrow"
                        if (
                            atempo_applied - 1.0 >= TTS_ATEMPO_FLAG_MIN
                            and "atempo" not in match_flags
                        ):
                            match_flags.append("atempo")
                        break
                    # Van tran: thu micro lai sau khi hard_limit mo rong.
                    closed_t3 = close_file_to_slot(
                        clip, max_avail,
                        start=start, next_start=next_start,
                        atempo_already=max(float(atempo_applied), 1.0),
                        cap=TTS_ATEMPO_SEVERE_MAX,
                        allow_speech_trim=False,
                        allow_micro_overlap=True,
                        allow_large_overlap=False,
                        label=f"doan {i} after-tier3",
                    )
                    tts_dur = float(closed_t3["dur"])
                    atempo_applied *= float(closed_t3.get("tempo_applied") or 1.0)
                    if closed_t3.get("micro_overlap") or not closed_t3["still_overlap"]:
                        overflow_action = str(closed_t3.get("action") or "tier3_borrow")
                        if closed_t3.get("micro_overlap"):
                            match_flags.append("micro_overlap")
                            match_flags.append("chong_doan_micro")
                            overflow_why = "micro_overlap"
                        break

            log.info(
                "SEMANTIC_SHORTEN doan %d BAT DAU: overflow_why=%s tts=%.2fs hard=%.2fs "
                "tier3_borrow=%.3fs",
                i, overflow_why, tts_dur, max_avail, tier3_borrowed_sec,
            )

            # Gop het muc + tang 3: rut gon ngu nghia (+ tang 4 aggressive).
            from .translate_llm import (
                dub_target_lang, load_term_lock, locked_cast_payload,
                shorten_semantic_overflow,
            )
            from .genre_profiles import load_genre_lock

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
            term_lock = load_term_lock(segs)
            _gid, genre_topic = load_genre_lock(work_dir)

            while attempt < max_tries:
                attempt += 1
                is_tier4 = attempt >= 4
                aggressive = attempt >= 3 or last_ineffective or is_tier4
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
                    "tier4": is_tier4,
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
                    locked_terms=term_lock,
                    topic_context=genre_topic,
                    speaker_id=str(seg.get("speaker_id") or seg.get("voice_id") or ""),
                    aggressive=aggressive,
                    ineffective_hint=ineffective_hint,
                    tier4=is_tier4,
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
                    if is_tier4:
                        used_tier4 = True
                        if "tier4_aggressive_cut" not in match_flags:
                            match_flags.append("tier4_aggressive_cut")
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
                if is_tier4:
                    used_tier4 = True
                    if "tier4_aggressive_cut" not in match_flags:
                        match_flags.append("tier4_aggressive_cut")
                if attempt < max_tries:
                    log.info(
                        "SEMANTIC_SHORTEN doan %d lan %d chua vua (actual_overlap cov=%.1f%%) "
                        "-> thu lan %d%s",
                        i, attempt, cov_ratio * 100, attempt + 1,
                        " (tier4, bo menh de phu)" if attempt + 1 >= 4
                        else (" (severe, cat manh hon)" if cov_ratio > 1.20 else ""),
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
                allow_large_overlap=True,
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
                    match_flags.append("chong_doan_micro")
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

            # --- Tang 5: overlap lon + crossfade dai — zero-fail, luon xuat ---
            ov = float(closed.get("overlap_sec") or max(
                0.0, start + tts_dur - (next_start - 0.02),
            ))
            xfade = float(closed.get("crossfade_sec") or large_overlap_crossfade(ov))
            if not closed.get("large_overlap"):
                # Dam bao chap nhan ke ca khi plan khong set (edge case).
                ov = max(0.0, start + tts_dur - (next_start - 0.02))
                xfade = large_overlap_crossfade(ov)
            used_tier5 = True
            tier5_overlap_sec = round(ov, 3)
            tier5_crossfade_sec = round(xfade, 3)
            match_flags.append("tier5_large_overlap")
            match_flags.append("chong_doan_severe")
            match_flags.append("qua_dai")
            if used_tier4 and "tier4_aggressive_cut" not in match_flags:
                match_flags.append("tier4_aggressive_cut")
            match_flags.append("shortened_semantic_fail")
            overflow_action = "tier5_large_overlap"
            overflow_why = "tier5_large_overlap"
            log.warning(
                "TIER5 doan %d: overlap=%.3fs crossfade=%.3fs tts=%.2fs hard=%.2fs "
                "— GIU full audio, KHONG cat, KHONG fail-closed. Can REVIEW. "
                "| GOC: %s | RUT: %s",
                i, tier5_overlap_sec, tier5_crossfade_sec, tts_dur, max_avail,
                vi_before, text,
            )
            shorten_flags.append({
                "i": i,
                "start": round(start, 3),
                "end": round(hard_end, 3),
                "slot_sec": round(max_avail, 3),
                "tts_dur_sec": round(tts_dur, 3),
                "overlap_sec": tier5_overlap_sec,
                "crossfade_sec": tier5_crossfade_sec,
                "required_tempo": fit.get("required_tempo"),
                "overflow_action": "tier5_large_overlap",
                "overflow_reason": merge_why,
                "text": text,
                "text_before": vi_before,
                "source_text": zh_src,
                "shorten_attempts": shorten_used,
                "semantic_attempts": attempts_log,
                "tier3_borrowed_sec": tier3_borrowed_sec,
                "tier4": used_tier4,
                "tier5": True,
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
                allow_large_overlap=True,
                label=f"doan {i} loudnorm",
            )
            post_dur = float(closed_ln["dur"])
            extra_ln = float(closed_ln.get("tempo_applied") or 1.0)
            atempo_applied *= extra_ln
            if closed_ln.get("micro_overlap") and "micro_overlap" not in match_flags:
                match_flags.append("micro_overlap")
                match_flags.append("chong_doan_micro")
                overflow_why = "micro_overlap"
            if closed_ln.get("large_overlap"):
                used_tier5 = True
                tier5_overlap_sec = round(float(closed_ln.get("overlap_sec") or 0), 3)
                tier5_crossfade_sec = round(float(closed_ln.get("crossfade_sec") or 0), 3)
                if "tier5_large_overlap" not in match_flags:
                    match_flags.append("tier5_large_overlap")
                if "chong_doan_severe" not in match_flags:
                    match_flags.append("chong_doan_severe")
                overflow_why = "tier5_large_overlap"
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
        # Bao ve file cuoi: backup + khong append clip rong.
        if not clip.exists() or clip.stat().st_size < 100:
            log.warning(
                "Doan %d clip mat/rong sau xu ly — TTS lai truoc khi append",
                i,
            )
            seg_cfg = _seg_tts_cfg(cfg, seg)
            if not _tts_once(seg_cfg):
                _make_silence_mp3(clip, max(0.2, play_slot))
                match_flags.append("tts_silence_fallback")
        bak_path = clip.with_name(clip.stem + "._bak.mp3")
        try:
            if clip.exists() and clip.stat().st_size > 100:
                shutil.copy2(clip, bak_path)
        except OSError as e:
            log.debug("Backup clip %s loi: %s", clip.name, e)
        clips.append((start, play_end, clip))
        cue_segs.append({
            **seg,
            "start": round(start, 3),
            "end": voice_end,
            "display_end": round(max(hard_end, voice_end), 3),
            "timeline_start": round(start, 3),
            "timeline_end": voice_end,
            "source_start": round(source_start, 3),
            "text": text,
            "tts_rate": seg_cfg.get("rate", seg.get("tts_rate")),
            "tts_silence_fallback": "tts_silence_fallback" in match_flags,
        })
        mean_db = mean_lufs if mean_lufs is not None else _mean_volume_db(clip)
        leftover = bool(cjk_re.search(text))
        _strip_legacy = (
            "qua_dai", "qua_ngan", "can_rut_gon", "chong_doan", "cat_khop",
            "chong_doan_micro", "chong_doan_severe",
        )
        flags = [f for f in match_flags if f not in _strip_legacy]
        # Giữ lại tier flags / micro / severe da gan trong match_flags.
        for keep in (
            "tier3_borrowed_time", "tier4_aggressive_cut", "tier5_large_overlap",
            "micro_overlap", "chong_doan_micro", "chong_doan_severe",
            "shortened_semantic", "shortened_semantic_fail", "atempo", "atempo_severe",
            "overrun_fallback",
        ):
            if keep in match_flags and keep not in flags:
                flags.append(keep)
        overlap_now = max(0.0, play_end - (next_start - 0.02))
        micro_ok = overlap_now <= float(DUB_MICRO_OVERLAP_SEC) + 0.02
        micro = (
            ("micro_overlap" in flags or "chong_doan_micro" in flags
             or overflow_why == "micro_overlap")
            and micro_ok
            and not used_tier5
        )
        if used_tier5 or ("tier5_large_overlap" in flags):
            if "tier5_large_overlap" not in flags:
                flags.append("tier5_large_overlap")
            if "chong_doan_severe" not in flags:
                flags.append("chong_doan_severe")
            if "qua_dai" not in flags:
                flags.append("qua_dai")
            if tier5_overlap_sec <= 0 and overlap_now > 0:
                tier5_overlap_sec = round(overlap_now, 3)
            if tier5_crossfade_sec <= 0 and overlap_now > 0:
                tier5_crossfade_sec = large_overlap_crossfade(overlap_now)
        elif place["would_overlap"] and not micro:
            # Van de nhung chua gan tier5 (edge) — zero-fail: gan severe.
            used_tier5 = True
            tier5_overlap_sec = round(overlap_now, 3)
            tier5_crossfade_sec = large_overlap_crossfade(overlap_now)
            flags.append("qua_dai")
            flags.append("chong_doan_severe")
            flags.append("tier5_large_overlap")
        elif micro:
            if "micro_overlap" not in flags:
                flags.append("micro_overlap")
            if "chong_doan_micro" not in flags:
                flags.append("chong_doan_micro")
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
        if used_tier3 and "tier3_borrowed_time" not in flags:
            flags.append("tier3_borrowed_time")
        if used_tier4 and "tier4_aggressive_cut" not in flags:
            flags.append("tier4_aggressive_cut")
        overlap_ms = round(max(0.0, play_end - (next_start - 0.02)) * 1000, 1)
        qa_rows.append({
            "i": i,
            "start": round(start, 2),
            "source_start": round(source_start, 3),
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
            "tier3_borrowed_sec": round(tier3_borrowed_sec, 3),
            "tier5_overlap_sec": round(tier5_overlap_sec, 3),
            "tier5_crossfade_sec": round(tier5_crossfade_sec, 3),
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
        clip_sz = clip.stat().st_size if clip.exists() else 0
        lifecycle_log(
            job_work, "qa_read", clip,
            seg_i=i, size=clip_sz, tts_dur=round(post_dur, 3),
        )
        log.info(
            "QA doan %d: source %.2f->%.2f speech=%.1fs hard=%.1fs tts=%.1fs "
            "timeline=%.2f->%.2f atempo=%.2f overflow_why=%s flags=%s size=%d | %s",
            i, source_start, hard_end, orig_slot, max_avail, post_dur,
            start, voice_end, atempo_applied, overflow_why,
            ",".join(flags) or "ok", clip_sz,
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

    # Pre-mix integrity: khong tin QA da pass — file co the doi giua 2 thoi diem.
    mix_t0 = time.perf_counter()
    lifecycle_log(job_work, "mix_integrity_begin", None, n_clips=len(clips))
    recovered_bak = 0
    recovered_retts = 0
    recovered_silence = 0
    still_bad: list[str] = []

    def _qa_flag(idx: int, flag: str) -> None:
        if idx < 0 or idx >= len(qa_rows):
            return
        fl = list(qa_rows[idx].get("flags") or [])
        if flag not in fl:
            fl.append(flag)
            qa_rows[idx]["flags"] = fl
        qa_rows[idx]["mix_recovery"] = flag

    for idx, (s0, play_end, cpath) in enumerate(clips):
        cpath = Path(cpath)
        stored = max(0.2, float(play_end) - float(s0))
        lifecycle_log(
            job_work, "mix_read", cpath,
            seg_i=idx, expected_dur=round(stored, 3),
        )
        if clip_integrity_ok(cpath, expected_dur_sec=stored, retries=5):
            continue
        # Race van xay ra — dem + phuc hoi, khong fail job neu cuu duoc.
        log.warning(
            "Mix integrity FAIL %s (expected ~%.2fs) — thu phuc hoi",
            cpath.name, stored,
        )
        fixed = False
        bak = cpath.with_name(cpath.stem + "._bak.mp3")
        if wait_file_ready(bak, min_bytes=100, retries=3):
            try:
                shutil.copy2(bak, cpath)
                if clip_integrity_ok(cpath, expected_dur_sec=stored, retries=3):
                    log.warning("Mix: phuc hoi %s tu backup", cpath.name)
                    recovered_bak += 1
                    _qa_flag(idx, "tts_recovered_at_mix")
                    lifecycle_log(job_work, "mix_recovered_bak", cpath, seg_i=idx)
                    fixed = True
            except OSError as e:
                log.warning("Mix: copy backup %s loi: %s", cpath.name, e)
        if not fixed:
            cue = cue_segs[idx] if idx < len(cue_segs) else {}
            t = _sanitize_tts_text(cue.get("text") or "")
            if t:
                tmp = unique_tmp(cpath, tag="mixfix")
                try:
                    _tts_ctx_set(work_dir=job_work, seg_i=idx)
                    synthesize(t, tmp, _seg_tts_cfg(cfg, cue))
                    if finalize_synthesized(
                        tmp, cpath, work_dir=job_work, seg_i=idx, stage="mix_retts",
                    ) and clip_integrity_ok(cpath, expected_dur_sec=None, retries=3):
                        log.warning("Mix: TTS lai %s ok", cpath.name)
                        recovered_retts += 1
                        _qa_flag(idx, "tts_recovered_at_mix")
                        lifecycle_log(job_work, "mix_recovered_retts", cpath, seg_i=idx)
                        fixed = True
                except Exception as e:  # noqa: BLE001
                    log.warning("Mix: TTS lai %s loi: %s", cpath.name, e)
                    tmp.unlink(missing_ok=True)
                finally:
                    _tts_ctx_set(work_dir=None, seg_i=None)
        if not fixed:
            try:
                _make_silence_mp3(cpath, stored)
                recovered_silence += 1
                _qa_flag(idx, "tts_silence_fallback")
                if idx < len(cue_segs):
                    cue_segs[idx]["tts_silence_fallback"] = True
                lifecycle_log(job_work, "mix_silence_fallback", cpath, seg_i=idx)
                log.warning("Mix: %s -> silence %.2fs (cuu job)", cpath.name, stored)
                fixed = True
            except Exception as e:  # noqa: BLE001
                still_bad.append(f"{cpath.name} ({e})")
    recovered = recovered_bak + recovered_retts + recovered_silence
    mix_stats = {
        "mix_recovered_total": recovered,
        "mix_recovered_from_bak": recovered_bak,
        "mix_recovered_retts": recovered_retts,
        "mix_silence_fallback": recovered_silence,
        "mix_integrity_ms": round((time.perf_counter() - mix_t0) * 1000, 1),
    }
    (job_work / "mix_recovery.json").write_text(
        json.dumps(mix_stats, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    # Viet lai QA neu co flag phuc hoi — de UI/report thay.
    if recovered:
        (job_work / "segments_qa.json").write_text(
            json.dumps(qa_rows, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    log.info(
        "Mix integrity: recovered=%d (bak=%d retts=%d silence=%d) — 0 la ly tuong",
        recovered, recovered_bak, recovered_retts, recovered_silence,
    )
    lifecycle_log(job_work, "mix_integrity_end", None, **mix_stats)
    if still_bad:
        raise RuntimeError(
            "TTS file loi truoc khi mix (khong phuc hoi duoc) — "
            + "; ".join(still_bad[:5])
        )

    voice_end_max = 0.0
    for s0, play_end, cpath in clips:
        stored = max(0.12, float(play_end) - float(s0))
        dur = probe_duration_retry(cpath, fallback=stored, retries=4)
        voice_end_max = max(voice_end_max, float(s0) + dur)
    if video_dur:
        total_dur = float(video_dur)
    else:
        total_dur = max(voice_end_max, total_dur)

    _mix_timeline_clips(clips, total_dur, out_path, job_work)

    try:
        (job_work / "segments_cues.json").write_text(
            json.dumps(cue_segs, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (job_work / "segments_qa.json").write_text(
            json.dumps(qa_rows, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        write_shorten_flags(job_work, shorten_flags)
        sync_spoken_text_to_segment_files(job_work, cue_segs)
        report = write_dub_final_report(job_work, qa_rows, video_dur=video_dur or total_dur)
        # Thong ke cuoi: so doan phai tu phuc hoi o mix (0 = ly tuong).
        mix_path = job_work / "mix_recovery.json"
        mix_info = {}
        if mix_path.exists():
            try:
                mix_info = json.loads(mix_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                mix_info = {}
        n_rec = int(mix_info.get("mix_recovered_total") or 0)
        n_sil = int(mix_info.get("mix_silence_fallback") or 0)
        log.info(
            "JOB STAT mix_recovered=%d silence_fallback=%d (0 recovered = khong co race)",
            n_rec, n_sil,
        )
        if isinstance(report, dict):
            report["mix_recovery"] = mix_info
            mix_path.write_text(
                json.dumps({**mix_info, "logged": True}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (job_work / "dub_final_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
            )
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
