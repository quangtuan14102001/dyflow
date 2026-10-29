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
    tvoice_voice: str = "",
) -> dict[str, str]:
    return {
        "id": vid,
        "gender": gender,
        "age": age,
        "age_years": AGE_YEARS[age],
        "voice": voice,
        "openai_voice": openai_voice,
        "tvoice_voice": tvoice_voice,
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
    tvoice_alt_male: tuple[str, ...] | None = None,
    tvoice_alt_female: tuple[str, ...] | None = None,
) -> list[dict]:
    """Nam/nu x 4 moc tuoi. Cung 1 speaker Edge: lech pitch/rate.

    child / young / adult / mature. Alt voice (English) dung speaker khac.
    """
    m_names = _pad4(male_alt, male_voice)
    f_names = _pad4(female_alt, female_voice)
    tv_m_names = _pad4(tvoice_alt_male, "Minh Đức")
    tv_f_names = _pad4(tvoice_alt_female, "Ngọc Huyền")
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
            tvoice_voice=tv_m_names[i],
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
            tvoice_voice=tv_f_names[i],
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
        "voices": _ages(
            "vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural",
            rate="+0%", pitch="+1Hz", volume="+5%",
            tvoice_alt_male=(
                "VIENEU_CLONE_1787477477754",  # Bé Minh Anh (6-12)
                "VIENEU_CLONE_1787469982602",  # Quang Review (18-25)
                "Minh Đức",                    # Minh Đức (30-45)
                "VIENEU_CLONE_1787124911734",  # Giọng Trầm (50+)
            ),
            tvoice_alt_female=(
                "VIENEU_CLONE_1787470643348",  # An Nhiên (6-12)
                "Ngọc Huyền",                  # Ngọc Huyền 5.0 (18-25)
                "VIENEU_CLONE_1787020235902",  # Huyền Trang (30-45)
                "VIENEU_CLONE_1787470345139",  # Trùng Dương (50+)
            ),
        ),
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
TTS_CHOICES: dict[str, dict[str, object]] = {
    "edge": {
        "id": "edge",
        "tts_engine": "edge",
        "openai_model": "tts-1",
        "label": "Edge TTS (Miễn phí)",
        "needs_openai_key": False,
        "needs_tvoice_key": False,
    },
    "tvoice": {
        "id": "tvoice",
        "tts_engine": "tvoice",
        "openai_model": "tvoice",
        "label": "T-Voice AI (240+ giọng Vbee/ElevenLabs)",
        "needs_openai_key": False,
        "needs_tvoice_key": True,
    },
}
DEFAULT_TTS = "edge"


def list_tts_choices() -> list[dict]:
    return [dict(v) for v in TTS_CHOICES.values()]


def resolve_tts(choice: str) -> dict:
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
    for v in voices:
        if v["id"].lower() == voice:
            return v["id"]
        if v.get("voice", "").lower() == voice and v.get("age") == AGE_ADULT:
            return v["id"]
    if voice in ("male", "m", "nam", "nam minh", "vi-vn-namminhneural"):
        for v in voices:
            if v["gender"] == "male" and v["age"] == AGE_ADULT:
                return v["id"]
        for v in voices:
            if v["gender"] == "male":
                return v["id"]
    if voice in ("female", "f", "nu", "nữ", "hoai my", "hoài my", "vi-vn-hoaimyneural"):
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
        try:
            from .tvoice import resolve_tvoice_voice_id
            tv_id = resolve_tvoice_voice_id(voice)
        except Exception:
            tv_id = voice
        return {
            "id": voice,
            "gender": "unknown",
            "age": "adult",
            "age_years": "30–45",
            "voice": voice,
            "openai_voice": "alloy",
            "tvoice_voice": tv_id,
            "label": voice,
            "label_key": "voice_custom",
            "rate": "+0%",
            "pitch": "+0Hz",
            "volume": "+0%",
            "lang": lang,
            "translate_to": LANGUAGES[lang]["translate_to"],
            "chars_per_line": int(LANGUAGES[lang].get("chars_per_line") or 24),
        }
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


# --- Voice profile (F0) -------------------------------------------------
# Bang nay co dinh theo target_lang. Dien / sua tts_voice_name khi co
# bang chinh thuc cho tung ngon ngu. Thieu lang -> fallback voice_id preset.
PROFILE_MALE_ADULT = "male_adult"
PROFILE_FEMALE_ADULT = "female_adult"
PROFILE_CHILD = "child"

# Nguong F0 tham khao (Hz). Khoang 155-165 va chong 250-255 xu ly o classify.
F0_MALE_ADULT = (85.0, 155.0)
F0_FEMALE_ADULT = (165.0, 255.0)
F0_CHILD = (250.0, 400.0)
F0_MALE_FEMALE_SPLIT = 160.0  # giua 155 va 165

_PROFILE_VOICE_ID = {
    PROFILE_MALE_ADULT: "male_adult",
    PROFILE_FEMALE_ADULT: "female_adult",
    PROFILE_CHILD: "male_child",
}

VOICE_PROFILE_MAP: dict[str, dict[str, dict[str, str]]] = {
    "vi": {
        PROFILE_MALE_ADULT: {
            "voice_id": "male_adult",
            "tts_voice_name": "vi-VN-NamMinhNeural",
        },
        PROFILE_FEMALE_ADULT: {
            "voice_id": "female_adult",
            "tts_voice_name": "vi-VN-HoaiMyNeural",
        },
        PROFILE_CHILD: {
            "voice_id": "male_child",
            "tts_voice_name": "vi-VN-NamMinhNeural",
        },
    },
}


def classify_f0_profile(f0_hz: float | None) -> str | None:
    """Nam truong thanh / nu truong thanh / tre em tu median F0.

    85-155 nam, 165-255 nu, 250-400 tre em.
    Chong 250-255: tre em. Khoang 155-165: moc 160 (≤160 nam).
    """
    if f0_hz is None:
        return None
    try:
        f0 = float(f0_hz)
    except (TypeError, ValueError):
        return None
    if f0 <= 0:
        return None
    if f0 < F0_CHILD[0]:
        if f0 <= F0_MALE_ADULT[1]:
            return PROFILE_MALE_ADULT
        if f0 < F0_FEMALE_ADULT[0]:
            return (
                PROFILE_MALE_ADULT
                if f0 <= F0_MALE_FEMALE_SPLIT
                else PROFILE_FEMALE_ADULT
            )
        return PROFILE_FEMALE_ADULT
    return PROFILE_CHILD


def resolve_voice_profile(lang: str, profile: str) -> dict[str, str]:
    """Map voice_profile -> TTS fields + tts_voice_name (bang VOICE_PROFILE_MAP)."""
    lang = (lang or DEFAULT_LANG).strip().lower()
    prof = (profile or "").strip().lower()
    row = (VOICE_PROFILE_MAP.get(lang) or {}).get(prof) or {}
    vid = str(row.get("voice_id") or _PROFILE_VOICE_ID.get(prof) or "male_adult")
    fields = tts_fields(lang, vid)
    name = str(row.get("tts_voice_name") or fields["tts_voice"])
    return {
        **fields,
        "voice_profile": prof,
        "tts_voice_name": name,
        "tts_voice": name,
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
