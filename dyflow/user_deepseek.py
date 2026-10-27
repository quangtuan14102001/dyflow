# -*- coding: utf-8 -*-
"""Key DeepSeek do user nhap — chi dung cho dich LLM (khong TTS)."""
from __future__ import annotations

import os
import re
from pathlib import Path

ENV = "DEEPSEEK_API_KEY"


def _user_env_path() -> Path:
    from .desktop_mode import data_dir, enabled

    if enabled():
        return data_dir() / ".env"
    extra = (os.getenv("DYFLOW_ENV_FILE") or "").strip()
    if extra:
        return Path(extra)
    return Path.cwd() / ".env"


def _normalize_key(text: str) -> str:
    """Bo khoang trang / quote / prefix Bearer khi user paste tu trang DeepSeek."""
    t = (text or "").strip()
    t = t.strip("\"'“”‘’")
    t = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", t)
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    return t.strip()


def load_deepseek_key() -> str:
    env = _normalize_key(os.getenv(ENV) or "")
    if env:
        return env
    path = _user_env_path()
    val = ""
    if path.is_file():
        try:
            from dotenv import dotenv_values

            val = _normalize_key(dotenv_values(path).get(ENV) or "")
        except Exception:  # noqa: BLE001
            pass
    if not val:
        try:
            from .desktop_mode import data_dir

            d_path = data_dir() / ".env"
            if d_path.is_file() and d_path != path:
                from dotenv import dotenv_values

                val = _normalize_key(dotenv_values(d_path).get(ENV) or "")
        except Exception:  # noqa: BLE001
            pass
    return val


def has_deepseek_key() -> bool:
    key = load_deepseek_key()
    return bool(key) and key.lower() not in ("", "your-key-here", "sk-xxx")


def _looks_like_key(text: str) -> bool:
    t = _normalize_key(text)
    # DeepSeek key dang sk-... (tuong tu OpenAI); chap nhan ca chuoi dai >= 20
    return len(t) >= 20 and (t.startswith("sk-") or len(t) >= 24)


def verify_deepseek_key(key: str, *, timeout: float = 20.0) -> None:
    """Ping DeepSeek — dung models.list (khong phu thuoc ten model / so du chat).

    Sai key -> deepseek_key_invalid.
    Mang/timeout -> deepseek_key_unreachable.
    """
    text = _normalize_key(key)
    if not _looks_like_key(text):
        raise ValueError("deepseek_key_invalid")
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            OpenAI,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError as e:
        raise ValueError("deepseek_key_invalid") from e

    from .llm_provider import DEEPSEEK_BASE_URL

    base = (os.getenv("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE_URL).strip() or DEEPSEEK_BASE_URL
    try:
        client = OpenAI(api_key=text, base_url=base, timeout=timeout, max_retries=0)
        # List models: xac thuc key, khong can model name (deepseek-chat da doi)
        client.models.list()
    except AuthenticationError as e:
        raise ValueError("deepseek_key_invalid") from e
    except PermissionDeniedError as e:
        raise ValueError("deepseek_key_invalid") from e
    except RateLimitError:
        # Key hop le — chi bi gioi han toc do
        return
    except (APIConnectionError, APITimeoutError, TimeoutError, OSError) as e:
        raise ValueError("deepseek_key_unreachable") from e
    except Exception as e:  # noqa: BLE001
        raw = str(e).lower()
        if any(
            t in raw
            for t in (
                "authentication",
                "unauthorized",
                "invalid_api_key",
                "incorrect api key",
                "api key is invalid",
                "authentication fails",
                "401",
            )
        ):
            # Tranh "invalid model" bi map thanh key sai
            if "model" in raw and "auth" not in raw and "401" not in raw and "api key" not in raw:
                return
            raise ValueError("deepseek_key_invalid") from e
        if any(
            t in raw
            for t in (
                "insufficient",
                "balance",
                "quota",
                "payment",
                "402",
                "arrearage",
            )
        ):
            # Key dung nhung chua nap tien — van cho luu
            return
        if any(t in raw for t in ("timeout", "connect", "network", "resolve", "unreachable", "ssl")):
            raise ValueError("deepseek_key_unreachable") from e
        if "model" in raw or "not found" in raw or "not exist" in raw:
            return
        if "429" in raw or "rate" in raw:
            return
        raise ValueError("deepseek_key_unreachable") from e


def _upsert_env(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    prefix = f"{key}="
    for line in lines:
        if line.strip().startswith(prefix) or line.strip().startswith(f"export {prefix}"):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def save_deepseek_key(key: str) -> str:
    text = _normalize_key(key)
    if not text:
        raise ValueError("deepseek_key_invalid")
    verify_deepseek_key(text)
    path = _user_env_path()
    _upsert_env(path, ENV, text)
    os.environ[ENV] = text
    return text
