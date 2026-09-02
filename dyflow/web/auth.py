"""Dang ky / dang nhap: hash mat khau (pbkdf2 stdlib) + session cookie."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import Request

from . import models

COOKIE_NAME = "dyflow_session"
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2$sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, algo, rounds, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            algo, password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:  # noqa: BLE001
        return False


def current_user(request: Request) -> Optional[dict]:
    """Lay user tu session cookie, None neu chua dang nhap.

    Desktop: luon co user local (khong bat login / credit).
    """
    token = request.cookies.get(COOKIE_NAME)
    if token:
        user = models.get_session_user(token)
        if user:
            return user
    from ..desktop_mode import enabled
    if enabled():
        return models.ensure_desktop_owner()
    return None
