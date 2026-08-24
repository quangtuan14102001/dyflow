"""Preset ngon ngu dich + giong Edge TTS cho bot / CLI / web.

UI chi hien Giọng nam / Giọng nữ. ShortName Edge TTS nam trong code.
Dich (translate_to) doc lap TTS — doi giong KHONG anh huong chat luong dich.
"""

from __future__ import annotations

from typing import Any


def _voice(
    vid: str,
    *,
    gender: str,
    voice: str,
    label: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> dict[str, str]:
    return {
        "id": vid,
        "gender": gender,
        "voice": voice,
        "label": label,
        "rate": rate,
        "pitch": pitch,
        "volume": volume,
    }


def _mf(male_voice: str, female_voice: str, *, rate: str = "+0%", pitch: str = "+0Hz", volume: str = "+0%") -> list[dict]:
    """Moi ngon ngu: dung 2 lua chon nam/nu."""
    return [
        _voice("male", gender="male", voice=male_voice, label="Giọng nam",
               rate=rate, pitch=pitch, volume=volume),
        _voice("female", gender="female", voice=female_voice, label="Giọng nữ",
               rate=rate, pitch=pitch, volume=volume),
    ]


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
        "voices": _mf("vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural",
                      rate="+0%", pitch="+1Hz", volume="+5%"),
    },
    "en": {
        "label": "English",
        "translate_to": "en",
        "chars_per_line": 32,
        "voices": _mf("en-US-GuyNeural", "en-US-JennyNeural", rate="+5%"),
    },
    "ja": {
        "label": "日本語 (Japanese)",
        "translate_to": "ja",
        "chars_per_line": 16,
        "voices": _mf("ja-JP-KeitaNeural", "ja-JP-NanamiNeural"),
    },
    "pt": {
        "label": "Português (Brasil)",
        "translate_to": "pt",
        "chars_per_line": 30,
        "voices": _mf("pt-BR-AntonioNeural", "pt-BR-FranciscaNeural", rate="+5%"),
    },
    "hi": {
        "label": "हिन्दी (Hindi)",
        "translate_to": "hi",
        "chars_per_line": 26,
        "voices": _mf("hi-IN-MadhurNeural", "hi-IN-SwaraNeural"),
    },
    "de": {
        "label": "Deutsch (German)",
        "translate_to": "de",
        "chars_per_line": 30,
        "voices": _mf("de-DE-ConradNeural", "de-DE-KatjaNeural"),
    },
    "id": {
        "label": "Bahasa Indonesia",
        "translate_to": "id",
        "chars_per_line": 30,
        "voices": _mf("id-ID-ArdiNeural", "id-ID-GadisNeural", rate="+5%"),
    },
    "tl": {
        "label": "Filipino",
        "translate_to": "tl",
        "chars_per_line": 30,
        "voices": _mf("fil-PH-AngeloNeural", "fil-PH-BlessicaNeural", rate="+5%"),
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
            if v["gender"] == "male":
                return v["id"]
    if voice in ("female", "f"):
        for v in voices:
            if v["gender"] == "female":
                return v["id"]
    return voice


VOICE_PRESETS: dict[str, dict] = {
    "male": dict(_voice_map(DEFAULT_LANG)["male"]),
    "female": dict(_voice_map(DEFAULT_LANG)["female"]),
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
                    "gender": v["gender"],
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


def preview_sentence(lang: str) -> str:
    lang = (lang or DEFAULT_LANG).strip().lower()
    return PREVIEW_TEXT.get(lang) or PREVIEW_TEXT["en"]
