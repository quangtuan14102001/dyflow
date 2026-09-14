"""Gan giong TTS theo nhan vat trong 1 clip (gioi tinh + tuoi).

Khong xuat nhieu ban. Moi doan thoai nhan speaker_id + preset Edge/OpenAI
khop tre em / thanh nien / nguoi lon / lon tuoi.
"""
from __future__ import annotations

import json
import math
import os
import struct
import wave
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .utils import log
from .voices import (
    AGE_ADULT,
    AGE_CHILD,
    AGE_MATURE,
    AGE_ORDER,
    AGE_YOUNG,
    DEFAULT_LANG,
    PROFILE_CHILD,
    PROFILE_FEMALE_ADULT,
    PROFILE_MALE_ADULT,
    classify_f0_profile,
    infer_voice_id,
    resolve_voice_profile,
    tts_fields,
)

MIN_PITCH_SEG_SEC = 1.0
_VOICE_LOCK_KEYS = (
    "voice_profile",
    "tts_voice_name",
    "voice_id",
    "tts_voice",
    "openai_voice",
    "speaker_rate",
    "speaker_pitch",
    "speaker_volume",
    "median_f0_hz",
    "gender",
    "age",
)

_VALID_GENDER = {"male", "female"}
_AGE_ALIAS = {
    "child": AGE_CHILD, "kid": AGE_CHILD, "children": AGE_CHILD,
    "boy": AGE_CHILD, "girl": AGE_CHILD,
    "young": AGE_YOUNG, "teen": AGE_YOUNG, "youth": AGE_YOUNG,
    "adult": AGE_ADULT, "middle": AGE_ADULT,
    "mature": AGE_MATURE, "old": AGE_MATURE, "elder": AGE_MATURE,
    "senior": AGE_MATURE, "aged": AGE_MATURE,
}
_CHUNK = 90


def _norm_age(raw: str) -> str:
    a = (raw or "").strip().lower()
    return _AGE_ALIAS.get(a, AGE_ADULT if a not in AGE_ORDER else a)


