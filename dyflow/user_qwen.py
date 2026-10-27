# -*- coding: utf-8 -*-
"""Key Qwen (DashScope / Model Studio) — chi dung cho dich LLM (khong TTS)."""
from __future__ import annotations

import os
import re
from pathlib import Path

ENV = "QWEN_API_KEY"
ENV_ALT = "DASHSCOPE_API_KEY"


def _user_env_path() -> Path:
    from .desktop_mode import data_dir, enabled

    if enabled():
        return data_dir() / ".env"
    extra = (os.getenv("DYFLOW_ENV_FILE") or "").strip()
    if extra:
        return Path(extra)
    return Path.cwd() / ".env"


def _normalize_key(text: str) -> str:
    t = (text or "").strip()
    t = t.strip("\"'“”‘’")
    t = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", t)
    # Model Studio moi: sk-ws-.... (co dau cham) — giu nguyen
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    return t.strip()


def load_qwen_key() -> str:
    for name in (ENV, ENV_ALT):
        env = _normalize_key(os.getenv(name) or "")
        if env:
            return env
    path = _user_env_path()
    if not path.is_file():
        return ""
    try:
        from dotenv import dotenv_values

        vals = dotenv_values(path)
        for name in (ENV, ENV_ALT):
            got = _normalize_key(vals.get(name) or "")
            if got:
                return got
    except Exception:  # noqa: BLE001
        return ""
    return ""


def has_qwen_key() -> bool:
    key = load_qwen_key()
    return bool(key) and key.lower() not in ("", "your-key-here", "sk-xxx")


def _looks_like_key(text: str) -> bool:
    t = _normalize_key(text)
    # sk- cu + sk-ws- (Model Studio 2026, co dau .)
    if t.startswith("sk-ws-") and len(t) >= 24:
        return True
    return len(t) >= 20 and (t.startswith("sk-") or len(t) >= 24)


def _candidate_bases() -> list[str]:
    from .llm_provider import (
        QWEN_BASE_URL,
        QWEN_BASE_URL_CN,
        QWEN_BASE_URL_TRIAL_CN,
        QWEN_BASE_URL_TRIAL_INTL,
    )

    custom = (os.getenv("QWEN_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or "").strip()
    if custom:
        return [custom]
    # Thu nhieu endpoint: sai region cung tra 401 "invalid" — phai thu het
    return [
        QWEN_BASE_URL,
        QWEN_BASE_URL_CN,
        QWEN_BASE_URL_TRIAL_INTL,
        QWEN_BASE_URL_TRIAL_CN,
    ]


def _ping(client: object, model: str) -> None:
    create = client.chat.completions.create  # type: ignore[attr-defined]
    try:
        create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
            extra_body={"enable_thinking": False},
        )
    except Exception as e:  # noqa: BLE001
        raw = str(e).lower()
        # Mot so region/SDK khong nhan enable_thinking — thu lai khong co
        if "enable_thinking" in raw or "thinking" in raw or "unknown" in raw:
            create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            )
            return
        raise


def _is_unpurchased(exc: BaseException) -> bool:
    raw = str(exc).lower()
    return any(
        t in raw
        for t in (
            "unpurchased",
            "accessdenied.unpurchased",
            "access to model denied",
            "not eligible",
            "eligible for using the model",
            "have not activated",
            "model is not activated",
        )
    )


def _is_balance(exc: BaseException) -> bool:
    raw = str(exc).lower()
    return any(
        t in raw
        for t in ("insufficient", "balance", "quota", "payment", "402", "arrearage")
    )


