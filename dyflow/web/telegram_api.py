"""Telegram Bot HTTP helpers (getMe, getChatMember) — khong can polling."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from ..utils import log
from . import billing


def bot_token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def channel_username() -> str:
    raw = (os.getenv("TELEGRAM_CHANNEL") or billing.DEFAULT_TELEGRAM_CHANNEL).strip()
    if not raw:
        return billing.DEFAULT_TELEGRAM_CHANNEL
    return raw if raw.startswith("@") else f"@{raw}"


def channel_link() -> str:
    name = channel_username().lstrip("@")
    return f"https://t.me/{name}"


def _api(method: str, **params: Any) -> Optional[dict]:
    token = bot_token()
    if not token:
        return None
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"https://api.telegram.org/bot{token}/{method}"
    if q:
        url = f"{url}?{q}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            body = json.loads(resp.read().decode())
        if not body.get("ok"):
            log.warning("Telegram %s that bai: %s", method, body)
            return None
        return body.get("result")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        log.warning("Telegram %s HTTP %s: %s", method, e.code, detail[:400])
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram %s loi: %s", method, e)
        return None


def get_bot_username() -> Optional[str]:
    override = (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    if override:
        return override
    me = _api("getMe")
    if not me:
        return None
    return (me.get("username") or "").strip() or None


def is_channel_member(telegram_user_id: int | str) -> bool:
    """True neu user la member/admin/creator cua channel. Bot phai la admin channel."""
    chat = channel_username()
    result = _api("getChatMember", chat_id=chat, user_id=str(telegram_user_id))
    if not result:
        return False
    status = (result.get("status") or "").lower()
    return status in {"creator", "administrator", "member", "restricted"}
