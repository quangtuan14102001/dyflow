"""Key OpenAI do khach tu nhap — chi dung cho TTS tra phi.

Khong dung OPENAI_API_KEY dong goi trong .exe (key cua chu app).
"""
from __future__ import annotations

import os
from pathlib import Path


TTS_ENV = "OPENAI_TTS_API_KEY"


def _user_env_path() -> Path:
    from .desktop_mode import data_dir, enabled

    if enabled():
        return data_dir() / ".env"
    extra = (os.getenv("DYFLOW_ENV_FILE") or "").strip()
    if extra:
        return Path(extra)
    return Path.cwd() / ".env"


def load_tts_key() -> str:
    env = (os.getenv(TTS_ENV) or "").strip()
    if env:
        return env
    path = _user_env_path()
    if not path.is_file():
        return ""
    try:
        from dotenv import dotenv_values

        return (dotenv_values(path).get(TTS_ENV) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def has_tts_key() -> bool:
    key = load_tts_key()
    return bool(key) and key.lower() not in ("", "your-key-here", "sk-xxx")


def save_tts_key(key: str) -> str:
    """Ghi OPENAI_TTS_API_KEY vao .env user. Chuoi rong = xoa."""
    text = (key or "").strip()
    if text and not (text.startswith("sk-") and len(text) >= 20):
        raise ValueError("openai_key_invalid")
    path = _user_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines(True)
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{TTS_ENV}="):
            found = True
            if text:
                out.append(f"{TTS_ENV}={text}\n")
            continue
        out.append(line)
    if text and not found:
        if out and not str(out[-1]).endswith("\n"):
            out.append("\n")
        out.append(f"{TTS_ENV}={text}\n")
    path.write_text("".join(out), encoding="utf-8")
    if text:
        os.environ[TTS_ENV] = text
    else:
        os.environ.pop(TTS_ENV, None)
    return text
