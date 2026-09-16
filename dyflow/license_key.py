"""License desktop: dán key có hạn, không cần email.

Chủ app mint key (CLI / Telegram). File .exe kiểm tra chữ ký + ngày hết hạn.
Máy dev (chạy source, chưa đóng gói) không bắt key — trừ khi DYFLOW_LICENSE=1.

Role nằm trong body đã ký (không chỉ prefix):
  VC1-…  user
  VCA-…  admin  (tạo được key user)
  VCS-…  super  (tạo được key user + admin; super chỉ mint CLI/Telegram)
Key VC1 cũ (ver 1, chưa có role) vẫn là user.
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


PREFIX_USER = "VC1"
PREFIX_ADMIN = "VCA"
PREFIX_SUPER = "VCS"
PREFIXES = {
    "user": PREFIX_USER,
    "admin": PREFIX_ADMIN,
    "super": PREFIX_SUPER,
}
_PREFIX_ROLE = {v: k for k, v in PREFIXES.items()}
# Giữ tên cũ cho chỗ import PREFIX.
PREFIX = PREFIX_USER

ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SUPER = "super"
ROLES = (ROLE_USER, ROLE_ADMIN, ROLE_SUPER)
_ROLE_BYTE = {ROLE_USER: 0, ROLE_ADMIN: 1, ROLE_SUPER: 2}
_BYTE_ROLE = {v: k for k, v in _ROLE_BYTE.items()}

_MAC_LEN = 10
_MAX_DAYS = 3650
_ADMIN_MINT_MAX_DAYS = 365
_VER_V1 = 1
_VER_V2 = 2
_VER_V3 = 3
_BODY_V1 = 17
_BODY_V2 = 18
_BODY_V3 = 26  # header 10 + kid 8 + machine 8
_KID_LEN = 8
_MACHINE_LEN = 8
_UNBOUND = b"\x00" * _MACHINE_LEN

# Goi cap key: lan 1 free 5 ngay, lan 2 = 1 thang, roi 3/6/12 thang.
GRANT_PRESETS: tuple[dict[str, Any], ...] = (
    {"id": "trial", "days": 5, "label_key": "license_grant_trial"},
    {"id": "1m", "days": 30, "label_key": "license_grant_1m"},
    {"id": "3m", "days": 90, "label_key": "license_grant_3m"},
    {"id": "6m", "days": 180, "label_key": "license_grant_6m"},
    {"id": "1y", "days": 365, "label_key": "license_grant_1y"},
)


class LicenseError(Exception):
    """code: empty | invalid | expired | machine"""

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


def normalize_role(raw: str | None) -> str:
    s = (raw or ROLE_USER).strip().lower().replace("-", "_")
    if s in ("user", "khach", "customer"):
        return ROLE_USER
    if s in ("admin",):
        return ROLE_ADMIN
    if s in ("super", "superadmin", "super_admin"):
        return ROLE_SUPER
    raise ValueError("role: user | admin | super")


def parse_duration(raw: str, *, max_days: int | None = None) -> int:
    """'30', '30d', '12m', '1y' -> so ngay."""
    s = (raw or "").strip().lower().replace(" ", "")
    if not s:
        raise ValueError("thieu thoi han")
    aliases = {
        "trial": 5,
        "5d": 5,
        "1m": 30,
        "3m": 90,
        "6m": 180,
        "1y": 365,
        "12m": 360,
    }
    if s in aliases:
        days = aliases[s]
        cap = int(max_days if max_days is not None else _MAX_DAYS)
        if days < 1 or days > cap:
            raise ValueError(f"thoi han 1..{cap} ngay")
        return days
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
    cap = int(max_days if max_days is not None else _MAX_DAYS)
    if days < 1 or days > cap:
        raise ValueError(f"thoi han 1..{cap} ngay")
    return days


def machine_raw() -> bytes:
    """8 byte on dinh theo may (Win MachineGuid / Mac IOPlatformUUID)."""
    forced = (os.getenv("VC_MACHINE_ID") or "").strip()
    if forced:
        try:
            return parse_machine_code(forced)
        except ValueError:
            return hashlib.sha256(forced.encode("utf-8")).digest()[:_MACHINE_LEN]
    parts = [sys.platform]
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                parts.append(str(guid))
        elif sys.platform == "darwin":
            import subprocess

            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
                timeout=4,
            ).decode("utf-8", "ignore")
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts.append(line.split("=", 1)[-1].strip().strip('"'))
                    break
        else:
            machine_id = Path("/etc/machine-id")
            if machine_id.is_file():
                parts.append(machine_id.read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        pass
    if len(parts) < 2:
        import socket

        parts.append(socket.gethostname())
        parts.append(str(os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or ""))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return digest[:_MACHINE_LEN]


def format_machine_code(raw: bytes | None = None) -> str:
    blob = raw if raw is not None else machine_raw()
    if not blob or blob == _UNBOUND:
        return ""
    token = base64.b32encode(blob).decode("ascii").rstrip("=")
    return "-".join(token[i : i + 4] for i in range(0, len(token), 4))


def parse_machine_code(raw: str) -> bytes:
    compact = "".join(c for c in (raw or "") if c.isalnum()).upper()
    if not compact:
        raise ValueError("thieu ma may")
    pad = (-len(compact)) % 8
    try:
        blob = base64.b32decode(compact + ("=" * pad), casefold=True)
    except Exception as e:  # noqa: BLE001
        raise ValueError("ma may khong hop le") from e
    if len(blob) >= _MACHINE_LEN:
        return blob[:_MACHINE_LEN]
    raise ValueError("ma may khong hop le")


def _kid_label(kid: bytes) -> str:
    if not kid:
        return ""
    return base64.b32encode(kid).decode("ascii").rstrip("=")[:8]


def allowed_mint_roles(actor: str | None) -> tuple[str, ...]:
    if actor == ROLE_SUPER:
        return (ROLE_USER, ROLE_ADMIN)
    if actor == ROLE_ADMIN:
        return (ROLE_USER,)
    return ()


def mint_max_days(actor: str | None) -> int:
    if actor == ROLE_SUPER:
        return _MAX_DAYS
    if actor == ROLE_ADMIN:
        return _ADMIN_MINT_MAX_DAYS
    return 0


def _mint_fields(role: str | None) -> dict[str, Any]:
    allowed = allowed_mint_roles(role)
    return {
        "role": role,
        "can_mint": bool(allowed),
        "can_mint_admin": ROLE_ADMIN in allowed,
        "mint_roles": list(allowed),
        "mint_max_days": mint_max_days(role),
        "grant_presets": [dict(p) for p in GRANT_PRESETS],
        "machine_code": format_machine_code(),
    }


def mint(
    days: int,
    role: str = ROLE_USER,
    now: float | None = None,
    *,
    kid: bytes | None = None,
    machine: bytes | str | None = None,
    expires_at: float | None = None,
    issued_at: float | None = None,
) -> tuple[str, dict[str, Any]]:
    role = normalize_role(role)
    days = int(days)
    if days < 1 or days > _MAX_DAYS:
        raise ValueError(f"thoi han 1..{_MAX_DAYS} ngay")
    iat = int(issued_at if issued_at is not None else (now if now is not None else time.time()))
    if expires_at is not None:
        exp = int(expires_at)
        days = max(1, int(round((exp - iat) / 86400)))
    else:
        exp = iat + days * 86400
    role_b = _ROLE_BYTE[role]
    kid_b = kid if kid and len(kid) == _KID_LEN else os.urandom(_KID_LEN)
    if isinstance(machine, str):
        mach = parse_machine_code(machine) if machine.strip() else _UNBOUND
    elif isinstance(machine, (bytes, bytearray)) and len(machine) == _MACHINE_LEN:
        mach = bytes(machine)
    else:
        mach = _UNBOUND
    body = struct.pack(">BBII", _VER_V3, role_b, exp, iat) + kid_b + mach
    mac = hmac.new(_secret(), body, hashlib.sha256).digest()[:_MAC_LEN]
    token = base64.b32encode(body + mac).decode("ascii").rstrip("=")
    key = _group(PREFIXES[role], token)
    return key, {
        "expires_at": float(exp),
        "issued_at": float(iat),
        "days": days,
        "role": role,
        "kid": _kid_label(kid_b),
        "machine_code": format_machine_code(mach),
        "bound": mach != _UNBOUND,
    }


def mint_legacy_v1(days: int, now: float | None = None) -> tuple[str, dict[str, Any]]:
    """Key VC1 ver 1 (không role) — chỉ để tương thích / test."""
    days = int(days)
    if days < 1 or days > _MAX_DAYS:
        raise ValueError(f"thoi han 1..{_MAX_DAYS} ngay")
    iat = int(now if now is not None else time.time())
    exp = iat + days * 86400
    body = struct.pack(">BII", _VER_V1, exp, iat) + os.urandom(8)
    mac = hmac.new(_secret(), body, hashlib.sha256).digest()[:_MAC_LEN]
    token = base64.b32encode(body + mac).decode("ascii").rstrip("=")
    key = _group(PREFIX_USER, token)
    return key, {
        "expires_at": float(exp),
        "issued_at": float(iat),
        "days": days,
        "role": ROLE_USER,
    }


def _group(prefix: str, token: str) -> str:
    chunks = [token[i : i + 5] for i in range(0, len(token), 5)]
    return prefix + "-" + "-".join(chunks)


def _token_body(key: str) -> tuple[str, str]:
    compact = "".join(c for c in (key or "") if c.isalnum())
    if len(compact) < 8:
        raise LicenseError("invalid")
    head = compact[:3].upper()
    if head not in _PREFIX_ROLE:
        raise LicenseError("invalid")
    return compact[3:], _PREFIX_ROLE[head]


def decode(
    key: str,
    now: float | None = None,
    *,
    allow_expired: bool = False,
    check_machine: bool = True,
) -> dict[str, Any]:
    token, prefix_role = _token_body(key)
    pad = (-len(token)) % 8
    try:
        raw = base64.b32decode(token + ("=" * pad), casefold=True)
    except Exception as e:  # noqa: BLE001
        raise LicenseError("invalid") from e
    if len(raw) < 1 + 8 + _MAC_LEN:
        raise LicenseError("invalid")
    ver = raw[0]
    kid = b""
    mach = _UNBOUND
    if ver == _VER_V1:
        body_len = _BODY_V1
        role = ROLE_USER
    elif ver == _VER_V2:
        body_len = _BODY_V2
        role = None
    elif ver == _VER_V3:
        body_len = _BODY_V3
        role = None
    else:
        raise LicenseError("invalid")
    if len(raw) < body_len + _MAC_LEN:
        raise LicenseError("invalid")
    body, mac = raw[:body_len], raw[body_len : body_len + _MAC_LEN]
    expect = hmac.new(_secret(), body, hashlib.sha256).digest()[:_MAC_LEN]
    if not hmac.compare_digest(mac, expect):
        raise LicenseError("invalid")
    if ver == _VER_V1:
        _, exp, iat = struct.unpack(">BII", body[:9])
    elif ver == _VER_V2:
        _, role_b, exp, iat = struct.unpack(">BBII", body[:10])
        role = _BYTE_ROLE.get(int(role_b))
        if role is None:
            raise LicenseError("invalid")
    else:
        _, role_b, exp, iat = struct.unpack(">BBII", body[:10])
        role = _BYTE_ROLE.get(int(role_b))
        if role is None:
            raise LicenseError("invalid")
        kid = body[10:18]
        mach = body[18:26]
    if role != prefix_role:
        raise LicenseError("invalid")
    if check_machine and mach != _UNBOUND and mach != machine_raw():
        raise LicenseError("machine")
    ts = float(now if now is not None else time.time())
    info = {
        "expires_at": float(exp),
        "issued_at": float(iat),
        "days": max(1, int(round((exp - iat) / 86400))),
        "role": role,
        "kid": _kid_label(kid),
        "kid_raw": kid,
        "machine_raw": mach,
        "machine_code": format_machine_code(mach),
        "bound": mach != _UNBOUND,
    }
    if ts >= float(exp) and not allow_expired:
        raise LicenseError("expired")
    return info


def _peek_expiry(key: str) -> float | None:
    try:
        token, _ = _token_body(key)
        pad = (-len(token)) % 8
        raw = base64.b32decode(token + ("=" * pad), casefold=True)
        ver = raw[0]
        if ver == _VER_V1:
            _, exp_i, _ = struct.unpack(">BII", raw[:9])
        elif ver in (_VER_V2, _VER_V3):
            _, _, exp_i, _ = struct.unpack(">BBII", raw[:10])
        else:
            return None
        return float(exp_i)
    except Exception:  # noqa: BLE001
        return None


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
    info = decode(text, now=now, check_machine=True)
    bound_key, info = _bind_to_this_machine(text, info, now=now)
    save_key(bound_key)
    return info


def _bind_to_this_machine(
    key: str,
    info: dict[str, Any],
    now: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Key chua gan may -> ky lai dung ma may hien tai (1 key = 1 may)."""
    mach = info.get("machine_raw") or _UNBOUND
    here = machine_raw()
    if mach != _UNBOUND:
        if mach != here:
            raise LicenseError("machine")
        return key, info
    kid = info.get("kid_raw") or os.urandom(_KID_LEN)
    bound, bound_info = mint(
        int(info.get("days") or 1),
        role=str(info.get("role") or ROLE_USER),
        kid=kid,
        machine=here,
        issued_at=info.get("issued_at"),
        expires_at=info.get("expires_at"),
        now=now,
    )
    return bound, bound_info


