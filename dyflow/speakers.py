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
    infer_voice_id,
    tts_fields,
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
            "voice_id": fields["voice_id"],
            "tts_voice": fields["tts_voice"],
            "openai_voice": fields["openai_voice"],
            "speaker_rate": fields["speaker_rate"],
            "speaker_pitch": pitch,
            "speaker_volume": fields["speaker_volume"],
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


def _f0_hz(wav_path: Path | None, start: float, end: float) -> float | None:
    if wav_path is None:
        return None
    try:
        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate() or 16000
            nch = wf.getnchannels() or 1
            sw = wf.getsampwidth() or 2
            nframes = wf.getnframes()
            mid = (float(start) + float(end)) / 2.0
            win = min(0.7, max(0.18, float(end) - float(start)))
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
    feats: list[dict],
    *,
    lang: str,
) -> list[dict] | None:
    key = _load_key()
    if not key:
        log.info("Cast: thieu OPENAI_API_KEY — dung F0")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("Cast: chua cai openai")
        return None

    client = OpenAI(api_key=key)
    model = os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini"
    assigned: list[dict | None] = [None] * len(segments)
    known: list[dict] = []

    for start in range(0, len(segments), _CHUNK):
        chunk = segments[start:start + _CHUNK]
        feats_c = feats[start:start + _CHUNK]
        payload = []
        for i, (seg, feat) in enumerate(zip(chunk, feats_c), start + 1):
            payload.append({
                "i": i,
                "start": round(float(seg.get("start") or 0), 2),
                "source": str(seg.get("source_text") or "")[:180],
                "dub": str(seg.get("text") or "")[:180],
                "f0_hz": feat.get("f0_hz"),
            })
        system = (
            "Assign character voices for ONE dubbed video. Same file, many speakers.\n"
            "Group lines spoken by the same person. Typical 2-6 characters.\n"
            "age MUST be one of: child (~6-12), young (~18-25), adult (~30-45), mature (~50+).\n"
            "gender: male | female. Narrator is a character too.\n"
            "Use Chinese source + dubbed line + f0_hz (Hz) together.\n"
            "f0 is a hint: child often ~250-350, adult female ~170-230, adult male ~100-160, "
            "mature male often <125. Dialogue ABOUT a child can still be adult speech.\n"
            "Identify WHO is speaking (boy, father, aunt, narrator...), not only pitch.\n"
            "Keep the same speaker_id for that person throughout the clip.\n"
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
            return None
        parsed = _parse_cast(raw, start, len(chunk), known)
        if not parsed:
            log.warning("Cast LLM: khong parse duoc JSON chunk %d", start)
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
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except Exception:  # noqa: BLE001
        pass
    return (os.getenv("OPENAI_API_KEY") or "").strip()
