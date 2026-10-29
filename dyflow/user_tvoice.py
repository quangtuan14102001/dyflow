# -*- coding: utf-8 -*-
"""Key T-Voice AI (tvoiceai.com) do user nhap - dung cho TTS chat luong cao."""
from __future__ import annotations

import os
import re
from pathlib import Path

ENV = "TVOICE_API_KEY"


def _user_env_path() -> Path:
    from .desktop_mode import data_dir, enabled

    if enabled():
        return data_dir() / ".env"
    extra = (os.getenv("DYFLOW_ENV_FILE") or "").strip()
    if extra:
        return Path(extra)
    return Path.cwd() / ".env"


def _normalize_key(text: str) -> str:
    """Bo khoang trang / quote / prefix Bearer khi user paste."""
    t = (text or "").strip()
    t = t.strip("\"'“”‘’")
    t = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", t)
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    return t.strip()


def load_tvoice_key() -> str:
    env = _normalize_key(os.getenv(ENV) or os.getenv("TVOICEAI_API_KEY") or "")
    if env:
        return env
    path = _user_env_path()
    val = ""
    if path.is_file():
        try:
            from dotenv import dotenv_values

            val = _normalize_key(dotenv_values(path).get(ENV) or dotenv_values(path).get("TVOICEAI_API_KEY") or "")
        except Exception:  # noqa: BLE001
            pass
    if not val:
        try:
            from .desktop_mode import data_dir

            d_path = data_dir() / ".env"
            if d_path.is_file() and d_path != path:
                from dotenv import dotenv_values

                val = _normalize_key(dotenv_values(d_path).get(ENV) or dotenv_values(d_path).get("TVOICEAI_API_KEY") or "")
        except Exception:  # noqa: BLE001
            pass
    if not val:
        local_p = Path.cwd() / ".env"
        if local_p.is_file():
            try:
                from dotenv import dotenv_values

                val = _normalize_key(dotenv_values(local_p).get(ENV) or dotenv_values(local_p).get("TVOICEAI_API_KEY") or "")
            except Exception:  # noqa: BLE001
                pass
    return val


def has_tvoice_key() -> bool:
    key = load_tvoice_key()
    return bool(key) and key.lower() not in ("", "your-key-here", "sk-xxx", "sk_tvoice_xxx")


def _looks_like_key(text: str) -> bool:
    t = _normalize_key(text)
    return len(t) >= 15 and (t.startswith("sk_tvoice_") or t.startswith("sk-") or len(t) >= 20)


def verify_tvoice_key(key: str, *, timeout: float = 15.0) -> None:
    """Xac thuc key T-Voice AI.
    
    Sai key -> tvoice_key_invalid.
    Timeout / khong the ket noi -> tvoice_key_unreachable.
    """
    text = _normalize_key(key)
    if not _looks_like_key(text):
        raise ValueError("tvoice_key_invalid")

    import requests

    headers = {
        "Authorization": f"Bearer {text}",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "Kiểm tra kết nối",
        "voice_id": "Ngọc Huyền",
        "speed": 1.0,
        "format": "mp3",
    }
    try:
        r = requests.post("https://tvoiceai.com/api/tts", headers=headers, json=payload, timeout=timeout)
        if r.status_code in (401, 403):
            raise ValueError("tvoice_key_invalid")
        data = r.json() if r.content else {}
        if not data.get("success") and not data.get("jobId"):
            err_msg = str(data.get("error") or data.get("message") or "").lower()
            # Neu bao so du khong du -> Key hoan toan hop le, chi la het token
            if any(x in err_msg for x in ("số dư không đủ", "so du khong du", "không đủ", "khong du")):
                return
            if any(x in err_msg for x in ("api key", "sai key", "unauthorized", "invalid key", "forbidden")):
                raise ValueError("tvoice_key_invalid")
    except (requests.ConnectionError, requests.Timeout, OSError) as e:
        raise ValueError("tvoice_key_unreachable") from e
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        raise ValueError("tvoice_key_unreachable") from e


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
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def save_tvoice_key(key: str) -> None:
    """Luu key vao ca AppData va repo root .env."""
    raw = _normalize_key(key)
    if not raw:
        for p in (_user_env_path(), Path.cwd() / ".env"):
            try:
                _upsert_env(p, ENV, "")
            except Exception:  # noqa: BLE001
                pass
        os.environ.pop(ENV, None)
        return

    if not _looks_like_key(raw):
        raise ValueError("tvoice_key_invalid")

    try:
        verify_tvoice_key(raw, timeout=6.0)
    except ValueError as e:
        if str(e) == "tvoice_key_invalid":
            raise
        log.warning("Khong the verify ngay (mang lag hoac server cham: %s), van luu key", e)
    except Exception as e:
        log.warning("Verify gap loi (%s), van luu key", e)

    paths_to_save = {_user_env_path(), Path.cwd() / ".env"}
    try:
        from .desktop_mode import data_dir

        paths_to_save.add(data_dir() / ".env")
    except Exception:  # noqa: BLE001
        pass

    for p in paths_to_save:
        try:
            _upsert_env(p, ENV, raw)
        except Exception:  # noqa: BLE001
            pass
    os.environ[ENV] = raw