def renew(
    old_key: str,
    days: int,
    machine: bytes | str | None = None,
    now: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Gia han cung kid + may (cho phep key het han). machine moi = chuyen may."""
    info = decode(old_key, now=now, allow_expired=True, check_machine=False)
    kid = info.get("kid_raw") or os.urandom(_KID_LEN)
    if machine is None or (isinstance(machine, str) and not machine.strip()):
        mach: bytes | str | None = info.get("machine_raw") or _UNBOUND
    else:
        mach = machine
    return mint(
        days,
        role=str(info.get("role") or ROLE_USER),
        now=now,
        kid=kid,
        machine=mach,
    )


def current(now: float | None = None) -> dict[str, Any] | None:
    key = load_saved_key()
    if not key:
        return None
    try:
        return decode(key, now=now)
    except LicenseError:
        return None


def license_role_or_dev(now: float | None = None) -> str:
    """Role gắn vào user desktop: theo key đang kích hoạt, hoặc super khi chạy source."""
    info = current(now=now)
    if info:
        return str(info.get("role") or ROLE_USER)
    if not is_required():
        return ROLE_SUPER
    return ROLE_USER


def actor_role_for(user: dict | None = None, now: float | None = None) -> str | None:
    """Ai đang được mint key: ưu tiên key đã kích hoạt, rồi cờ user, rồi máy dev."""
    info = current(now=now)
    if info:
        return str(info.get("role") or ROLE_USER)
    if user:
        if int(user.get("is_super_admin") or 0) == 1:
            return ROLE_SUPER
        if int(user.get("is_admin") or 0) == 1:
            return ROLE_ADMIN
    if not is_required():
        try:
            from .desktop_mode import enabled as desktop_on

            if desktop_on():
                return ROLE_SUPER
        except Exception:  # noqa: BLE001
            pass
    return None


def caps_for_user(user: dict | None = None, now: float | None = None) -> dict[str, Any]:
    return _mint_fields(actor_role_for(user, now=now))


def public_status(now: float | None = None, user: dict | None = None) -> dict[str, Any]:
    ts = float(now if now is not None else time.time())
    req = is_required()
    caps = caps_for_user(user, now=ts)
    try:
        from .user_openai import has_tts_key

        has_oa = has_tts_key()
    except Exception:  # noqa: BLE001
        has_oa = False
    extra = {
        "machine_code": format_machine_code(),
        "key_machine_code": "",
        "kid": "",
        "bound": False,
        "has_openai_tts_key": has_oa,
    }
    empty = {
        "required": req,
        "ok": True,
        "reason": "skipped",
        "expires_at": None,
        "days_left": None,
        "until_label": "",
        **caps,
        **extra,
    }
    if not req:
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
                **_mint_fields(str(info.get("role") or ROLE_USER)),
                **extra,
                "key_machine_code": str(info.get("machine_code") or ""),
                "kid": str(info.get("kid") or ""),
                "bound": bool(info.get("bound")),
            }
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
            **_mint_fields(str(info.get("role") or ROLE_USER)),
            **extra,
            "key_machine_code": str(info.get("machine_code") or ""),
            "kid": str(info.get("kid") or ""),
            "bound": bool(info.get("bound")),
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
                exp = _peek_expiry(key)
    st = {
        "required": req,
        "ok": False,
        "reason": reason,
        "expires_at": exp,
        "days_left": 0 if reason == "expired" else None,
        "until_label": _date_label(exp) if exp else "",
        **_mint_fields(None),
        **extra,
    }
    return st


def block_message(lang: str = "vi") -> str:
    st = public_status()
    if st["reason"] == "expired":
        if lang == "en":
            return "License expired. Paste a renewed key to continue."
        return "Key da het han. Dan key gia han de tiep tuc."
    if st["reason"] == "machine":
        if lang == "en":
            return "This key is locked to another computer."
        return "Key nay da gan may khac. Lien he admin de chuyen may."
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
            "Mint license desktop (1 key = 1 may):\n"
            "  python -m dyflow.license_key machine\n"
            "  python -m dyflow.license_key 5d user MA-MAY\n"
            "  python -m dyflow.license_key 1m user MA-MAY\n"
            "  python -m dyflow.license_key 3m admin MA-MAY\n"
            "  python -m dyflow.license_key 1y super MA-MAY\n"
            "  python -m dyflow.license_key renew VC1-.... 1m\n"
            "  python -m dyflow.license_key verify VC1-....\n"
            "Goi: 5d (lan 1 free) | 1m | 3m | 6m | 1y\n"
        )
        return 0
    if argv[1] == "machine":
        print(format_machine_code())
        return 0
    if argv[1] == "verify":
        key = " ".join(argv[2:]).strip()
        try:
            info = decode(key, allow_expired=True, check_machine=False)
        except LicenseError as e:
            print(f"FAIL: {e.code}", file=sys.stderr)
            return 1
        print(
            f"OK role={info['role']} kid={info.get('kid') or '-'} "
            f"may={info.get('machine_code') or 'chua gan'} "
            f"het han {_date_label(info['expires_at'])} ({info['days']} ngay)"
        )
        return 0
    if argv[1] == "renew":
        if len(argv) < 4:
            print("Dung: python -m dyflow.license_key renew KEY 1m [MA-MAY]", file=sys.stderr)
            return 1
        try:
            days = parse_duration(argv[3])
            machine = argv[4] if len(argv) > 4 else None
            key, info = renew(argv[2], days, machine=machine)
        except (ValueError, LicenseError) as e:
            print(f"Loi: {e}", file=sys.stderr)
            return 1
        print(key)
        print(
            f"Gia han role={info['role']} kid={info.get('kid')} "
            f"may={info.get('machine_code') or '-'} "
            f"Han: {_date_label(info['expires_at'])} ({info['days']} ngay)"
        )
        return 0
    try:
        days = parse_duration(argv[1])
        role = ROLE_USER
        machine = None
        rest = argv[2:]
        if rest and rest[0].lower() in ROLES:
            role = normalize_role(rest[0])
            rest = rest[1:]
        if rest:
            machine = rest[0]
        key, info = mint(days, role=role, machine=machine)
    except ValueError as e:
        print(f"Loi: {e}", file=sys.stderr)
        return 1
    print(key)
    print(
        f"Role: {info['role']}  kid={info.get('kid')}  "
        f"may={info.get('machine_code') or 'CHUA GAN — nen nhap ma may'}  "
        f"Han: {_date_label(info['expires_at'])} ({info['days']} ngay)"
    )
    print("Gui dung dong key cho khach. Het han: renew cung kid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
