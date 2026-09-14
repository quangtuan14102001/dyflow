"""License desktop: dán key có hạn, không cần email.

Chủ app mint key (CLI / Telegram). File .exe kiểm tra chữ ký + ngày hết hạn.
Máy dev (chạy source, chưa đóng gói) không bắt key — trừ khi DYFLOW_LICENSE=1.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any


PREFIX = "VC1"
_MAC_LEN = 10
_MAX_DAYS = 3650


class LicenseError(Exception):
    """code: empty | invalid | expired"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _secret() -> bytes:
    p = bytes.fromhex(
        "a41c7e90b3d5f1826c09e4a7b81d3f50c26e9a14f0b8d357e1c49a70b5d28f36"
    )
    q = bytes.fromhex(
        "5e9b2c4d71a0f8e3d64b1c90a7e35f128d3c5e6fa1b24709c8e4d1b0a9376f25"
    )
    return bytes(x ^ y for x, y in zip(p, q))


def parse_duration(raw: str) -> int:
    """'30', '30d', '12m', '1y' -> so ngay."""
    s = (raw or "").strip().lower().replace(" ", "")
    if not s:
        raise ValueError("thieu thoi han")
    mult = 1
    if s.endswith("y"):
        mult = 365
        s = s[:-1]
    elif s.endswith("m"):
        mult = 30
        s = s[:-1]
    elif s.endswith("d"):
        s = s[:-1]
    days = int(float(s) * mult)
    if days < 1 or days > _MAX_DAYS:
        raise ValueError(f"thoi han 1..{_MAX_DAYS} ngay")
    return days


def mint(days: int, now: float | None = None) -> tuple[str, dict[str, Any]]:
    days = int(days)
    if days < 1 or days > _MAX_DAYS:
        raise ValueError(f"thoi han 1..{_MAX_DAYS} ngay")
    iat = int(now if now is not None else time.time())
    exp = iat + days * 86400
    body = struct.pack(">BII", 1, exp, iat) + os.urandom(8)
    mac = hmac.new(_secret(), body, hashlib.sha256).digest()[:_MAC_LEN]
    token = base64.b32encode(body + mac).decode("ascii").rstrip("=")
    key = _group(PREFIX + token)
    return key, {
        "expires_at": float(exp),
        "issued_at": float(iat),
        "days": days,
    }


def _group(s: str) -> str:
    body = s[len(PREFIX) :] if s.upper().startswith(PREFIX) else s
    chunks = [body[i : i + 5] for i in range(0, len(body), 5)]
    return PREFIX + "-" + "-".join(chunks)


def _token_body(key: str) -> str:
    compact = "".join(c for c in (key or "") if c.isalnum())
    if len(compact) < 8:
        raise LicenseError("invalid")
    head = compact[:3].upper()
    if head != PREFIX:
        raise LicenseError("invalid")
    return compact[3:]


def decode(key: str, now: float | None = None) -> dict[str, Any]:
    token = _token_body(key)
    pad = (-len(token)) % 8
    try:
        raw = base64.b32decode(token + ("=" * pad), casefold=True)
    except Exception as e:  # noqa: BLE001
        raise LicenseError("invalid") from e
    if len(raw) < 9 + 8 + _MAC_LEN:
        raise LicenseError("invalid")
    body, mac = raw[:17], raw[17 : 17 + _MAC_LEN]
    expect = hmac.new(_secret(), body, hashlib.sha256).digest()[:_MAC_LEN]
    if not hmac.compare_digest(mac, expect):
        raise LicenseError("invalid")
    ver, exp, iat = struct.unpack(">BII", body[:9])
    if ver != 1:
        raise LicenseError("invalid")
    ts = float(now if now is not None else time.time())
    info = {
        "expires_at": float(exp),
        "issued_at": float(iat),
        "days": max(1, int(round((exp - iat) / 86400))),
    }
    if ts >= float(exp):
        raise LicenseError("expired")
    return info


