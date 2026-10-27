# -*- coding: utf-8 -*-
"""LLM dich: OpenAI / DeepSeek / Qwen (API tuong thich OpenAI)."""
from __future__ import annotations

import contextvars
import os
from typing import Any

from .utils import log

_PROVIDER = contextvars.ContextVar("dyflow_llm_provider", default="deepseek")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"

# DashScope / Model Studio OpenAI-compatible
# Legacy shared + trial (sk-ws moi). Workspace host: set QWEN_BASE_URL thu cong.
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
QWEN_BASE_URL_CN = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_BASE_URL_TRIAL_INTL = "https://trial.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
QWEN_BASE_URL_TRIAL_CN = "https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
QWEN_DEFAULT_MODEL = "qwen3.7-flash"

_LLM_PROVIDERS = frozenset({"openai", "deepseek", "qwen", "google"})


def set_llm_provider(provider: str | None) -> str:
    p = str(provider or "deepseek").strip().lower()
    if p not in _LLM_PROVIDERS:
        p = "deepseek"
    if p == "google":
        p = preferred_chat_provider()
    _PROVIDER.set(p)
    return p


def current_llm_provider() -> str:
    return _PROVIDER.get() or "deepseek"


def preferred_chat_provider() -> str:
    """Uu tien model dang chon, hoac DeepSeek roi moi toi OpenAI/Qwen."""
    curr = _PROVIDER.get()
    if curr in ("deepseek", "qwen", "openai") and has_llm_key(curr):
        return curr
    from .user_deepseek import has_deepseek_key

    if has_deepseek_key():
        return "deepseek"
    from .preflight import openai_key

    if openai_key():
        return "openai"
    from .user_qwen import has_qwen_key

    if has_qwen_key():
        return "qwen"
    return "deepseek" if has_deepseek_key() else "openai"


def deepseek_key() -> str:
    from .user_deepseek import load_deepseek_key

    return load_deepseek_key()


def qwen_key() -> str:
    from .user_qwen import load_qwen_key

    return load_qwen_key()


def llm_api_key(provider: str | None = None) -> str:
    p = (provider or current_llm_provider()).strip().lower()
    if p == "deepseek":
        return deepseek_key()
    if p == "qwen":
        return qwen_key()
    from .preflight import openai_key

    return openai_key()


def llm_model(provider: str | None = None) -> str:
    p = (provider or current_llm_provider()).strip().lower()
    if p == "deepseek":
        return (
            os.getenv("DEEPSEEK_TRANSLATE_MODEL") or DEEPSEEK_DEFAULT_MODEL
        ).strip() or DEEPSEEK_DEFAULT_MODEL
    if p == "qwen":
        return (
            os.getenv("QWEN_TRANSLATE_MODEL") or QWEN_DEFAULT_MODEL
        ).strip() or QWEN_DEFAULT_MODEL
    return (
        os.getenv("OPENAI_TRANSLATE_MODEL") or OPENAI_DEFAULT_MODEL
    ).strip() or OPENAI_DEFAULT_MODEL


def llm_base_url(provider: str | None = None) -> str | None:
    p = (provider or current_llm_provider()).strip().lower()
    if p == "deepseek":
        return (os.getenv("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE_URL).strip() or DEEPSEEK_BASE_URL
    if p == "qwen":
        custom = (os.getenv("QWEN_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or "").strip()
        return custom or QWEN_BASE_URL
    custom = (os.getenv("OPENAI_BASE_URL") or "").strip()
    return custom or None


def make_llm_client(
    provider: str | None = None,
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> Any:
    """OpenAI SDK client — DeepSeek/Qwen dung base_url tuong thich."""
    from openai import OpenAI

    p = (provider or current_llm_provider()).strip().lower()
    key = llm_api_key(p)
    if not key:
        if p == "deepseek":
            raise RuntimeError("Thieu key DeepSeek.")
        if p == "qwen":
            raise RuntimeError("Thieu key Qwen (DashScope).")
        raise RuntimeError("Thieu OPENAI_API_KEY.")
    kwargs: dict[str, Any] = {"api_key": key}
    base = llm_base_url(p)
    if base:
        kwargs["base_url"] = base
    if timeout is not None:
        kwargs["timeout"] = timeout
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    log.debug("LLM client provider=%s model=%s base=%s", p, llm_model(p), base or "default")
    client = OpenAI(**kwargs)
    if p == "deepseek":
        _wrap_extra_body(client, {"thinking": {"type": "disabled"}})
    elif p == "qwen":
        # qwen3.7-flash mac dinh bat thinking — tat de dich JSON nhanh/re
        _wrap_extra_body(client, {"enable_thinking": False})
    return client


def _wrap_extra_body(client: Any, defaults: dict[str, Any]) -> None:
    """Merge extra_body mac dinh vao moi chat.completions.create."""
    completions = client.chat.completions
    orig = completions.create

    def create(*args: Any, **kwargs: Any) -> Any:
        extra = dict(defaults)
        extra.update(dict(kwargs.get("extra_body") or {}))
        kwargs["extra_body"] = extra
        return orig(*args, **kwargs)

    completions.create = create  # type: ignore[method-assign]


def has_llm_key(provider: str) -> bool:
    p = str(provider or "").strip().lower()
    if p == "deepseek":
        from .user_deepseek import has_deepseek_key

        return has_deepseek_key()
    if p == "qwen":
        from .user_qwen import has_qwen_key

        return has_qwen_key()
    if p == "openai":
        from .preflight import openai_key

        return bool(openai_key())
    return False
