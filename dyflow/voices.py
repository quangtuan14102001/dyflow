"""Preset ngon ngu dich + giong Edge TTS cho bot / CLI / web.

UI: nam/nu x do tuoi (tre em, 18-25, 30-45, 50+). Job cu voice=male/female
van chay — alias sang do tuoi truong thanh.
Dich (translate_to) doc lap TTS — doi giong KHONG anh huong chat luong dich.
Pipeline tu gan giong theo nhan vat trong clip; preset chon tren UI la du phong.
"""

from __future__ import annotations

from typing import Any

AGE_CHILD = "child"     # ~6-12
AGE_YOUNG = "young"     # ~18-25
AGE_ADULT = "adult"     # ~30-45  (mac dinh, alias male/female)
AGE_MATURE = "mature"   # ~50+
AGE_YEARS = {
    AGE_CHILD: "6–12",
    AGE_YOUNG: "18–25",
    AGE_ADULT: "30–45",
    AGE_MATURE: "50+",
}
AGE_ORDER = (AGE_CHILD, AGE_YOUNG, AGE_ADULT, AGE_MATURE)

# OpenAI tts-1 chi 6 voice co dinh — child dung chung young.
_OA_MALE = ("echo", "echo", "onyx", "fable")
_OA_FEMALE = ("nova", "nova", "shimmer", "alloy")


