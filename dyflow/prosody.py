"""Doc nhip / nang luong / cam xuc tu audio goc -> tham so Edge TTS an toan.

Muc tieu:
1) Khop giay/nhip slot goc (rate fit) de dub + sub khong lech.
2) Dieu chinh pitch/volume (+ rate nhe) theo cam xuc giong goc.
3) Moi gia tri nam trong khoang Edge TTS chap nhan (tranh NoAudioReceived).
"""
from __future__ import annotations

import math
import re
import struct
import wave
from collections import Counter
from pathlib import Path
from typing import Any

from .utils import log, run

# Gioi han Edge TTS — rate thap de tranh doc qua nhanh (truoc +40% bi chipmunk)
_RATE_MIN, _RATE_MAX = -15, 12
_PITCH_MIN, _PITCH_MAX = -12, 14
_VOL_MIN, _VOL_MAX = -12, 20
# Rate chi dung de fit slot (uu tien keo soft_slot, khong ep toc)
_RATE_FIT_MAX = 8

_EXCITE_RE = re.compile(r"[!！？?]{1,}|啊+|呀+|哇+|哈+|呵+|嘿+|！{2,}")
_SOFT_RE = re.compile(r"[.…。]{2,}|呢$|吧$|嘛$|…")


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    mid = len(s) // 2
    if len(s) % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _parse_signed(s: str) -> float:
    s = (s or "0").strip().replace("%", "").replace("Hz", "").replace("hz", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def safe_edge_rate(pct: float, *, hi: float | None = None) -> str:
    top = _RATE_MAX if hi is None else float(hi)
    return f"{int(round(_clamp(pct, _RATE_MIN, top))):+d}%"


def safe_edge_pitch(hz: float) -> str:
    return f"{int(round(_clamp(hz, _PITCH_MIN, _PITCH_MAX))):+d}Hz"


def safe_edge_volume(pct: float) -> str:
    return f"{int(round(_clamp(pct, _VOL_MIN, _VOL_MAX))):+d}%"


def sanitize_edge_cfg(cfg: dict, *, rate_hi: float | None = None) -> dict:
    """Ep rate/pitch/volume ve dung format Edge; khong bao gio de gia tri hong."""
    out = dict(cfg)
    out["rate"] = safe_edge_rate(_parse_signed(str(cfg.get("rate", "+0%"))), hi=rate_hi)
    out["pitch"] = safe_edge_pitch(_parse_signed(str(cfg.get("pitch", "+0Hz"))))
    out["volume"] = safe_edge_volume(_parse_signed(str(cfg.get("volume", "+0%"))))
    voice = str(cfg.get("voice") or "").strip()
    if voice:
        out["voice"] = voice
    return out


def normalize_segment_timings(
    segments: list[dict],
    video_dur: float | None = None,
    *,
    min_dur: float = 0.18,
    min_gap: float = 0.04,
) -> list[dict]:
    """1 luot check timestamp: clamp, chong chong, dam bao end > start.

    Sub + dub dung chung mang nay -> khong lech giay.
    """
    if not segments:
        return []
    vd = float(video_dur) if video_dur and video_dur > 0 else None
    cleaned: list[dict] = []
    for seg in segments:
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if vd is not None:
            start = _clamp(start, 0.0, max(0.0, vd - min_dur))
            end = _clamp(end, 0.0, vd)
        if end < start + min_dur:
            end = start + min_dur
            if vd is not None and end > vd:
                end = vd
                start = max(0.0, end - min_dur)
        row = {**seg, "start": round(start, 3), "end": round(end, 3), "text": text}
        cleaned.append(row)

    if not cleaned:
        return []

    cleaned.sort(key=lambda s: (float(s["start"]), float(s["end"])))
    out: list[dict] = []
    for seg in cleaned:
        start = float(seg["start"])
        end = float(seg["end"])
        if out:
            prev_end = float(out[-1]["end"])
            if start < prev_end + min_gap:
                start = prev_end + min_gap
            if end < start + min_dur:
                end = start + min_dur
            if vd is not None and end > vd:
                end = vd
                if end < start + min_dur:
                    # het cho trong video — bo doan
                    continue
                start = min(start, end - min_dur)
        out.append({**seg, "start": round(start, 3), "end": round(end, 3)})
    return out


def _ensure_mono_wav(video: Path, work_dir: Path) -> Path:
    wav = work_dir / "audio16k.wav"
    if wav.exists() and wav.stat().st_size > 1000:
        return wav
    work_dir.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav),
    ])
    return wav


