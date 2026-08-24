"""TTS dub: rate-matching hoi tu (Edge) + loudnorm 2-pass + atempo nhe."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .dub_constants import (
    LOUDNORM_LRA,
    LOUDNORM_SAMPLE_RATE,
    LOUDNORM_TARGET_I,
    LOUDNORM_TRUE_PEAK,
    TTS_ATEMPO_MAX,
    TTS_ATEMPO_MIN,
    TTS_EDGE_RATE_MAX_PCT,
    TTS_EDGE_RATE_MIN_PCT,
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
    """Tinh rate Edge moi de duration gan slot (khong dung ffmpeg)."""
    slot = max(0.12, float(slot_dur))
    tts = max(0.05, float(tts_dur))
    cur_factor = 1.0 + current_rate_pct / 100.0
    new_factor = tts * cur_factor / slot
    return _clamp_rate((new_factor - 1.0) * 100.0)


def edge_rate_converge(
    i: int,
    *,
    slot_dur: float,
    tts_dur: float,
    seg_cfg: dict,
    clip: Path,
    tts_once,
    prosody,
    segment_gap: float = 0.4,
) -> tuple[dict, float, TtsMatchResult]:
    """Lap toi da TTS_RATE_MAX_ITERS lan bang --rate Edge (khong ffmpeg)."""
    slot = max(0.12, float(slot_dur))
    cur_dur = float(tts_dur)
    iters = 0
    flags: list[str] = []

    for attempt in range(TTS_RATE_MAX_ITERS):
        dr = drift_ratio(cur_dur, slot)
        if dr <= TTS_RATE_MATCH_TOLERANCE:
            break
        cur_rate = _parse_rate_pct(str(seg_cfg.get("rate", "+0%")))
        need = compute_target_rate_pct(cur_rate, cur_dur, slot)
        if abs(need - cur_rate) < 1.0:
            break
        newer = prosody.sanitize_edge_cfg(
            {**seg_cfg, "rate": f"{need:+.0f}%"},
            rate_hi=TTS_EDGE_RATE_MAX_PCT,
        )
        log.info(
            "Doan %d rate-fit iter %d: TTS %.2fs slot %.2fs drift %.0f%% rate %s -> %s",
            i, attempt + 1, cur_dur, slot, dr * 100,
            seg_cfg.get("rate"), newer.get("rate"),
        )
        if not tts_once(newer):
            flags.append("tts_fail")
            break
        seg_cfg = newer
        cur_dur = probe_duration(clip)
        iters += 1
        time.sleep(min(segment_gap, 0.5))

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


def mild_atempo_for_residual(
    tts_dur: float,
    slot_dur: float,
    *,
    skip: bool = False,
) -> float:
    """Atempo nhe sau rate-fit (0.92-1.08).

    - Ap dung khi 0.5% < drift <= 15% (bao gom dead zone 8-15%).
    - Bo qua khi drift > 15% (can rut gon, vd #7 slot 1.32s) hoac skip=True.
    """
    if skip:
        return 1.0
    slot = max(0.12, float(slot_dur))
    dr = drift_ratio(tts_dur, slot)
    if dr <= 0.005:
        return 1.0
    if dr > TTS_RATE_MATCH_FAIL_RATIO:
        return 1.0
    tempo = max(TTS_ATEMPO_MIN, min(TTS_ATEMPO_MAX, float(tts_dur) / slot))
    if abs(tempo - 1.0) < 0.005:
        return 1.0
    return tempo


def loudnorm_twopass(path: Path) -> float | None:
    """Two-pass loudnorm -> target LUFS. Tra mean_volume uoc luong (dB) neu do duoc."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < 100:
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
            return float(out_stats.get("input_i", LOUDNORM_TARGET_I))
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