def verify_qwen_key(key: str, *, timeout: float = 25.0) -> str:
    """Ping Qwen/Model Studio. Tra ve base_url thanh cong.

    Sai key (moi region) -> qwen_key_invalid.
    Mang/timeout -> qwen_key_unreachable.
    Key dung nhung chua mua/kich hoat model -> van chap nhan (luu key).
    """
    text = _normalize_key(key)
    if not _looks_like_key(text):
        raise ValueError("qwen_key_invalid")
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
        raise ValueError("qwen_key_invalid") from e

    from .llm_provider import QWEN_DEFAULT_MODEL

    models = []
    primary = (os.getenv("QWEN_TRANSLATE_MODEL") or QWEN_DEFAULT_MODEL).strip() or QWEN_DEFAULT_MODEL
    models.append(primary)
    for alt in ("qwen-flash", "qwen-turbo", "qwen3.5-flash", "qwen3.7-flash"):
        if alt not in models:
            models.append(alt)

    last_err: BaseException | None = None
    saw_auth = False
    saw_net = False
    # base nao da xac nhan key (401 khong, nhung 403 unpurchased / balance)
    accepted_base: str | None = None
    hard_auth_bases = 0

    for base in _candidate_bases():
        base_hard_auth = False
        for model in models:
            try:
                client = OpenAI(api_key=text, base_url=base, timeout=timeout, max_retries=0)
                _ping(client, model)
                return base
            except AuthenticationError as e:
                saw_auth = True
                base_hard_auth = True
                last_err = e
                break
            except PermissionDeniedError as e:
                last_err = e
                # Key dung + dung region, chua mua/kich hoat model
                if _is_unpurchased(e) or _is_balance(e):
                    accepted_base = base
                    return base
                # Quyen khac — van coi la region dung neu khong phai 401
                if "403" in str(e) or "denied" in str(e).lower():
                    accepted_base = base
                    return base
                saw_auth = True
                base_hard_auth = True
                break
            except RateLimitError:
                return base
            except (APIConnectionError, APITimeoutError, TimeoutError, OSError) as e:
                saw_net = True
                last_err = e
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                raw = str(e).lower()
                if _is_unpurchased(e) or _is_balance(e):
                    return base
                if any(
                    t in raw
                    for t in (
                        "authentication",
                        "unauthorized",
                        "invalid_api_key",
                        "incorrect api key",
                        "invalid api-key",
                        "401",
                    )
                ):
                    saw_auth = True
                    base_hard_auth = True
                    break
                if "model" in raw or "not found" in raw or "not exist" in raw or "does not exist" in raw:
                    continue
                if "429" in raw or "rate" in raw:
                    return base
                if any(t in raw for t in ("timeout", "connect", "network", "resolve", "unreachable", "ssl")):
                    saw_net = True
                    break
                continue
        if base_hard_auth:
            hard_auth_bases += 1

    if accepted_base:
        return accepted_base
    if saw_auth and hard_auth_bases >= 1 and not saw_net:
        # Neu chi fail 401 o moi noi → key sai / copy thieu
        raise ValueError("qwen_key_invalid") from last_err
    if saw_net:
        raise ValueError("qwen_key_unreachable") from last_err
    raise ValueError("qwen_key_unreachable") from last_err


def _upsert_env(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found: set[str] = set()
    for line in lines:
        stripped = line.strip()
        replaced = False
        for key, value in updates.items():
            prefix = f"{key}="
            if stripped.startswith(prefix) or stripped.startswith(f"export {prefix}"):
                out.append(f"{key}={value}")
                found.add(key)
                replaced = True
                break
        if not replaced:
            out.append(line)
    for key, value in updates.items():
        if key not in found:
            if out and out[-1].strip():
                out.append("")
            out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def save_qwen_key(key: str) -> str:
    text = _normalize_key(key)
    if not text:
        raise ValueError("qwen_key_invalid")
    base = verify_qwen_key(text)
    path = _user_env_path()
    updates = {ENV: text, ENV_ALT: text}
    if base:
        updates["QWEN_BASE_URL"] = base
    _upsert_env(path, updates)
    os.environ[ENV] = text
    os.environ[ENV_ALT] = text
    if base:
        os.environ["QWEN_BASE_URL"] = base
    return text
