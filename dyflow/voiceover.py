"""Buoc 3: Long tieng bang edge-tts (theo doan, khop nhip goc)."""
from __future__ import annotations
import asyncio
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import TypedDict

from .dub_constants import (
    DUB_CHARS_PER_SEC,
    DUB_SHORTEN_MAX_RETRIES,
    LOUDNORM_TARGET_I,
    SEGMENT_CROSSFADE_MS,
    TARGET_CHARS_PER_SEC,
    TTS_ATEMPO_MAX,
)
from .dub_report import write_dub_final_report, write_shorten_flags, sync_spoken_text_to_segment_files
from .dub_tts import (
    apply_atempo_file,
    drift_ratio,
    duration_mismatch_flags,
    edge_rate_converge,
    loudnorm_twopass,
    mild_atempo_for_residual,
)
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


def _tts_engine(cfg: dict) -> str:
    return str(cfg.get("tts_engine") or os.getenv("DYFLOW_TTS_ENGINE") or "edge").strip().lower()


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
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except Exception:  # noqa: BLE001
        pass
    key = os.getenv("OPENAI_API_KEY") or ""
    if not key:
        raise RuntimeError("Thieu OPENAI_API_KEY trong .env")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Chua cai openai. pip install openai") from e

    model = str(cfg.get("openai_model") or "tts-1")
    voice = _openai_voice_id(cfg)
    client = OpenAI(api_key=key)
    r = client.audio.speech.create(
        model=model,
        voice=voice,
        input=text,
        response_format="mp3",
    )
    data = r.content if hasattr(r, "content") else bytes(r)
    out_path.write_bytes(data)
    if out_path.stat().st_size < 100:
        raise RuntimeError("OpenAI TTS khong tra audio")
    nchars = len(text)
    log.info("OpenAI TTS %s voice=%s chars=%d -> %s", model, voice, nchars, out_path.name)
    acc = Path(out_path).parent.parent / "openai_tts_chars.txt"
    try:
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
    cfg = sanitize_edge_cfg(cfg)

    if _tts_engine(cfg) in ("openai", "tts-1", "tts-1-hd", "gpt-4o-mini-tts", "openai_tts"):
        try:
            _synthesize_openai(text, out_path, cfg)
            return out_path, []
        except Exception as e:  # noqa: BLE001
            log.warning("OpenAI TTS loi, fallback Edge: %s", e)

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
                    await asyncio.sleep(wait)
        raise RuntimeError(f"TTS that bai sau {_TTS_RETRIES} lan: {last_err}") from last_err

    words = asyncio.run(_run())
    log.info("Long tieng xong: %s (%d tu)", out_path.name, len(words))
    return out_path, words


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

    natural=True (mac dinh): toc do tu nhien 1.0x; chi tua nhe khi TTS dai hon slot.
    natural=False: keo/tua de lap day slot (de gay giong robot).
    """
    slot = max(0.12, float(slot))
    if tts_dur < 0.05:
        return f"{input_label}anull[{out_label}]"

    if natural:
        if tts_dur <= slot * 1.02:
            fill = slot * 0.88
            if tts_dur < fill * 0.92:
                slow = max(0.92, tts_dur / fill)
                if slow < 0.995:
                    chain = _atempo_chain(slow)
                    return f"{input_label}{chain},asetpts=PTS-STARTPTS[{out_label}]"
            return f"{input_label}asetpts=PTS-STARTPTS[{out_label}]"
        cap = max(1.01, min(float(max_stretch), 1.15))
        ratio = max(1.01, min(tts_dur / slot, cap))
        chain = _atempo_chain(ratio)
        return f"{input_label}{chain},asetpts=PTS-STARTPTS[{out_label}]"

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
        if tts_dur <= slot * 1.02:
            fill = slot * 0.88
            if tts_dur < fill * 0.92:
                slow = max(0.92, tts_dur / fill)
                return tts_dur / slow, slow
            return tts_dur, 1.0
        cap = max(1.01, min(float(max_stretch), 1.15))
        ratio = max(1.01, min(tts_dur / slot, cap))
        return tts_dur / ratio, ratio
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
        time.sleep(min(segment_gap, 0.5))
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


def synthesize_segments(
    segments: list[dict],
    work_dir: str | Path,
    out_path: str | Path,
    cfg: dict,
    video_dur: float | None = None,
    source_video: str | Path | None = None,
) -> tuple[Path, list[dict]]:
    """Long tieng tung doan dung nhip goc.

    Tra ve (voice.mp3, segments_da_chuan_hoa) — sub phai dung cung mang timestamp nay.
    """
    from . import prosody

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(out_path)

    if not segments:
        raise RuntimeError("Khong co doan thoai de long tieng")

    match_prosody = bool(cfg.get("match_original_prosody", True))
    max_stretch = float(cfg.get("max_atempo_stretch", 1.12))
    max_stretch = max(1.05, min(max_stretch, 1.25))
    is_openai = _tts_engine(cfg) in ("openai", "tts-1", "tts-1-hd", "gpt-4o-mini-tts", "openai_tts")
    if is_openai:
        max_stretch = min(max_stretch, 1.08)
    dub_natural = str(cfg.get("dub_timing", "natural")).lower() not in (
        "fill", "fill_slot", "slot", "stretch",
    )
    job_work = work_dir.parent if work_dir.name == "seg_tts" else work_dir

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
    skip_failed = bool(cfg.get("skip_failed_segments", False))
    if is_openai:
        skip_failed = False
    segment_gap = float(cfg.get("tts_segment_gap", _TTS_SEGMENT_GAP))

    clips: list[tuple[float, float, Path]] = []
    cue_segs: list[dict] = []
    qa_rows: list[dict] = []
    shorten_flags: list[dict] = []
    cjk_re = re.compile(r"[\u4e00-\u9fff]")

    prev_sid = None
    for i, seg in enumerate(segs):
        text = _sanitize_tts_text(seg.get("text", ""))
        if not text:
            continue
        if cjk_re.search(text):
            raise RuntimeError(
                f"Doan {i} van con chu Trung/Han, khong TTS clip loi: {text[:80]}"
            )
        start = float(seg["start"])
        hard_end = float(seg["end"])
        next_start = float(segs[i + 1]["start"]) if i + 1 < len(segs) else (
            float(video_dur) if video_dur else hard_end + 1.5
        )
        max_avail = max(0.15, next_start - start - 0.04)

        clip = work_dir / f"seg_{i:04d}.mp3"
        sid = seg.get("speaker_id") or seg.get("voice_id") or ""
        if sid and sid != prev_sid:
            log.info("Giong %s (%s) tu doan %d", sid, seg.get("tts_voice") or cfg.get("voice"), i)
            prev_sid = sid
        seg_cfg = prosody.sanitize_edge_cfg({
            **cfg,
            "voice": seg.get("tts_voice") or cfg.get("voice"),
            "openai_voice": seg.get("openai_voice") or cfg.get("openai_voice"),
            "rate": seg.get("tts_rate") or cfg.get("rate", "+0%"),
            "pitch": seg.get("tts_pitch") or cfg.get("pitch", "+0Hz"),
            "volume": seg.get("tts_volume") or cfg.get("volume", "+0%"),
        })

        def _tts_once(c: dict) -> bool:
            try:
                if clip.exists():
                    clip.unlink(missing_ok=True)
                synthesize(text, clip, c)
                return clip.exists() and clip.stat().st_size > 100
            except RuntimeError as e:
                log.warning("TTS doan %d loi: %s", i, e)
                return False

        if refresh or not (clip.exists() and clip.stat().st_size > 100):
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
            time.sleep(segment_gap)
        else:
            log.info("Bo qua (da co): %s", clip.name)


        tts_dur_raw = probe_duration(clip) if clip.exists() else max_avail
        tts_dur = tts_dur_raw
        rate_iters = 0
        atempo_applied = 1.0
        match_flags: list[str] = []
        shorten_used = 0
        auto_shorten = bool(cfg.get("dub_auto_shorten", True))
        chars_per_sec = float(
            cfg.get("target_chars_per_sec")
            or cfg.get("dub_chars_per_sec")
            or (cfg.get("auto") or {}).get("target_chars_per_sec")
            or (cfg.get("auto") or {}).get("dub_chars_per_sec")
            or TARGET_CHARS_PER_SEC
        )
        max_shorten = int(cfg.get("dub_shorten_max_retries", DUB_SHORTEN_MAX_RETRIES))

        for shorten_pass in range(max_shorten + 1):
            tts_dur_raw = probe_duration(clip) if clip.exists() else max_avail
            tts_dur = tts_dur_raw
            rate_iters = 0
            atempo_applied = 1.0
            match_flags = []

            if not is_openai and text:
                seg_cfg, tts_dur, match_res = edge_rate_converge(
                    i,
                    slot_dur=max_avail,
                    tts_dur=tts_dur,
                    seg_cfg=seg_cfg,
                    clip=clip,
                    tts_once=_tts_once,
                    prosody=prosody,
                    segment_gap=segment_gap,
                )
                rate_iters = match_res.iters
                match_flags = list(match_res.flags)
                atempo_applied = mild_atempo_for_residual(
                    tts_dur, max_avail, skip=match_res.needs_shorten,
                )
                if atempo_applied != 1.0:
                    apply_atempo_file(clip, atempo_applied)
                    tts_dur = probe_duration(clip)
                    log.info(
                        "Doan %d atempo nhe %.3fx (%.2fs -> %.2fs / slot %.2fs)",
                        i, atempo_applied, tts_dur_raw, tts_dur, max_avail,
                    )

                if match_res.needs_shorten and auto_shorten and shorten_pass < max_shorten:
                    from .translate_llm import shorten_for_slot

                    shorter = shorten_for_slot(
                        text,
                        slot_sec=max_avail,
                        source_text=str(seg.get("source_text") or ""),
                        target=str(cfg.get("translate_to") or "vi"),
                        chars_per_sec=chars_per_sec,
                    )
                    if shorter and shorter != text:
                        text = _sanitize_tts_text(shorter)
                        shorten_used += 1
                        seg_cfg = prosody.sanitize_edge_cfg({
                            **cfg,
                            "rate": seg.get("tts_rate") or cfg.get("rate", "+0%"),
                            "pitch": seg.get("tts_pitch") or cfg.get("pitch", "+0Hz"),
                            "volume": seg.get("tts_volume") or cfg.get("volume", "+0%"),
                        })
                        if _tts_once(seg_cfg):
                            time.sleep(min(segment_gap, 0.5))
                            continue

                # Re-danh gia sau atempo: bo qua_dai neu file drift <= 15%
                post_flags, _ = duration_mismatch_flags(tts_dur, max_avail)
                if not post_flags:
                    match_flags = [
                        f for f in match_flags
                        if f not in ("qua_dai", "qua_ngan", "can_rut_gon")
                    ]
                else:
                    match_flags = [
                        f for f in match_flags
                        if f not in ("qua_dai", "qua_ngan", "can_rut_gon")
                    ] + post_flags

                if "qua_dai" in match_flags:
                    shorten_flags.append({
                        "i": i,
                        "start": round(start, 3),
                        "end": round(hard_end, 3),
                        "slot_sec": round(max_avail, 3),
                        "tts_dur_sec": round(tts_dur, 3),
                        "drift_pct": round(drift_ratio(tts_dur, max_avail) * 100, 1),
                        "drift_play_pct": round(
                            drift_ratio(
                                _effective_play_dur(
                                    tts_dur, max_avail, max_stretch=TTS_ATEMPO_MAX, natural=True,
                                )[0],
                                max_avail,
                            ) * 100,
                            1,
                        ),
                        "text": text,
                        "shorten_attempts": shorten_used,
                    })
                break
            break

        mean_lufs = loudnorm_twopass(clip)
        post_dur = probe_duration(clip) if clip.exists() else tts_dur

        slot_for_fit = max_avail
        play_slot, speed = _effective_play_dur(
            post_dur, slot_for_fit, max_stretch=TTS_ATEMPO_MAX, natural=True,
        )
        if speed > max_stretch + 0.02:
            log.info(
                "Doan %d TTS %.2fs / slot %.2fs -> play %.2fs toc=%.2fx",
                i, tts_dur, slot_for_fit, play_slot, speed,
            )

        play_end = start + play_slot
        # Phu de + gate chi trong luc CO giong (khong hien chu khi im lang)
        voice_end = round(start + play_slot, 3)
        clips.append((start, play_end, clip))
        cue_segs.append({
            **seg,
            "start": round(start, 3),
            "end": voice_end,
            "display_end": round(float(seg.get("end", voice_end)), 3),
            "text": text,
            "tts_rate": seg_cfg.get("rate", seg.get("tts_rate")),
        })
        mean_db = mean_lufs if mean_lufs is not None else _mean_volume_db(clip)
        dr_pct = round(drift_ratio(play_slot, max_avail) * 100, 1)
        drift_file_pct = round(drift_ratio(post_dur, max_avail) * 100, 1)
        leftover = bool(cjk_re.search(text))
        flags = list(match_flags)
        if leftover:
            flags.append("chua_dich")
        if speed > TTS_ATEMPO_MAX + 0.02:
            flags.append("qua_nhanh")
        if atempo_applied != 1.0:
            flags.append("atempo")
        if mean_db is not None and abs(mean_db - LOUDNORM_TARGET_I) > 2.0:
            flags.append("vol_lech")
        if play_slot < max_avail * 0.55:
            flags.append("im_lang")
        if shorten_used:
            flags.append(f"shortened_x{shorten_used}")
        seg_offset_ms = round((play_slot - max_avail) * 1000, 1)
        cum_offset_ms = round(
            sum(float(r.get("seg_offset_ms", 0)) for r in qa_rows) + seg_offset_ms, 1,
        )
        qa_rows.append({
            "i": i,
            "start": round(start, 2),
            "slot": round(max_avail, 2),
            "tts_dur_raw": round(tts_dur_raw, 2),
            "tts_dur": round(tts_dur, 2),
            "post_dur": round(post_dur, 2),
            "play": round(play_slot, 2),
            "drift_pct": dr_pct,
            "drift_file_pct": drift_file_pct,
            "seg_offset_ms": seg_offset_ms,
            "cum_offset_ms": cum_offset_ms,
            "rate_iters": rate_iters,
            "atempo": round(atempo_applied, 3),
            "speed": round(speed, 2),
            "mean_db": mean_db,
            "target_lufs": LOUDNORM_TARGET_I,
            "chars": len(text),
            "shorten_used": shorten_used,
            "flags": flags,
            "text": text,
        })
        log.info(
            "QA doan %d: raw %.1fs fit %.1fs post %.1fs slot %.1fs drift=%.0f%% "
            "rate_iter=%d atempo=%.2f vol=%s flags=%s | %s",
            i, tts_dur_raw, tts_dur, post_dur, max_avail, dr_pct,
            rate_iters, atempo_applied,
            f"{mean_db:.1f}dB" if mean_db is not None else "?",
            ",".join(flags) or "ok",
            text[:60],
        )

    if not clips:
        raise RuntimeError("Khong co doan thoai hop le de long tieng")

    # Chi cat overlap neu van du cho audio (khong ep 2x)
    for i in range(len(cue_segs) - 1):
        limit = float(cue_segs[i + 1]["start"]) - 0.02
        tts_dur = probe_duration(clips[i][2])
        min_end = float(cue_segs[i]["start"]) + (tts_dur / max_stretch)
        if float(cue_segs[i]["end"]) > limit:
            cue_segs[i]["end"] = round(max(min_end, min(float(cue_segs[i]["end"]), limit + 0.05)), 3)
            s0, _, cpath = clips[i]
            clips[i] = (s0, float(cue_segs[i]["end"]), cpath)

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={total_dur:.3f}"]
    for _, _, clip in clips:
        cmd += ["-i", str(clip)]

    filters = []
    mix_inputs = ["[0:a]"]
    fade_s = SEGMENT_CROSSFADE_MS / 1000.0
    for i, (start, play_end, clip_path) in enumerate(clips):
        slot = max(0.12, play_end - start)
        tts_dur = probe_duration(clip_path)
        label = f"a{i}"
        fit = _fit_to_slot_filter(
            f"[{i + 1}:a]", tts_dur, slot, f"f{i}",
            max_stretch=TTS_ATEMPO_MAX,
            natural=True,
        )
        fade_out_st = max(0.0, tts_dur - fade_s)
        delay_ms = int(round(start * 1000))
        filters.append(
            f"{fit};[f{i}]afade=t=in:st=0:d={fade_s:.4f},"
            f"afade=t=out:st={fade_out_st:.4f}:d={fade_s:.4f},"
            f"volume=1.0,adelay={delay_ms}|{delay_ms}[{label}]"
        )
        mix_inputs.append(f"[{label}]")

    n = len(mix_inputs)
    filters.append(
        f"{''.join(mix_inputs)}amix=inputs={n}:duration=longest:dropout_transition=0[aout]"
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


def auto_script(video_path: str | Path, work_dir: str | Path, cfg_auto: dict):
    from . import transcribe
    return transcribe.auto_script(video_path, work_dir, cfg_auto)