def is_required() -> bool:
    skip = os.getenv("DYFLOW_LICENSE_SKIP", "").strip().lower()
    if skip in ("1", "true", "yes"):
        return False
    force = os.getenv("DYFLOW_LICENSE", "").strip().lower()
    if force in ("1", "true", "yes"):
        return True
    try:
        from .desktop_mode import enabled as desktop_on
    except Exception:  # noqa: BLE001
        return False
    return bool(getattr(sys, "frozen", False)) and desktop_on()


def gate_ok() -> bool:
    if not is_required():
        return True
    return current() is not None


def license_path() -> Path:
    env = (os.getenv("VC_LICENSE_FILE") or "").strip()
    if env:
        return Path(env)
    from .desktop_mode import data_dir

    return data_dir() / "license.json"


def save_key(key: str) -> None:
    path = license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"key": key.strip(), "activated_at": time.time()}, indent=2),
        encoding="utf-8",
    )


def load_saved_key() -> str:
    path = license_path()
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(data, dict):
        return str(data.get("key") or "").strip()
    return ""


def activate(key: str, now: float | None = None) -> dict[str, Any]:
    text = (key or "").strip()
    if not text:
        raise LicenseError("empty")
    info = decode(text, now=now)
    save_key(text)
    return info


def current(now: float | None = None) -> dict[str, Any] | None:
    key = load_saved_key()
    if not key:
        return None
    try:
        return decode(key, now=now)
    except LicenseError:
        return None


def public_status(now: float | None = None) -> dict[str, Any]:
    ts = float(now if now is not None else time.time())
    req = is_required()
    empty = {
        "required": req,
        "ok": True,
        "reason": "skipped",
        "expires_at": None,
        "days_left": None,
        "until_label": "",
    }
    if not req:
        return empty
    info = current(now=ts)
    if info:
        left = max(0, int((info["expires_at"] - ts) // 86400))
        return {
            "required": req,
            "ok": True,
            "reason": "ok",
            "expires_at": info["expires_at"],
            "days_left": left,
            "until_label": _date_label(info["expires_at"]),
        }
    key = load_saved_key()
    reason = "missing"
    exp = None
    if key:
        try:
            decode(key, now=ts)
        except LicenseError as e:
            reason = e.code
            if e.code == "expired":
                try:
                    token = _token_body(key)
                    pad = (-len(token)) % 8
                    raw = base64.b32decode(token + ("=" * pad), casefold=True)
                    _, exp_i, _ = struct.unpack(">BII", raw[:9])
                    exp = float(exp_i)
                except Exception:  # noqa: BLE001
                    exp = None
    return {
        "required": req,
        "ok": False,
        "reason": reason,
        "expires_at": exp,
        "days_left": 0 if reason == "expired" else None,
        "until_label": _date_label(exp) if exp else "",
    }


def block_message(lang: str = "vi") -> str:
    st = public_status()
    if st["reason"] == "expired":
        if lang == "en":
            return "License expired. Paste a new key to continue."
        return "Key da het han. Dan key moi de tiep tuc."
    if lang == "en":
        return "Paste a license key to use VideosCreator."
    return "Dan license key de dung VideosCreator."


def _date_label(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%d/%m/%Y", time.localtime(float(ts)))
    except (OverflowError, OSError, ValueError):
        return ""


def _cli(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(
            "Mint license desktop (gui key cho khach):\n"
            "  python -m dyflow.license_key 30\n"
            "  python -m dyflow.license_key 12m\n"
            "  python -m dyflow.license_key 1y\n"
            "  python -m dyflow.license_key verify VC1-....\n"
        )
        return 0
    if argv[1] == "verify":
        key = " ".join(argv[2:]).strip()
        try:
            info = decode(key)
        except LicenseError as e:
            print(f"FAIL: {e.code}", file=sys.stderr)
            return 1
        print(f"OK het han {_date_label(info['expires_at'])} ({info['days']} ngay)")
        return 0
    try:
        days = parse_duration(argv[1])
        key, info = mint(days)
    except ValueError as e:
        print(f"Loi: {e}", file=sys.stderr)
        return 1
    print(key)
    print(f"Han: {_date_label(info['expires_at'])} ({info['days']} ngay)")
    print("Gui dung dong key cho khach — het han thi dan key moi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