def _voice(
    vid: str,
    *,
    gender: str,
    age: str,
    voice: str,
    openai_voice: str,
    label: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> dict[str, str]:
    return {
        "id": vid,
        "gender": gender,
        "age": age,
        "age_years": AGE_YEARS[age],
        "voice": voice,
        "openai_voice": openai_voice,
        "label": label,
        "label_key": f"voice_{gender}_{age}",
        "rate": rate,
        "pitch": pitch,
        "volume": volume,
    }


def _shift_hz(base: str, delta: int) -> str:
    raw = (base or "+0Hz").replace("Hz", "").replace("hz", "").strip() or "0"
    try:
        n = int(float(raw))
    except ValueError:
        n = 0
    n = max(-12, min(14, n + delta))
    return f"{n:+d}Hz"


def _shift_pct(base: str, delta: int) -> str:
    raw = (base or "+0%").replace("%", "").strip() or "0"
    try:
        n = int(float(raw))
    except ValueError:
        n = 0
    n = max(-15, min(12, n + delta))
    return f"{n:+d}%"


def _pad4(names: tuple[str, ...] | None, fallback: str) -> tuple[str, str, str, str]:
    if not names:
        return (fallback, fallback, fallback, fallback)
    if len(names) >= 4:
        return (names[0], names[1], names[2], names[3])
    if len(names) == 3:
        return (names[0], names[0], names[1], names[2])
    return (fallback, fallback, fallback, fallback)


def _ages(
    male_voice: str,
    female_voice: str,
    *,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
    male_alt: tuple[str, ...] | None = None,
    female_alt: tuple[str, ...] | None = None,
) -> list[dict]:
    """Nam/nu x 4 moc tuoi. Cung 1 speaker Edge: lech pitch/rate.

    child / young / adult / mature. Alt voice (English) dung speaker khac.
    """
    m_names = _pad4(male_alt, male_voice)
    f_names = _pad4(female_alt, female_voice)
    same_m = len(set(m_names)) == 1
    same_f = len(set(f_names)) == 1
    # tre em: cao + nhanh; lon tuoi: tram + cham. Alt voice van nang nhe child.
    rate_d = (8, 6, 0, -6)
    pitch_d = (10, 6, 0, -6)
    alt_pitch_d = (4, 0, 0, 0)
    m_rate = tuple(_shift_pct(rate, d if same_m else 0) for d in rate_d)
    f_rate = tuple(_shift_pct(rate, d if same_f else 0) for d in rate_d)
    m_pitch = tuple(
        _shift_hz(pitch, d if same_m else alt_pitch_d[i])
        for i, d in enumerate(pitch_d)
    )
    f_pitch = tuple(
        _shift_hz(pitch, d if same_f else alt_pitch_d[i])
        for i, d in enumerate(pitch_d)
    )
    out: list[dict] = []
    for i, age in enumerate(AGE_ORDER):
        years = AGE_YEARS[age]
        out.append(_voice(
            f"male_{age}",
            gender="male",
            age=age,
            voice=m_names[i],
            openai_voice=_OA_MALE[i],
            label=f"Nam · {years}",
            rate=m_rate[i],
            pitch=m_pitch[i],
            volume=volume,
        ))
    for i, age in enumerate(AGE_ORDER):
        years = AGE_YEARS[age]
        out.append(_voice(
            f"female_{age}",
            gender="female",
            age=age,
            voice=f_names[i],
            openai_voice=_OA_FEMALE[i],
            label=f"Nữ · {years}",
            rate=f_rate[i],
            pitch=f_pitch[i],
            volume=volume,
        ))
    return out


PREVIEW_TEXT: dict[str, str] = {
    "vi": "Xin chào, đây là giọng lồng tiếng mẫu của VideosCreator.",
    "en": "Hello, this is a sample VideosCreator voiceover.",
    "ja": "こんにちは。これはVideosCreatorのサンプル音声です。",
    "pt": "Olá, esta é uma amostra de narração do VideosCreator.",
    "hi": "नमस्ते, यह VideosCreator की नमूना आवाज़ है।",
    "de": "Hallo, das ist eine Beispielstimme von VideosCreator.",
    "id": "Halo, ini adalah contoh suara VideosCreator.",
    "tl": "Kumusta, ito ang sample na boses ng VideosCreator.",
}

LANGUAGES: dict[str, dict[str, Any]] = {
    "vi": {
        "label": "Tiếng Việt",
        "translate_to": "vi",
        "chars_per_line": 24,
        # Chi 2 speaker Edge: NamMinh / HoaiMy — tuoi = pitch/rate.
        "voices": _ages("vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural",
                        rate="+0%", pitch="+1Hz", volume="+5%"),
    },
    "en": {
        "label": "English",
        "translate_to": "en",
        "chars_per_line": 32,
        "voices": _ages(
            "en-US-GuyNeural", "en-US-JennyNeural", rate="+5%",
            male_alt=(
                "en-US-AndrewNeural", "en-US-AndrewNeural",
                "en-US-GuyNeural", "en-US-EricNeural",
            ),
            female_alt=(
                "en-US-AnaNeural", "en-US-AriaNeural",
                "en-US-JennyNeural", "en-US-NancyNeural",
            ),
        ),
    },
    "ja": {
        "label": "日本語 (Japanese)",
        "translate_to": "ja",
        "chars_per_line": 16,
        "voices": _ages("ja-JP-KeitaNeural", "ja-JP-NanamiNeural"),
    },
    "pt": {
        "label": "Português (Brasil)",
        "translate_to": "pt",
        "chars_per_line": 30,
        "voices": _ages("pt-BR-AntonioNeural", "pt-BR-FranciscaNeural", rate="+5%"),
    },
    "hi": {
        "label": "हिन्दी (Hindi)",
        "translate_to": "hi",
        "chars_per_line": 26,
        "voices": _ages("hi-IN-MadhurNeural", "hi-IN-SwaraNeural"),
    },
    "de": {
        "label": "Deutsch (German)",
        "translate_to": "de",
        "chars_per_line": 30,
        "voices": _ages("de-DE-ConradNeural", "de-DE-KatjaNeural"),
    },
    "id": {
        "label": "Bahasa Indonesia",
        "translate_to": "id",
        "chars_per_line": 30,
        "voices": _ages("id-ID-ArdiNeural", "id-ID-GadisNeural", rate="+5%"),
    },
    "tl": {
        "label": "Filipino",
        "translate_to": "tl",
        "chars_per_line": 30,
        "voices": _ages("fil-PH-AngeloNeural", "fil-PH-BlessicaNeural", rate="+5%"),
    },
}

DEFAULT_LANG = "vi"

# Lua chon TTS tren web. Batch 1000 clip van dung Edge (khong qua form nay).
TTS_CHOICES: dict[str, dict[str, str]] = {
    "edge": {
        "id": "edge",
        "tts_engine": "edge",
        "openai_model": "tts-1",
        "label": "Edge TTS",
    },
    "tts-1": {
        "id": "tts-1",
        "tts_engine": "openai",
        "openai_model": "tts-1",
        "label": "OpenAI tts-1",
    },
    "tts-1-hd": {
        "id": "tts-1-hd",
        "tts_engine": "openai",
        "openai_model": "tts-1-hd",
        "label": "OpenAI tts-1-hd",
    },
    "gpt-4o-mini-tts": {
        "id": "gpt-4o-mini-tts",
        "tts_engine": "openai",
        "openai_model": "gpt-4o-mini-tts",
        "label": "OpenAI gpt-4o-mini-tts",
    },
}
DEFAULT_TTS = "edge"


def list_tts_choices() -> list[dict[str, str]]:
    return [dict(v) for v in TTS_CHOICES.values()]


def resolve_tts(choice: str) -> dict[str, str]:
    key = (choice or DEFAULT_TTS).strip().lower()
    if key in ("openai", "openai_tts"):
        key = "tts-1"
    if key not in TTS_CHOICES:
        ids = ", ".join(TTS_CHOICES)
        raise ValueError(f"TTS khong hop le: {choice}. Chon: {ids}")
    return dict(TTS_CHOICES[key])


def _voice_map(lang: str) -> dict[str, dict]:
    return {v["id"]: v for v in LANGUAGES[lang]["voices"]}


def _alias_id(lang: str, voice: str) -> str:
    voice = (voice or "").strip().lower()
    voices = LANGUAGES[lang]["voices"]
    if voice in ("male", "m"):
        for v in voices:
            if v["gender"] == "male" and v["age"] == AGE_ADULT:
                return v["id"]
        for v in voices:
            if v["gender"] == "male":
                return v["id"]
    if voice in ("female", "f"):
        for v in voices:
            if v["gender"] == "female" and v["age"] == AGE_ADULT:
                return v["id"]
        for v in voices:
            if v["gender"] == "female":
                return v["id"]
    return voice


VOICE_PRESETS: dict[str, dict] = {
    "male": dict(_voice_map(DEFAULT_LANG)["male_adult"]),
    "female": dict(_voice_map(DEFAULT_LANG)["female_adult"]),
}


def list_languages() -> list[dict]:
    out = []
    for lang_id, meta in LANGUAGES.items():
        out.append({
            "id": lang_id,
            "label": meta["label"],
            "translate_to": meta["translate_to"],
            "voices": [
                {
                    "id": v["id"],
                    "label": v["label"],
                    "label_key": v["label_key"],
                    "gender": v["gender"],
                    "age": v["age"],
                    "age_years": v["age_years"],
                    "voice": v["voice"],
                }
                for v in meta["voices"]
            ],
        })
    return out


def resolve_preset(lang: str, voice: str) -> dict:
    lang = (lang or DEFAULT_LANG).strip().lower()
    if lang not in LANGUAGES:
        raise ValueError(f"Ngon ngu khong hop le: {lang}. Chon: {', '.join(LANGUAGES)}")
    vid = _alias_id(lang, voice)
    vmap = _voice_map(lang)
    if vid not in vmap:
        ids = ", ".join(vmap)
        raise ValueError(f"Giong khong hop le: {voice}. Chon: {ids}")
    preset = dict(vmap[vid])
    return {
        **preset,
        "lang": lang,
        "translate_to": LANGUAGES[lang]["translate_to"],
        "chars_per_line": int(LANGUAGES[lang].get("chars_per_line") or 24),
    }


def voice_label_key(voice_id: str) -> str | None:
    """i18n key cho id giọng (male/female cu -> adult). None neu khong map."""
    vid = (voice_id or "").strip().lower()
    if vid in ("male", "m"):
        return "voice_male_adult"
    if vid in ("female", "f"):
        return "voice_female_adult"
    if vid in {f"{g}_{a}" for g in ("male", "female") for a in AGE_ORDER}:
        return f"voice_{vid}"
    return None


def preview_sentence(lang: str) -> str:
    lang = (lang or DEFAULT_LANG).strip().lower()
    return PREVIEW_TEXT.get(lang) or PREVIEW_TEXT["en"]


def tts_fields(lang: str, voice_id: str) -> dict[str, str]:
    """Truong TTS de gan len tung doan (cast nhan vat)."""
    p = resolve_preset(lang, voice_id)
    return {
        "voice_id": p["id"],
        "tts_voice": p["voice"],
        "openai_voice": p.get("openai_voice") or "",
        "speaker_rate": p.get("rate") or "+0%",
        "speaker_pitch": p.get("pitch") or "+0Hz",
        "speaker_volume": p.get("volume") or "+0%",
        "gender": p["gender"],
        "age": p["age"],
    }


def infer_voice_id(voice: str, *, gender: str = "", age: str = "") -> str:
    """Map id / alias / ten Edge -> male_adult style id."""
    g = (gender or "").strip().lower()
    a = (age or "").strip().lower()
    if a not in AGE_ORDER:
        a = AGE_ADULT
    if g in ("male", "m", "nam"):
        return f"male_{a}"
    if g in ("female", "f", "nu", "nữ"):
        return f"female_{a}"
    raw = (voice or "").strip().lower()
    if raw in {f"{gg}_{aa}" for gg in ("male", "female") for aa in AGE_ORDER}:
        return raw
    vid = _alias_id(DEFAULT_LANG, raw)
    if vid in {f"{gg}_{aa}" for gg in ("male", "female") for aa in AGE_ORDER}:
        return vid
    female = (
        "female", "hoaimy", "jenny", "nanami", "francisca", "swara",
        "katja", "gadis", "blessica", "nova", "shimmer", "coral", "alloy",
        "ana", "aria", "nancy",
    )
    if any(x in raw for x in female):
        return f"female_{a}"
    return f"male_{a}"