def assign_cast(
    segments: list[dict],
    source_video: str | Path | None,
    work_dir: str | Path,
    *,
    lang: str = DEFAULT_LANG,
    fallback_voice: str = "",
    fallback_cfg: dict | None = None,
) -> list[dict]:
    """Gan speaker + TTS cho tung doan. Loi -> giu 1 giong du phong."""
    if not segments:
        return segments
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    lang = (lang or DEFAULT_LANG).strip().lower()
    fallback_id = infer_voice_id(
        fallback_voice or ((fallback_cfg or {}).get("voice_id") or "")
        or ((fallback_cfg or {}).get("voice") or "male_adult"),
    )

    feats = _acoustic_hints(segments, source_video, work_dir)
    cast_rows = _llm_cast(segments, feats, lang=lang)
    if not cast_rows:
        cast_rows = _acoustic_cast(segments, feats, fallback_id)

    offsets = _same_voice_offsets(cast_rows)
    out: list[dict] = []
    used: Counter[str] = Counter()
    for seg, row, feat in zip(segments, cast_rows, feats):
        vid = infer_voice_id(
            row.get("voice_id") or "",
            gender=str(row.get("gender") or ""),
            age=str(row.get("age") or ""),
        )
        try:
            fields = tts_fields(lang, vid)
        except ValueError:
            fields = tts_fields(lang, fallback_id)
        sid = str(row.get("speaker_id") or fields["voice_id"]).strip() or fields["voice_id"]
        hz_off = int(offsets.get(sid, 0))
        pitch = fields["speaker_pitch"]
        if hz_off:
            pitch = _add_hz(pitch, hz_off)
        used[f"{sid}:{fields['gender']}/{fields['age']}"] += 1
        out.append({
            **seg,
            "speaker_id": sid,
            "speaker_role": str(row.get("role") or sid),
            "speaker_relation": str(row.get("speaker_relation") or seg.get("speaker_relation") or ""),
            "pronoun_pair_used": str(row.get("pronoun_pair_used") or seg.get("pronoun_pair_used") or ""),
            "voice_id": fields["voice_id"],
            "tts_voice": fields["tts_voice"],
            "openai_voice": fields["openai_voice"],
            "speaker_rate": fields["speaker_rate"],
            "speaker_pitch": pitch,
            "speaker_volume": fields["speaker_volume"],
            "gender": fields["gender"],
            "age": fields["age"],
            "f0_hz": feat.get("f0_hz"),
        })

    summary = ", ".join(f"{k}×{n}" for k, n in used.most_common())
    log.info("Cast %d nhan vat / %d doan: %s", len(used), len(out), summary)
    try:
        (work_dir / "cast.json").write_text(
            json.dumps({
                "fallback": fallback_id,
                "speakers": [
                    {
                        "speaker_id": s.get("speaker_id"),
                        "role": s.get("speaker_role"),
                        "voice_id": s.get("voice_id"),
                        "start": s.get("start"),
                        "end": s.get("end"),
                        "text": s.get("text"),
                        "speaker_relation": s.get("speaker_relation"),
                        "pronoun_pair_used": s.get("pronoun_pair_used"),
                        "f0_hz": s.get("f0_hz"),
                    }
                    for s in out
                ],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (work_dir / "segments_vi.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Khong ghi cast.json: %s", e)
    return out


def _llm_gender_age(sample: dict) -> tuple[str | None, str | None]:
    """Gioi/tuoi tu assign_cast (voice_id hoac gender/age)."""
    vid = str(sample.get("voice_id") or "").strip().lower()
    g = str(sample.get("gender") or "").strip().lower()
    a = str(sample.get("age") or "").strip().lower()
    a = _AGE_ALIAS.get(a, a)
    if "_" in vid:
        left, right = vid.split("_", 1)
        if left in _VALID_GENDER:
            g = g or left
        if right in AGE_ORDER:
            a = a or right
    if g not in _VALID_GENDER:
        g = None
    if a not in AGE_ORDER:
        a = None
    return g, a


def _f0_gender(profile: str | None) -> str | None:
    if profile == PROFILE_MALE_ADULT:
        return "male"
    if profile == PROFILE_FEMALE_ADULT:
        return "female"
    return None


def _stamp_voice_profile(age: str, gender: str) -> str:
    if age == AGE_CHILD:
        return PROFILE_CHILD
    if gender == "female":
        return PROFILE_FEMALE_ADULT
    return PROFILE_MALE_ADULT


def _majority_llm_gender_age(segs: list[dict]) -> tuple[str | None, str | None]:
    """1 speaker_id: lay gioi/tuoi xuat hien nhieu nhat (khong lay segs[0])."""
    genders: list[str] = []
    ages: list[str] = []
    for seg in segs:
        g, a = _llm_gender_age(seg)
        if g:
            genders.append(g)
        if a:
            ages.append(a)
    g = Counter(genders).most_common(1)[0][0] if genders else None
    a = Counter(ages).most_common(1)[0][0] if ages else None
    return g, a


def resolve_profile_prefer_llm(
    sample: dict,
    f0_profile: str | None,
    *,
    lang: str,
    segs: list[dict] | None = None,
) -> tuple[dict, str]:
    """Gioi + tuoi: assign_cast (LLM). F0 chi do, khong doi nam/nu hay tre/lon.

    Tra (tts fields + voice_profile, ly do log).
    """
    if segs:
        llm_g, llm_a = _majority_llm_gender_age(segs)
    else:
        llm_g, llm_a = _llm_gender_age(sample)
    pitch_g = _f0_gender(f0_profile)
    if llm_a or llm_g:
        age = llm_a or AGE_ADULT
        gender = llm_g or pitch_g or "male"
        why = (
            f"khoa LLM gender={gender} age={age}; "
            f"F0={f0_profile or 'none'} chi do, khong doi giong"
        )
        vid = f"{gender}_{age}"
        try:
            fields = tts_fields(lang, vid)
        except ValueError:
            fields = tts_fields(lang, f"{gender}_{AGE_ADULT}")
        prof = _stamp_voice_profile(
            str(fields.get("age") or age),
            str(fields.get("gender") or gender),
        )
        out = {
            **fields,
            "voice_profile": prof,
            "tts_voice_name": fields["tts_voice"],
        }
        return out, why
    if f0_profile:
        fields = resolve_voice_profile(lang, f0_profile)
        return fields, f"khong co tuoi/gioi LLM, dung F0={f0_profile}"
    return {}, "khong do duoc F0 va khong co tuoi LLM"


def detect_speaker_voice_profile(
    segments: list[dict],
    source_video: str | Path | None,
    work_dir: str | Path,
    *,
    target_lang: str = DEFAULT_LANG,
) -> list[dict]:
    """Do median F0 / speaker, khoa 1 giong TTS xuyen clip (cung giai doan lock_cast).

    Chi dung doan > 1s (pyin). Thieu librosa -> YIN fallback, khong fail job.
    """
    if not segments:
        return segments
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    lang = (target_lang or DEFAULT_LANG).strip().lower()

    by_sid: dict[str, list[int]] = defaultdict(list)
    for i, seg in enumerate(segments):
        sid = str(seg.get("speaker_id") or "").strip()
        if sid:
            by_sid[sid].append(i)
    if not by_sid:
        log.warning("Voice profile: khong co speaker_id — bo qua")
        return segments

    wav = None
    if source_video:
        try:
            from .prosody import _ensure_mono_wav
            wav = _ensure_mono_wav(Path(source_video), work_dir)
        except Exception as e:  # noqa: BLE001
            log.warning("Voice profile: khong trich audio (%s)", e)

    pyin_ok = _pyin_available()
    if wav is not None and not pyin_ok:
        log.warning(
            "Voice profile: chua cai librosa — dung YIN fallback. pip install librosa"
        )

    info_by_sid: dict[str, dict] = {}
    report: list[dict] = []
    for sid, idxs in by_sid.items():
        segs = [segments[i] for i in idxs]
        measured = _measure_speaker_f0(
            segs, wav, use_pyin=pyin_ok and wav is not None,
        )
        median = measured["median_f0_hz"]
        profile = classify_f0_profile(median)
        n_long = int(measured["n_long"])
        n_voiced = int(measured["n_voiced"])
        method = str(measured["method"])
        sample = segs[0]
        fields, why = resolve_profile_prefer_llm(
            sample, profile, lang=lang, segs=segs,
        )
        if fields.get("voice_profile") or fields.get("voice_id"):
            info_by_sid[sid] = {
                **fields,
                "median_f0_hz": round(float(median), 1) if median else None,
                "n_long": n_long,
                "n_voiced": n_voiced,
                "method": method,
            }
            log.info(
                "Voice profile speaker=%s median_f0=%s Hz "
                "(%d doan >%.0fs, %d frame voiced, %s) -> %s / %s (%s) | %s",
                sid,
                f"{float(median):.1f}" if median else "-",
                n_long, MIN_PITCH_SEG_SEC, n_voiced, method,
                fields.get("voice_profile") or "-",
                fields.get("tts_voice_name") or fields.get("tts_voice") or "-",
                fields.get("voice_id") or "-",
                why,
            )
        else:
            keep_name = str(sample.get("tts_voice") or sample.get("tts_voice_name") or "")
            keep_vid = str(sample.get("voice_id") or "")
            info_by_sid[sid] = {
                "voice_profile": "",
                "tts_voice_name": keep_name,
                "median_f0_hz": None,
                "n_long": n_long,
                "n_voiced": n_voiced,
                "method": method,
                "kept_assign_cast": True,
            }
            log.warning(
                "Voice profile speaker=%s khong do duoc F0 "
                "(%d doan >%.0fs, method=%s) — giu giong assign_cast %s / %s",
                sid, n_long, MIN_PITCH_SEG_SEC, method, keep_vid, keep_name or "-",
            )
        segs_bits = []
        for p in measured.get("per_seg") or []:
            hz = p.get("median_f0_hz")
            segs_bits.append(
                f"{p.get('start')}-{p.get('end')}s={hz if hz is not None else '-'}"
            )
        if segs_bits:
            log.info("Voice profile %s doan F0: %s", sid, "; ".join(segs_bits))
        report.append({
            "speaker_id": sid,
            "median_f0_hz": info_by_sid[sid].get("median_f0_hz"),
            "n_long": n_long,
            "n_voiced": n_voiced,
            "method": method,
            "voice_profile": info_by_sid[sid].get("voice_profile") or "",
            "tts_voice_name": info_by_sid[sid].get("tts_voice_name") or "",
            "voice_id": info_by_sid[sid].get("voice_id") or sample.get("voice_id") or "",
            "segments": measured.get("per_seg") or [],
        })

    out = apply_speaker_voice_profiles(segments, info_by_sid)
    _write_voice_profile_files(work_dir, out, report)
    return out


def apply_speaker_voice_profiles(
    segments: list[dict],
    info_by_sid: dict[str, dict],
) -> list[dict]:
    """Gan cung voice_profile / tts_voice_name cho MOI doan cung speaker_id."""
    from .translate_llm import apply_cast_lock, locked_cast_payload

    offsets = _profile_pitch_offsets(info_by_sid)
    stamp: dict[str, dict] = {}
    for row in locked_cast_payload(segments):
        sid = str(row.get("speaker_id") or "")
        if sid:
            stamp[sid] = dict(row)
    for sid, info in info_by_sid.items():
        merged = dict(stamp.get(sid) or {"speaker_id": sid})
        for k in _VOICE_LOCK_KEYS:
            if k not in info:
                continue
            val = info.get(k)
            if val is None or val == "":
                continue
            merged[k] = val
        pitch = str(merged.get("speaker_pitch") or info.get("speaker_pitch") or "")
        hz_off = int(offsets.get(sid, 0))
        if pitch and hz_off:
            merged["speaker_pitch"] = _add_hz(pitch, hz_off)
        stamp[sid] = merged
    return apply_cast_lock(segments, stamp)


def _profile_pitch_offsets(info_by_sid: dict[str, dict]) -> dict[str, int]:
    """2 speaker cung profile: lech pitch nhe (nhu assign_cast)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for sid, info in info_by_sid.items():
        profile = str(info.get("voice_profile") or "").strip()
        if not profile or info.get("kept_assign_cast"):
            continue
        if sid not in groups[profile]:
            groups[profile].append(sid)
    out: dict[str, int] = {}
    for sids in groups.values():
        if len(sids) <= 1:
            continue
        for i, sid in enumerate(sids):
            out[sid] = int(round((i - (len(sids) - 1) / 2) * 3))
    return out


def _pyin_available() -> bool:
    try:
        import librosa  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(float(x) for x in vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _seg_dur(seg: dict) -> float:
    return max(0.0, float(seg.get("end") or 0) - float(seg.get("start") or 0))


def _measure_speaker_f0(
    segs: list[dict],
    wav: Path | None,
    *,
    use_pyin: bool,
) -> dict[str, Any]:
    long_segs = [s for s in segs if _seg_dur(s) > MIN_PITCH_SEG_SEC]
    per_seg: list[dict] = []
    frames: list[float] = []
    method = "none"
    if wav is not None and long_segs:
        if use_pyin:
            method = "pyin"
            for s in long_segs:
                voiced = _pyin_frames(
                    wav, float(s.get("start") or 0), float(s.get("end") or 0),
                )
                seg_med = _median(voiced)
                frames.extend(voiced)
                per_seg.append({
                    "start": round(float(s.get("start") or 0), 3),
                    "end": round(float(s.get("end") or 0), 3),
                    "dur": round(_seg_dur(s), 3),
                    "median_f0_hz": round(seg_med, 1) if seg_med else None,
                    "n_voiced": len(voiced),
                })
        if not frames:
            method = "yin"
            per_seg = []
            yin_vals: list[float] = []
            for s in long_segs:
                f0 = _f0_hz(
                    wav,
                    float(s.get("start") or 0),
                    float(s.get("end") or 0),
                    max_win=None,
                )
                if f0:
                    yin_vals.append(f0)
                per_seg.append({
                    "start": round(float(s.get("start") or 0), 3),
                    "end": round(float(s.get("end") or 0), 3),
                    "dur": round(_seg_dur(s), 3),
                    "median_f0_hz": round(f0, 1) if f0 else None,
                    "n_voiced": 1 if f0 else 0,
                })
            frames = yin_vals
    if not frames:
        method = "assign_cast_f0"
        fallback: list[float] = []
        for s in long_segs or segs:
            f0 = s.get("f0_hz")
            if f0:
                fallback.append(float(f0))
        frames = fallback
        if not per_seg:
            for s in long_segs or segs:
                f0 = s.get("f0_hz")
                per_seg.append({
                    "start": round(float(s.get("start") or 0), 3),
                    "end": round(float(s.get("end") or 0), 3),
                    "dur": round(_seg_dur(s), 3),
                    "median_f0_hz": round(float(f0), 1) if f0 else None,
                    "n_voiced": 1 if f0 else 0,
                })
    median = _median(frames)
    return {
        "median_f0_hz": round(median, 1) if median else None,
        "n_long": len(long_segs),
        "n_voiced": len(frames),
        "method": method,
        "per_seg": per_seg,
    }


def _pyin_frames(wav_path: Path, start: float, end: float) -> list[float]:
    dur = float(end) - float(start)
    if dur <= MIN_PITCH_SEG_SEC:
        return []
    try:
        import librosa
        import numpy as np
    except ImportError:
        return []
    try:
        y, sr = librosa.load(
            str(wav_path), sr=None, mono=True,
            offset=max(0.0, float(start)), duration=dur,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("pyin load %.2f-%.2f that bai: %s", start, end, e)
        return []
    if y is None or len(y) < 32:
        return []
    try:
        f0, _, _ = librosa.pyin(
            y, fmin=80.0, fmax=400.0, sr=int(sr) or 16000,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("pyin %.2f-%.2f that bai: %s", start, end, e)
        return []
    voiced = f0[~np.isnan(f0)]
    return [float(x) for x in voiced if 80.0 <= float(x) <= 400.0]


def warmup_pyin() -> None:
    """JIT-compile librosa.pyin / numba truoc job dau — tranh ~115s lan goi dau."""
    import time as _time

    if not _pyin_available():
        log.info("pyin warmup: bo qua (chua cai librosa)")
        return
    try:
        import librosa
        import numpy as np
    except ImportError:
        return
    sr = 16000
    y = (np.random.default_rng(0).standard_normal(sr * 2) * 0.02).astype(np.float32)
    t0 = _time.perf_counter()
    try:
        librosa.pyin(y, fmin=80.0, fmax=400.0, sr=sr)
    except Exception as e:  # noqa: BLE001
        log.warning("pyin warmup that bai: %s", e)
        return
    log.info("pyin warmup xong %.2fs (JIT da compile)", _time.perf_counter() - t0)


def _write_voice_profile_files(
    work_dir: Path,
    segments: list[dict],
    report: list[dict],
) -> None:
    from .translate_llm import locked_cast_payload

    try:
        (work_dir / "voice_profile.json").write_text(
            json.dumps({"speakers": report}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.exception("Khong ghi voice_profile.json")
    payload = locked_cast_payload(segments)
    lock_path = work_dir / "cast_lock.json"
    existing: list[dict] = []
    if lock_path.exists():
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("cast"), list):
                existing = [c for c in data["cast"] if isinstance(c, dict)]
        except Exception:
            log.exception("Khong doc cast_lock.json truoc khi ghi voice profile")
    by: dict[str, dict] = {
        str(c.get("speaker_id") or ""): dict(c) for c in existing if c.get("speaker_id")
    }
    for row in payload:
        sid = str(row.get("speaker_id") or "")
        if not sid:
            continue
        by.setdefault(sid, {"speaker_id": sid})
        by[sid].update({k: v for k, v in row.items() if v not in (None, "")})
        if row.get("median_f0_hz") is not None:
            by[sid]["median_f0_hz"] = row["median_f0_hz"]
    try:
        lock_path.write_text(
            json.dumps({"cast": list(by.values())}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.exception("Khong ghi voice profile vao cast_lock.json")
    try:
        cast_path = work_dir / "cast.json"
        if cast_path.exists():
            prev = json.loads(cast_path.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("speakers"), list):
                info = {str(c.get("speaker_id") or ""): c for c in by.values()}
                for row in prev["speakers"]:
                    sid = str(row.get("speaker_id") or "")
                    c = info.get(sid) or {}
                    for k in _VOICE_LOCK_KEYS:
                        if c.get(k) not in (None, ""):
                            row[k] = c[k]
                prev["locked_cast"] = list(by.values())
                prev["voice_profile"] = report
                cast_path.write_text(
                    json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8",
                )
    except Exception:
        log.exception("Khong cap nhat cast.json sau voice profile")


def _add_hz(pitch: str, delta: int) -> str:
    raw = (pitch or "+0Hz").replace("Hz", "").replace("hz", "").strip() or "0"
    try:
        n = int(float(raw))
    except ValueError:
        n = 0
    n = max(-12, min(14, n + delta))
    return f"{n:+d}Hz"


def _same_voice_offsets(rows: list[dict]) -> dict[str, int]:
    """2 nhan vat cung gioi/tuoi: lech pitch nhe de khong trung khop 100%."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        sid = str(row.get("speaker_id") or "").strip()
        if not sid:
            continue
        key = (
            str(row.get("gender") or "").lower(),
            str(row.get("age") or "").lower(),
        )
        if sid not in groups[key]:
            groups[key].append(sid)
    out: dict[str, int] = {}
    for sids in groups.values():
        if len(sids) <= 1:
            continue
        for i, sid in enumerate(sids):
            out[sid] = (i - (len(sids) - 1) / 2) * 3
    return {k: int(round(v)) for k, v in out.items()}


def _acoustic_hints(
    segments: list[dict],
    source_video: str | Path | None,
    work_dir: Path,
) -> list[dict]:
    wav = None
    if source_video:
        try:
            from .prosody import _ensure_mono_wav
            wav = _ensure_mono_wav(Path(source_video), work_dir)
        except Exception as e:  # noqa: BLE001
            log.warning("cast: khong trich audio (%s)", e)
    out = []
    for seg in segments:
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        f0 = _f0_hz(wav, start, end) if wav else None
        out.append({"f0_hz": round(f0, 1) if f0 else None})
    return out


def _f0_hz(
    wav_path: Path | None,
    start: float,
    end: float,
    *,
    max_win: float | None = 0.7,
) -> float | None:
    if wav_path is None:
        return None
    try:
        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate() or 16000
            nch = wf.getnchannels() or 1
            sw = wf.getsampwidth() or 2
            nframes = wf.getnframes()
            span = max(0.0, float(end) - float(start))
            if max_win is None:
                t0 = max(0.0, float(start))
                win = max(0.18, span)
            else:
                mid = (float(start) + float(end)) / 2.0
                win = min(float(max_win), max(0.18, span))
                t0 = max(0.0, mid - win / 2.0)
            i0 = int(t0 * rate)
            n = int(win * rate)
            if i0 >= nframes or n < 64:
                return None
            wf.setpos(min(i0, nframes - 1))
            raw = wf.readframes(min(n, nframes - i0))
    except Exception:  # noqa: BLE001
        return None
    if sw != 2 or not raw:
        return None
    samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    if nch > 1:
        samples = samples[0::nch]
    if len(samples) < 64:
        return None
    return _yin_f0(samples, rate)


def _yin_f0(samples: tuple[int, ...] | list[int], rate: int) -> float | None:
    """F0 tho (80-420 Hz) — du de doan nam/nu/tre, khong can pyannote."""
    n = len(samples)
    step = max(1, n // 4000)
    xs = [samples[i] / 32768.0 for i in range(0, n, step)]
    if len(xs) < 80:
        return None
    # high-pass nhe
    ys = [xs[i] - xs[i - 1] for i in range(1, len(xs))]
    sr = rate / step
    min_lag = max(2, int(sr / 420))
    max_lag = min(len(ys) // 3, int(sr / 80))
    if max_lag <= min_lag + 2:
        return None
    best_lag = 0
    best_corr = 0.0
    for lag in range(min_lag, max_lag):
        acc = 0.0
        e1 = 0.0
        e2 = 0.0
        m = len(ys) - lag
        if m < 32:
            break
        take = min(m, 800)
        for i in range(0, take, max(1, take // 200)):
            a = ys[i]
            b = ys[i + lag]
            acc += a * b
            e1 += a * a
            e2 += b * b
        denom = math.sqrt(e1 * e2) or 1e-9
        corr = acc / denom
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    if best_lag < 1 or best_corr < 0.35:
        return None
    f0 = sr / best_lag
    if f0 < 75 or f0 > 450:
        return None
    return f0


def _guess_band(f0: float | None) -> tuple[str, str]:
    if f0 is None:
        return "male", AGE_ADULT
    if f0 >= 255:
        return ("female" if f0 >= 290 else "male"), AGE_CHILD
    if f0 >= 190:
        return "female", AGE_YOUNG if f0 >= 210 else AGE_ADULT
    if f0 >= 165:
        return "male", AGE_YOUNG
    if f0 < 125:
        return "male", AGE_MATURE
    return "male", AGE_ADULT


def _acoustic_cast(
    segments: list[dict],
    feats: list[dict],
    fallback_id: str,
) -> list[dict]:
    rows = []
    prev_key = ""
    for seg, feat in zip(segments, feats):
        gender, age = _guess_band(feat.get("f0_hz"))
        if feat.get("f0_hz") is None:
            vid = fallback_id
            gender = "female" if vid.startswith("female") else "male"
            age = vid.split("_", 1)[-1] if "_" in vid else AGE_ADULT
        key = f"{gender}_{age}"
        # giu cung speaker khi cung band; doi speaker khi doi band
        sid = key if key == prev_key or not prev_key else key
        prev_key = key
        rows.append({
            "speaker_id": sid,
            "gender": gender,
            "age": age,
            "voice_id": key,
            "role": sid,
        })
    log.info("Cast acoustic (khong LLM): %s", Counter(r["speaker_id"] for r in rows))
    return rows


def _llm_cast(
    segments: list[dict],
    feats: list[dict],  # noqa: ARG001 — giu chu ky; F0 khong dua vao prompt
    *,
    lang: str,
) -> list[dict] | None:
    key = _load_key()
    if not key:
        from .preflight import allow_degraded, alert_ops, require_openai
        if not allow_degraded():
            require_openai(kind="f0_fallback")
        alert_ops("f0_fallback", "Cast: thieu OPENAI_API_KEY — dung F0 (DYFLOW_ENV=dev)")
        log.warning("Cast: thieu OPENAI_API_KEY — dung F0 (DYFLOW_ENV=dev)")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        from .preflight import allow_degraded, alert_ops
        msg = "Cast: chua cai openai"
        alert_ops("f0_fallback", msg)
        if not allow_degraded():
            raise RuntimeError(msg) from None
        log.warning(msg)
        return None

    client = OpenAI(api_key=key)
    model = os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini"
    assigned: list[dict | None] = [None] * len(segments)
    known: list[dict] = []

    for start in range(0, len(segments), _CHUNK):
        chunk = segments[start:start + _CHUNK]
        payload = []
        for i, seg in enumerate(chunk, start + 1):
            payload.append({
                "i": i,
                "start": round(float(seg.get("start") or 0), 2),
                "source": str(seg.get("source_text") or "")[:180],
                "dub": str(seg.get("text") or "")[:180],
            })
        system = (
            "Assign character voices for ONE dubbed video. Same file, many speakers.\n"
            "Group lines spoken by the same person. Typical 2-6 characters.\n"
            "age MUST be one of: child (~6-12), young (~18-25), adult (~30-45), mature (~50+).\n"
            "gender: male | female. Narrator is a character too.\n"
            "Decide from Chinese source + names (妈/爸/儿子/女儿/阿姨) + who is speaking.\n"
            "Do NOT guess gender or age from pitch. Dialogue ABOUT a child can be adult speech.\n"
            "Narrator / voice-over is usually adult, even if the story is about a child.\n"
            "Keep the same speaker_id, gender, and age for that person throughout the clip.\n"
            "speaker_id: short ascii slug (boy, dad, aunt, narrator).\n"
            "Return JSON: {\"cast\":[{\"id\":\"boy\",\"gender\":\"male\",\"age\":\"child\","
            "\"role\":\"young boy\"}], \"lines\":[{\"i\":1,\"speaker\":\"boy\"}, ...]} "
            "lines must cover every i in the input, same count."
        )
        user: dict[str, Any] = {"lines": payload}
        if known:
            user["known_cast"] = known
        try:
            r = client.chat.completions.create(
                model=model,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
            )
            raw = (r.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            log.warning("Cast LLM loi: %s", e)
            from .preflight import alert_ops
            alert_ops("f0_fallback", f"Cast LLM loi: {e}")
            return None
        parsed = _parse_cast(raw, start, len(chunk), known)
        if not parsed:
            log.warning("Cast LLM: khong parse duoc JSON chunk %d", start)
            from .preflight import alert_ops
            alert_ops("f0_fallback", f"Cast LLM: khong parse duoc JSON chunk {start}")
            return None
        rows, known = parsed
        for j, row in enumerate(rows):
            assigned[start + j] = row

    if any(x is None for x in assigned):
        return None
    return [x for x in assigned if x is not None]


def _parse_cast(
    raw: str,
    start: int,
    n: int,
    known: list[dict],
) -> tuple[list[dict], list[dict]] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        import re
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    cast_meta = {}
    for item in data.get("cast") or known:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or item.get("speaker") or "").strip()
        if not cid:
            continue
        gender = str(item.get("gender") or "male").strip().lower()
        age = _norm_age(str(item.get("age") or AGE_ADULT))
        if gender not in _VALID_GENDER:
            gender = "male"
        if age not in AGE_ORDER:
            age = AGE_ADULT
        cast_meta[cid] = {
            "speaker_id": cid,
            "gender": gender,
            "age": age,
            "voice_id": f"{gender}_{age}",
            "role": str(item.get("role") or cid),
        }
    lines = data.get("lines")
    if not isinstance(lines, list) or len(lines) != n:
        return None
    rows = []
    for j, item in enumerate(lines):
        if isinstance(item, str):
            sid = item.strip()
        elif isinstance(item, dict):
            sid = str(item.get("speaker") or item.get("id") or "").strip()
            expect = item.get("i")
            if expect is not None and int(expect) != start + j + 1:
                # van chap nhan theo thu tu
                pass
        else:
            return None
        if not sid:
            return None
        meta = cast_meta.get(sid)
        if not meta:
            gender, age = "male", AGE_ADULT
            if isinstance(item, dict):
                gender = str(item.get("gender") or gender).lower()
                age = _norm_age(str(item.get("age") or age))
            if gender not in _VALID_GENDER:
                gender = "male"
            if age not in AGE_ORDER:
                age = AGE_ADULT
            meta = {
                "speaker_id": sid,
                "gender": gender,
                "age": age,
                "voice_id": f"{gender}_{age}",
                "role": sid,
            }
            cast_meta[sid] = meta
        rows.append(dict(meta))
    known_out = list(cast_meta.values())
    return rows, known_out


def _load_key() -> str:
    from .preflight import hydrate_openai_key
    return hydrate_openai_key()
