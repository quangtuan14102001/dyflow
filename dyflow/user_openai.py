"""Key OpenAI do khach tu nhap — dich OpenAI + TTS tra phi.

Khong dung OPENAI_API_KEY dong goi trong .exe (key cua chu app) khi khach
da dan key cua minh. Luu key: bat buoc ping OpenAI — sai thi KHONG ghi.
"""
from __future__ import annotations

import os
from pathlib import Path


TTS_ENV = "OPENAI_TTS_API_KEY"
API_ENV = "OPENAI_API_KEY"


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


def _looks_like_key(text: str) -> bool:
    return bool(text) and text.startswith("sk-") and len(text) >= 20


def verify_openai_key(key: str, *, timeout: float = 15.0) -> None:
    """Ping OpenAI. Sai/het han -> openai_key_invalid. Mang -> openai_key_unreachable."""
    text = (key or "").strip()
    if not _looks_like_key(text):
        raise ValueError("openai_key_invalid")
    try:
        from openai import APIConnectionError, APITimeoutError, AuthenticationError, OpenAI
        from openai import PermissionDeniedError
    except ImportError as e:
        raise ValueError("openai_key_invalid") from e
    try:
        client = OpenAI(api_key=text, timeout=timeout, max_retries=0)
        # List models — du de xac thuc key (401 neu sai)
        client.models.list()
    except AuthenticationError as e:
        raise ValueError("openai_key_invalid") from e
    except PermissionDeniedError as e:
        raise ValueError("openai_key_invalid") from e
    except (APIConnectionError, APITimeoutError, TimeoutError, OSError) as e:
        raise ValueError("openai_key_unreachable") from e
    except Exception as e:  # noqa: BLE001
        raw = str(e).lower()
        if any(
            t in raw
            for t in (
                "authentication",
                "unauthorized",
                "invalid_api_key",
                "incorrect api key",
                "401",
                "permission",
            )
        ):
            raise ValueError("openai_key_invalid") from e
        if any(t in raw for t in ("timeout", "connect", "network", "resolve", "unreachable")):
            raise ValueError("openai_key_unreachable") from e
        # Khong chac — coi nhu invalid, khong luu
        raise ValueError("openai_key_invalid") from e


def _upsert_env_keys(path: Path, updates: dict[str, str | None]) -> None:
    """Ghi/xoa nhieu key trong .env; None = xoa dong."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines(True)
    done = {name: False for name in updates}
    out: list[str] = []
    for line in lines:
        matched = False
        for name, val in updates.items():
            if line.startswith(f"{name}="):
                matched = True
                done[name] = True
                if val is not None:
                    out.append(f"{name}={val}\n")
                break
        if not matched:
            out.append(line)
    for name, val in updates.items():
        if done[name] or val is None:
            continue
        if out and not str(out[-1]).endswith("\n"):
            out.append("\n")
        out.append(f"{name}={val}\n")
    path.write_text("".join(out), encoding="utf-8")


def save_tts_key(key: str, *, verify: bool = True) -> str:
    """Ghi key khach. Chuoi rong = xoa.

    verify=True: ping OpenAI truoc — sai/mang loi KHONG ghi file.
    Key dung: ghi ca OPENAI_TTS_API_KEY + OPENAI_API_KEY (dong bo, tranh key cu).
    """
    text = (key or "").strip()
    if text and not _looks_like_key(text):
        raise ValueError("openai_key_invalid")
    if text and verify:
        verify_openai_key(text)

    path = _user_env_path()
    if text:
        _upsert_env_keys(path, {TTS_ENV: text, API_ENV: text})
        os.environ[TTS_ENV] = text
        os.environ[API_ENV] = text
    else:
        _upsert_env_keys(path, {TTS_ENV: None})
        os.environ.pop(TTS_ENV, None)
        # Khong xoa OPENAI_API_KEY bundled — chi bo key TTS khach
    return text
