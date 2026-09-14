"""TTS dub: rate-matching hoi tu (Edge) + loudnorm 2-pass + atempo nhe."""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .dub_constants import (
    DUB_ENCODER_PAD_SEC,
    DUB_MICRO_OVERLAP_SEC,
    LOUDNORM_LRA,
    LOUDNORM_MIN_DUR,
    LOUDNORM_SAMPLE_RATE,
    LOUDNORM_TARGET_I,
    LOUDNORM_TRUE_PEAK,
    TTS_ATEMPO_FLAG_MIN,
    TTS_ATEMPO_MAX,
    TTS_ATEMPO_SEVERE_MAX,
    TTS_EDGE_RATE_MAX_PCT,
    TTS_EDGE_RATE_MIN_PCT,
    TTS_OPENAI_API_SPEED_MAX,
    TTS_OPENAI_API_SPEED_MIN,
    TTS_OPENAI_RATE_MAX_PCT,
    TTS_OPENAI_RATE_MIN_PCT,
    TTS_OPENAI_SPEED_MAX,
    TTS_OPENAI_SPEED_MIN,
    TTS_RATE_MATCH_FAIL_RATIO,
    TTS_RATE_MATCH_TOLERANCE,
    TTS_RATE_MAX_ITERS,
)
from .utils import log, probe_duration, run


@dataclass
class TtsMatchResult:
    tts_dur: float
    slot_dur: float
    rate_pct: float
    atempo: float
    post_dur: float
    iters: int
    drift_ratio: float
    needs_shorten: bool = False
    flags: list[str] = field(default_factory=list)