def _rms_slice(wav_path: Path, start: float, end: float) -> float:
    start = max(0.0, float(start))
    end = max(start + 0.05, float(end))
    try:
        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate() or 16000
            nch = wf.getnchannels() or 1
            sw = wf.getsampwidth() or 2
            nframes = wf.getnframes()
            i0 = int(start * rate)
            i1 = min(nframes, int(end * rate))
            if i1 <= i0:
                return 0.0
            wf.setpos(i0)
            raw = wf.readframes(i1 - i0)
    except Exception as e:  # noqa: BLE001
        log.debug("prosody wav slice fail: %s", e)
        return 0.0

    if sw != 2 or not raw:
        return 0.0
    n_samp = len(raw) // 2
    try:
        samples = struct.unpack(f"<{n_samp}h", raw[: n_samp * 2])
    except struct.error:
        return 0.0
    if nch > 1:
        samples = samples[0::nch]
    if not samples:
        return 0.0
    step = max(1, len(samples) // 8000)
    acc = 0.0
    n = 0
    for i in range(0, len(samples), step):
        v = samples[i] / 32768.0
        acc += v * v
        n += 1
    return math.sqrt(acc / n) if n else 0.0


def _source_units(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    if cjk >= max(2, len(text) // 3):
        return max(1, cjk)
    words = re.findall(r"[A-Za-z0-9']+", text)
    return max(1, len(words) or max(1, len(text) // 4))


def _est_tts_seconds(text: str, rate_pct: float = 0.0) -> float:
    """Uoc luong thoi luong Edge TTS (khong goi API)."""
    t = (text or "").strip()
    if not t:
        return 0.2
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t))
    if cjk >= max(2, len(t) // 3):
        # ~5.2 ky tu CJK / giay o +0%
        base = max(0.25, cjk / 5.2)
    else:
        chars = len(re.sub(r"\s+", " ", t))
        # Tieng Viet (dau thanh) doc cham hon English ~14.5
        has_vi = bool(re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", t, re.I))
        cps = 11.0 if has_vi else 14.0
        base = max(0.25, chars / cps)
    speed = 1.0 + (rate_pct / 100.0)
    speed = max(0.7, speed)
    return base / speed


def _text_emotion_bias(text: str) -> tuple[float, float, float]:
    t = text or ""
    rate = pitch = vol = 0.0
    if _EXCITE_RE.search(t):
        rate += 5
        pitch += 2
        vol += 4
    if "?" in t or "？" in t:
        pitch += 2
        rate += 1
    if _SOFT_RE.search(t):
        rate -= 3
        pitch -= 1
        vol -= 2
    return rate, pitch, vol


def _emotion_label(pace: float, energy: float) -> str:
    if pace >= 1.25 and energy >= 1.12:
        return "excited"
    if pace >= 1.15:
        return "urgent"
    if energy >= 1.22:
        return "emphatic"
    if pace <= 0.8 and energy <= 0.88:
        return "soft"
    if pace <= 0.85:
        return "calm"
    return "neutral"


def analyze_segments(
    segments: list[dict],
    *,
    source_video: str | Path | None,
    work_dir: str | Path,
    base_rate: str = "+0%",
    base_pitch: str = "+0Hz",
    base_volume: str = "+0%",
    video_dur: float | None = None,
) -> list[dict]:
    """Check 1 luot + gan tts_rate/pitch/volume: uu tien khop slot, sau do cam xuc."""
    segs = normalize_segment_timings(segments, video_dur)
    if not segs:
        return []

    wav: Path | None = None
    if source_video:
        try:
            wav = _ensure_mono_wav(Path(source_video), Path(work_dir))
        except Exception as e:  # noqa: BLE001
            log.warning("prosody: khong trich audio (%s) — van tinh rate fit theo chu", e)

    feats: list[dict[str, Any]] = []
    for seg in segs:
        start = float(seg["start"])
        end = float(seg["end"])
        dur = max(0.12, end - start)
        src = seg.get("source_text") or seg.get("text") or ""
        dst = seg.get("text") or ""
        units = _source_units(src) or _source_units(dst)
        rate_u = units / dur
        rms = _rms_slice(wav, start, end) if wav else 0.0
        feats.append({
            "dur": dur,
            "rate_u": rate_u,
            "rms": rms,
            "src": src,
            "dst": dst,
        })

    med_rate = _median([f["rate_u"] for f in feats if f["rate_u"] > 0]) or 1.0
    med_rms = _median([f["rms"] for f in feats if f["rms"] > 0]) or 0.05

    out: list[dict] = []
    labels: list[str] = []

    for i, (seg, f) in enumerate(zip(segs, feats)):
        # Giong nhan vat (cast) ghi de base toan clip
        seg_br = _parse_signed(seg.get("speaker_rate") or base_rate)
        seg_bp = _parse_signed(seg.get("speaker_pitch") or base_pitch)
        seg_bv = _parse_signed(seg.get("speaker_volume") or base_volume)
        # Base rate UI de dang lam doc nhanh — child duoc phep +8
        comfort_base = _clamp(seg_br, -5, 8)
        slot = f["dur"]
        start = float(seg["start"])
        next_start = float(segs[i + 1]["start"]) if i + 1 < len(segs) else (video_dur or (float(seg["end"]) + 1.2))
        # Toi da cho phep den truoc doan sau (uu tien keo dai, khong tang toc)
        max_avail = max(0.12, next_start - start - 0.05)
        gap = max(0.0, next_start - float(seg["end"]))

        pace = (f["rate_u"] / med_rate) if med_rate > 0 else 1.0
        energy = (f["rms"] / med_rms) if (med_rms > 0 and f["rms"] > 0) else 1.0
        pace = _clamp(pace, 0.55, 1.8)
        energy = _clamp(energy, 0.45, 2.0)

        # Cam xuc: chu yeu pitch/volume; rate chi lech nhe
        rate_emo = _clamp((pace - 1.0) * 10.0, -6, 5)
        pitch_emo = _clamp((energy - 1.0) * 7.0 + (pace - 1.0) * 2.0, -8, 10)
        vol_emo = _clamp((energy - 1.0) * 14.0, -8, 12)
        tr, tp, tv = _text_emotion_bias(f["src"])
        tr2, tp2, tv2 = _text_emotion_bias(f["dst"])
        rate_emo += tr * 0.35 + tr2 * 0.2
        pitch_emo += tp * 0.6 + tp2 * 0.35
        vol_emo += tv * 0.6 + tv2 * 0.35

        # Doc o toc do thoai mai truoc, roi moi tinh soft_slot
        rate_final = _clamp(comfort_base + rate_emo, _RATE_MIN, _RATE_MAX)
        natural = _est_tts_seconds(f["dst"], rate_pct=rate_final)

        # soft_slot: lay gap de chua het cau o toc do thoai mai
        soft_slot = min(max(slot, natural * 1.02), max_avail)
        # Neu van thieu cho: tang rate RAT NHE (toi da +8%), roi keo soft_slot lai
        rate_fit = 0.0
        if natural > max_avail * 0.98:
            need = natural / max(max_avail * 0.98, 0.12)
            rate_fit = _clamp((need - 1.0) * 100.0, 0, _RATE_FIT_MAX)
            rate_final = _clamp(rate_final + rate_fit, _RATE_MIN, _RATE_MAX)
            natural = _est_tts_seconds(f["dst"], rate_pct=rate_final)
            soft_slot = min(max(slot, natural * 1.01), max_avail)
        elif natural < slot * 0.55:
            # Qua ngan so voi khung goc: giam toc nhe cho kip nhip (khong de trong qua lau)
            slow = _clamp((natural / max(slot * 0.75, 0.12) - 1.0) * 100.0, -10, 0)
            rate_final = _clamp(rate_final + slow, _RATE_MIN, _RATE_MAX)
            natural = _est_tts_seconds(f["dst"], rate_pct=rate_final)
            soft_slot = min(max(slot, natural * 1.02), max_avail)

        pitch_final = seg_bp + pitch_emo
        vol_final = seg_bv + vol_emo
        est = natural

        emo = _emotion_label(pace, energy)
        tts_rate = safe_edge_rate(rate_final)
        tts_pitch = safe_edge_pitch(pitch_final)
        tts_volume = safe_edge_volume(vol_final)

        display_end = float(seg["end"])
        labels.append(
            f"{emo} rate={tts_rate} pitch={tts_pitch} vol={tts_volume} "
            f"slot={slot:.2f}s soft={soft_slot:.2f}s est={est:.2f}s "
            f"pace={pace:.2f} energy={energy:.2f}"
        )
        out.append({
            **seg,
            "emotion": emo,
            "tts_rate": tts_rate,
            "tts_pitch": tts_pitch,
            "tts_volume": tts_volume,
            "soft_slot": round(soft_slot, 3),
            "display_end": round(display_end, 3),
            "prosody": {
                "pace": round(pace, 3),
                "energy": round(energy, 3),
                "rms": round(f["rms"], 5),
                "rate_fit": round(rate_fit, 2),
                "est_sec": round(est, 3),
                "soft_slot": round(soft_slot, 3),
                "max_avail": round(max_avail, 3),
            },
        })

    cnt = Counter(s.get("emotion", "?") for s in out)
    log.info(
        "Prosody check 1 luot: %d doan | %s | base=%s/%s/%s",
        len(out),
        ", ".join(f"{k}={v}" for k, v in sorted(cnt.items())),
        base_rate, base_pitch, base_volume,
    )
    for i, line in enumerate(labels[:8]):
        log.info("  seg[%d] %s", i, line)
    if len(labels) > 8:
        log.info("  ... +%d doan", len(labels) - 8)
    return out