def _parse_rate_pct(rate: str) -> float:
    s = (rate or "0").strip().replace("%", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clamp_rate(pct: float) -> float:
    return max(TTS_EDGE_RATE_MIN_PCT, min(TTS_EDGE_RATE_MAX_PCT, pct))


def _clamp_openai_rate_pct(pct: float) -> float:
    return max(TTS_OPENAI_RATE_MIN_PCT, min(TTS_OPENAI_RATE_MAX_PCT, pct))


def rate_to_openai_speed(rate: str | float | None, *, openai_fit: bool = False) -> float:
    """Doi Edge rate (+N%) sang OpenAI speed.

    openai_fit chi con de tuong thich: clamp nhip tu nhien (0.95–1.08).
    Khong mo lai 0.25–4.0 — giong se khong deu.
    """
    if isinstance(rate, (int, float)):
        pct = float(rate)
    else:
        pct = _parse_rate_pct(str(rate or "+0%"))
    pct = _clamp_openai_rate_pct(pct) if openai_fit else _clamp_rate(pct)
    return max(TTS_OPENAI_SPEED_MIN, min(TTS_OPENAI_SPEED_MAX, 1.0 + pct / 100.0))


def compute_target_openai_speed(current_speed: float, tts_dur: float, slot_dur: float) -> float:
    """Tinh speed OpenAI moi — chi tua khi TTS dai hon slot, khong keo cham."""
    slot = max(0.12, float(slot_dur))
    tts = max(0.05, float(tts_dur))
    cur = max(TTS_OPENAI_SPEED_MIN, min(TTS_OPENAI_SPEED_MAX, float(current_speed)))
    need = cur * tts / slot
    if need <= cur:
        return cur
    return max(TTS_OPENAI_SPEED_MIN, min(TTS_OPENAI_SPEED_MAX, need))


def openai_speed_to_rate_str(speed: float) -> str:
    pct = _clamp_openai_rate_pct((float(speed) - 1.0) * 100.0)
    return f"{pct:+.0f}%"


def clamp_openai_api_speed(speed: float) -> float:
    """Clamp cuoi truoc khi goi API (0.25–4.0). Dub da clamp 0.85–1.22 truoc do."""
    return max(TTS_OPENAI_API_SPEED_MIN, min(TTS_OPENAI_API_SPEED_MAX, float(speed)))


def resolve_openai_speed(cfg: dict | None) -> float:
    """Uu tien openai_speed (float) — KHONG doc rate da bi sanitize_edge_cfg (+12%)."""
    cfg = cfg or {}
    raw = cfg.get("openai_speed")
    if raw is not None and raw != "":
        try:
            return max(TTS_OPENAI_SPEED_MIN, min(TTS_OPENAI_SPEED_MAX, float(raw)))
        except (TypeError, ValueError):
            pass
    return rate_to_openai_speed(cfg.get("rate") or "+0%", openai_fit=True)


def drift_ratio(tts_dur: float, slot_dur: float) -> float:
    slot = max(0.12, float(slot_dur))
    return abs(float(tts_dur) - slot) / slot


def duration_mismatch_flags(tts_dur: float, slot_dur: float) -> tuple[list[str], bool]:
    """Phan loai lech thoi luong: qua_dai (can rut gon) vs qua_ngan (mapping/slot thua)."""
    slot = max(0.12, float(slot_dur))
    tts = float(tts_dur)
    ratio = TTS_RATE_MATCH_FAIL_RATIO
    flags: list[str] = []
    needs_shorten = False
    if tts > slot * (1 + ratio):
        flags.append("qua_dai")
        needs_shorten = True
    elif tts < slot * (1 - ratio):
        flags.append("qua_ngan")
    return flags, needs_shorten


def compute_target_rate_pct(current_rate_pct: float, tts_dur: float, slot_dur: float) -> float:
    """Tinh rate Edge moi — chi tua khi TTS dai hon slot, khong keo cham."""
    slot = max(0.12, float(slot_dur))
    tts = max(0.05, float(tts_dur))
    cur_factor = 1.0 + current_rate_pct / 100.0
    new_factor = tts * cur_factor / slot
    if new_factor <= cur_factor:
        return current_rate_pct
    return _clamp_rate((new_factor - 1.0) * 100.0)


def tts_rate_converge(
    i: int,
    *,
    slot_dur: float,
    tts_dur: float,
    seg_cfg: dict,
    clip: Path,
    tts_once,
    prosody,
    segment_gap: float = 0.4,
    openai: bool = False,
) -> tuple[dict, float, TtsMatchResult]:
    """Lap toi da TTS_RATE_MAX_ITERS lan (Edge --rate / OpenAI speed). Cung constants."""
    slot = max(0.12, float(slot_dur))
    cur_dur = float(tts_dur)
    iters = 0
    flags: list[str] = []
    best_dur = cur_dur
    best_cfg = dict(seg_cfg)
    best_dr = drift_ratio(best_dur, slot)
    bak = clip.with_name(clip.stem + "._best.mp3")
    if clip.exists():
        shutil.copy2(clip, bak)

    for attempt in range(TTS_RATE_MAX_ITERS):
        # Chi tua khi TTS dai hon slot. Slot thua = im lang, khong keo cham.
        if cur_dur <= slot * (1.0 + TTS_RATE_MATCH_TOLERANCE):
            break
        dr = drift_ratio(cur_dur, slot)
        if openai:
            cur_speed = resolve_openai_speed(seg_cfg)
            need_speed = compute_target_openai_speed(cur_speed, cur_dur, slot)
            if need_speed <= cur_speed + 0.02:
                break
            newer = {
                **seg_cfg,
                "rate": openai_speed_to_rate_str(need_speed),
                "openai_speed": round(need_speed, 3),
            }
            log_speed = need_speed
        else:
            cur_rate = _parse_rate_pct(str(seg_cfg.get("rate", "+0%")))
            need = compute_target_rate_pct(cur_rate, cur_dur, slot)
            if need <= cur_rate + 0.5:
                break
            newer = prosody.sanitize_edge_cfg(
                {**seg_cfg, "rate": f"{need:+.0f}%"},
                rate_hi=TTS_EDGE_RATE_MAX_PCT,
            )
            log_speed = rate_to_openai_speed(newer.get("rate"))
        log.info(
            "Doan %d rate-fit iter %d: TTS %.2fs slot %.2fs drift %.0f%% rate %s -> %s (speed=%.2f)",
            i, attempt + 1, cur_dur, slot, dr * 100,
            seg_cfg.get("rate"), newer.get("rate"), log_speed,
        )
        if not tts_once(newer):
            flags.append("tts_fail")
            if bak.exists():
                shutil.copy2(bak, clip)
                cur_dur = best_dur
                seg_cfg = best_cfg
            break
        new_dur = probe_duration(clip)
        new_dr = drift_ratio(new_dur, slot)
        iters += 1
        if new_dr + 0.005 < best_dr:
            shutil.copy2(clip, bak)
            best_dur = new_dur
            best_cfg = dict(newer)
            best_dr = new_dr
            seg_cfg = newer
            cur_dur = new_dur
        else:
            log.info(
                "Doan %d rate-fit iter %d khong tot hon "
                "(%.2fs drift %.0f%% vs best %.2fs %.0f%%) — giu lan tot",
                i, attempt + 1, new_dur, new_dr * 100, best_dur, best_dr * 100,
            )
            if bak.exists():
                shutil.copy2(bak, clip)
            cur_dur = best_dur
            seg_cfg = best_cfg
            break
        time.sleep(min(segment_gap, 0.5))

    if bak.exists():
        bak.unlink(missing_ok=True)

    dr = drift_ratio(cur_dur, slot)
    mismatch, needs_shorten = duration_mismatch_flags(cur_dur, slot)
    flags.extend(mismatch)
    if "qua_dai" in mismatch:
        log.warning(
            "Doan %d van dai %.0f%% sau %d lan rate-fit — can rut gon cau dich",
            i, dr * 100, iters,
        )
    elif "qua_ngan" in mismatch:
        log.warning(
            "Doan %d van ngan %.0f%% sau %d lan rate-fit — co the mapping/slot thua",
            i, dr * 100, iters,
        )

    res = TtsMatchResult(
        tts_dur=cur_dur,
        slot_dur=slot,
        rate_pct=_parse_rate_pct(str(seg_cfg.get("rate", "+0%"))),
        atempo=1.0,
        post_dur=cur_dur,
        iters=iters,
        drift_ratio=dr,
        needs_shorten=needs_shorten,
        flags=flags,
    )
    return seg_cfg, cur_dur, res


# Ten cu — Edge va OpenAI dung chung tts_rate_converge.
edge_rate_converge = tts_rate_converge


def mild_atempo_for_residual(
    tts_dur: float,
    slot_dur: float,
    *,
    skip: bool = False,
    force: bool = False,
) -> float:
    """Atempo nhe sau rate-fit — chi tua (1.00-1.08), khong keo cham de lap slot.

    - Bo qua khi TTS ngan hon/bang slot.
    - Bo qua khi drift > 15% (can rut gon) tru force=True (pass cuoi, van chi tua).
    """
    if skip:
        return 1.0
    slot = max(0.12, float(slot_dur))
    tts = float(tts_dur)
    if tts <= slot * 1.005:
        return 1.0
    dr = drift_ratio(tts, slot)
    if dr > TTS_RATE_MATCH_FAIL_RATIO and not force:
        return 1.0
    tempo = min(TTS_ATEMPO_MAX, tts / slot)
    if tempo <= 1.0 + TTS_ATEMPO_FLAG_MIN / 2:
        return 1.0
    return max(1.0, tempo)


def _finite_loudness(val) -> bool:
    try:
        x = float(val)
    except (TypeError, ValueError):
        return False
    return math.isfinite(x)


def loudnorm_twopass(path: Path) -> float | None:
    """Two-pass loudnorm -> target LUFS. Tra mean_volume uoc luong (dB) neu do duoc."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < 100:
        return None
    dur = probe_duration(path)
    if dur > 0 and dur < LOUDNORM_MIN_DUR:
        log.info("Bo qua loudnorm %s (%.2fs < %.2fs)", path.name, dur, LOUDNORM_MIN_DUR)
        return None
    tmp = path.with_name(path.stem + "._ln2.mp3")
    try:
        p1 = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-i", str(path),
                "-af", f"loudnorm=I={LOUDNORM_TARGET_I}:TP={LOUDNORM_TRUE_PEAK}:"
                       f"LRA={LOUDNORM_LRA}:print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False,
        )
        blob = (p1.stderr or "") + (p1.stdout or "")
        m = re.search(r'\{\s*"input_i"\s*:[\s\S]*?\}\s*', blob)
        if not m:
            raise RuntimeError("loudnorm pass1 khong co JSON")
        stats = json.loads(m.group(0))
        if not _finite_loudness(stats.get("input_i")):
            log.info(
                "Bo qua loudnorm %s (measured_I=%s)",
                path.name, stats.get("input_i"),
            )
            tmp.unlink(missing_ok=True)
            return None
        run([
            "ffmpeg", "-y", "-i", str(path),
            "-af",
            f"loudnorm=I={LOUDNORM_TARGET_I}:TP={LOUDNORM_TRUE_PEAK}:LRA={LOUDNORM_LRA}:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true:print_format=none",
            "-ar", str(LOUDNORM_SAMPLE_RATE), "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(tmp),
        ])
        if tmp.exists() and tmp.stat().st_size > 100:
            tmp.replace(path)
        p2 = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-i", str(path),
                "-af", f"loudnorm=I={LOUDNORM_TARGET_I}:TP={LOUDNORM_TRUE_PEAK}:"
                       f"LRA={LOUDNORM_LRA}:print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False,
        )
        blob2 = (p2.stderr or "") + (p2.stdout or "")
        m2 = re.search(r'\{\s*"input_i"\s*:[\s\S]*?\}\s*', blob2)
        if m2:
            out_stats = json.loads(m2.group(0))
            val = out_stats.get("input_i", LOUDNORM_TARGET_I)
            if _finite_loudness(val):
                return float(val)
            return LOUDNORM_TARGET_I
        return LOUDNORM_TARGET_I
    except Exception as e:  # noqa: BLE001
        log.warning("loudnorm 2-pass %s loi: %s", path.name, e)
        tmp.unlink(missing_ok=True)
        return None


def apply_atempo_file(path: Path, tempo: float) -> None:
    if abs(tempo - 1.0) < 0.005:
        return
    path = Path(path)
    tmp = path.with_name(path.stem + "._at.mp3")
    chain = _atempo_chain(tempo)
    run([
        "ffmpeg", "-y", "-i", str(path),
        "-af", chain,
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(tmp),
    ])
    if tmp.exists() and tmp.stat().st_size > 100:
        tmp.replace(path)


def trim_audio_to_dur(path: Path, dur: float) -> None:
    """Cat audio ve dung slot (fade cuoi). Chi dung khi van dai sau rut gon + atempo."""
    path = Path(path)
    if not path.exists():
        return
    cur = probe_duration(path)
    limit = max(0.12, float(dur))
    if cur <= limit + 0.015:
        return
    fade = min(0.08, limit * 0.12)
    start_fade = max(0.0, limit - fade)
    tmp = path.with_name(path.stem + "._tr.mp3")
    run([
        "ffmpeg", "-y", "-i", str(path),
        "-t", f"{limit:.3f}",
        "-af", f"afade=t=out:st={start_fade:.3f}:d={fade:.3f}",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(tmp),
    ])
    if tmp.exists() and tmp.stat().st_size > 100:
        tmp.replace(path)


def strip_edge_silence(path: Path) -> None:
    """Cat im lang DAU/CUOI TTS. Khong cat pause giua cau (stop_periods an no)."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < 100:
        return
    before = probe_duration(path)
    backup = path.read_bytes()
    tmp = path.with_name(path.stem + "._sil.mp3")
    try:
        # areverse + start_periods = chi cat duoi file, khong an khoang lang giua cau.
        run([
            "ffmpeg", "-y", "-i", str(path),
            "-af",
            "silenceremove=start_periods=1:start_duration=0.04:start_threshold=-40dB:detection=peak,"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0.08:start_threshold=-38dB:detection=peak,"
            "areverse",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(tmp),
        ])
        if tmp.exists() and tmp.stat().st_size > 100:
            tmp.replace(path)
            after = probe_duration(path) if path.exists() else 0.0
            # Neu an mat >0.5s (loi noi, khong phai pad), tra file goc.
            if after + 0.5 < before:
                log.warning(
                    "Cat im lang %s an %.2fs -> %.2fs — giu file goc",
                    path.name, before, after,
                )
                path.write_bytes(backup)
    except Exception as e:  # noqa: BLE001
        log.warning("Cat im lang %s loi: %s", path.name, e)
        tmp.unlink(missing_ok=True)
        if not path.exists() or path.stat().st_size < 100:
            path.write_bytes(backup)


def close_file_to_slot(
    path: Path,
    slot: float,
    *,
    start: float,
    next_start: float,
    atempo_already: float = 1.0,
    cap: float = TTS_ATEMPO_SEVERE_MAX,
    allow_speech_trim: bool = False,
    allow_micro_overlap: bool = False,
    label: str = "",
) -> dict:
    """Khong de TTS de cau sau: im lang cuoi -> atempo toi cap -> cat pad encoder.

    Khong cat loi noi. Neu van tran: micro-overlap <=250ms (neu bat) hoac overflow.
    """
    from .dub_timing import overlap_close_plan, plan_placement

    path = Path(path)
    if allow_speech_trim:
        log.warning("%sclose_slot: allow_speech_trim da cam — khong cat loi noi", f"{label} " if label else "")
    hard = max(0.12, float(slot))
    strip_edge_silence(path)
    dur = probe_duration(path) if path.exists() else hard
    plan = overlap_close_plan(
        tts_dur=dur,
        slot=hard,
        atempo_already=atempo_already,
        cap=cap,
        encoder_pad=DUB_ENCODER_PAD_SEC,
        allow_speech_trim=False,
        allow_micro_overlap=allow_micro_overlap,
        micro_overlap_sec=DUB_MICRO_OVERLAP_SEC,
    )
    tempo = float(plan.get("tempo") or 1.0)
    if tempo > 1.005:
        apply_atempo_file(path, tempo)
        dur = probe_duration(path) if path.exists() else dur / tempo
    # Chi cat pad encoder (im lang mux). Khong speech_trim.
    if plan.get("pad_trim"):
        trim_audio_to_dur(path, hard)
        dur = probe_duration(path) if path.exists() else hard
        place = plan_placement(
            source_start=start, source_end=start,
            next_start=next_start, tts_dur=dur,
        )
        leftover = max(0.0, dur - hard)
        if place["would_overlap"] and leftover <= DUB_ENCODER_PAD_SEC + 0.03:
            trim_audio_to_dur(path, max(0.12, hard - 0.03))
            dur = probe_duration(path) if path.exists() else hard
    place = plan_placement(
        source_start=start, source_end=start,
        next_start=next_start, tts_dur=dur,
    )
    still = bool(place["would_overlap"])
    micro = bool(plan.get("micro_overlap"))
    tag = f"{label} " if label else ""
    log.info(
        "%sclose_slot %s action=%s tempo=%.3f pad_trim=%s micro=%s dur=%.2fs "
        "hard=%.2fs overlap=%s",
        tag, path.name, plan.get("action"), tempo, plan.get("pad_trim"),
        micro, dur, hard, still,
    )
    return {
        "dur": dur,
        "tempo_applied": tempo if tempo > 1.005 else 1.0,
        "action": plan.get("action"),
        "pad_trim": bool(plan.get("pad_trim")),
        "speech_trim": False,
        "micro_overlap": micro,
        "still_overlap": still and not micro,
        "fits": (not still) or micro,
    }


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
