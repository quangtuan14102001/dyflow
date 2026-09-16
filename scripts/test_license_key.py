#!/usr/bin/env python3
"""Mint / verify / het han license desktop.

  python scripts/test_license_key.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DYFLOW_LICENSE"] = "1"
os.environ["VC_LICENSE_FILE"] = str(Path(tempfile.gettempdir()) / "vc_license_test.json")

from dyflow.license_key import (  # noqa: E402
    LicenseError,
    activate,
    actor_role_for,
    allowed_mint_roles,
    current,
    decode,
    format_machine_code,
    gate_ok,
    load_saved_key,
    mint,
    mint_legacy_v1,
    parse_duration,
    public_status,
    renew,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def main() -> int:
    path = Path(os.environ["VC_LICENSE_FILE"])
    if path.exists():
        path.unlink()

    _assert(parse_duration("5d") == 5, "5d")
    _assert(parse_duration("3m") == 90, "3m")
    _assert(parse_duration("6m") == 180, "6m")
    _assert(parse_duration("12m") == 360, "12m")
    _assert(parse_duration("1y") == 365, "1y")
    _assert(allowed_mint_roles("super") == ("user", "admin"), "super mint")
    _assert(allowed_mint_roles("admin") == ("user",), "admin mint")
    _assert(allowed_mint_roles("user") == (), "user mint")

    frozen = 1_700_000_000
    key, info = mint(30, now=frozen)
    _assert(key.startswith("VC1-"), key)
    _assert(info["role"] == "user", info)
    got = decode(key, now=frozen + 10)
    _assert(got["expires_at"] == info["expires_at"], "exp match")
    _assert(got["role"] == "user", got)

    admin_key, admin_info = mint(30, role="admin", now=frozen)
    _assert(admin_key.startswith("VCA-"), admin_key)
    _assert(decode(admin_key, now=frozen + 10)["role"] == "admin", "admin role")

    super_key, super_info = mint(30, role="super", now=frozen)
    _assert(super_key.startswith("VCS-"), super_key)
    _assert(super_info["perpetual"], super_info)
    _assert(decode(super_key, now=frozen + 10)["role"] == "super", "super role")
    _assert(decode(super_key, now=frozen + 80 * 365 * 86400)["perpetual"], "super never expires")

    spoof = "VCS-" + key.split("-", 1)[1]
    try:
        decode(spoof, now=frozen + 10)
        raise SystemExit("FAIL: prefix spoof became super")
    except LicenseError as e:
        _assert(e.code == "invalid", e.code)

    v1, v1_info = mint_legacy_v1(30, now=frozen)
    _assert(v1.startswith("VC1-"), v1)
    v1_got = decode(v1, now=frozen + 10)
    _assert(v1_got["role"] == "user", v1_got)
    _assert(v1_got["expires_at"] == v1_info["expires_at"], "v1 exp")

    try:
        decode(key, now=info["expires_at"] + 1)
        raise SystemExit("FAIL: expired key still valid")
    except LicenseError as e:
        _assert(e.code == "expired", e.code)

    try:
        decode("VC1-AAAAA-BBBBB-CCCCC")
        raise SystemExit("FAIL: garbage key valid")
    except LicenseError as e:
        _assert(e.code == "invalid", e.code)

    live, live_info = mint(30)
    _assert(current() is None, "no file")
    _assert(not gate_ok(), "gate closed")
    activate(live)
    _assert(current() is not None, "saved")
    _assert(gate_ok(), "gate open")
    st = public_status()
    _assert(st["ok"] and st["days_left"] >= 29, st)
    _assert(st["expires_at"] == live_info["expires_at"], "live exp")
    _assert(st["role"] == "user", st)
    _assert(not st["can_mint"], st)

    live_super, _ = mint(30, role="super")
    activate(live_super)
    st_super = public_status()
    _assert(st_super["role"] == "super" and st_super["can_mint_admin"], st_super)
    _assert(st_super["perpetual"] and st_super["days_left"] is None, st_super)
    _assert(actor_role_for() == "super", "actor super")

    path.write_text('{"key": "%s"}' % key, encoding="utf-8")
    st2 = public_status(now=info["expires_at"] + 5)
    _assert(not st2["ok"] and st2["reason"] == "expired", st2)
    _assert(st2["until_label"], "expired date")

    here = format_machine_code()
    locked, locked_info = mint(5, machine=here, now=frozen)
    _assert(locked_info["bound"] and locked_info["machine_code"], locked_info)
    _assert(decode(locked, now=frozen + 10)["bound"], "this machine ok")
    alien, _ = mint(5, machine=b"\x11" * 8, now=frozen)
    try:
        decode(alien, now=frozen + 10)
        raise SystemExit("FAIL: other-machine key valid here")
    except LicenseError as e:
        _assert(e.code == "machine", e.code)
    _assert(
        decode(alien, now=frozen + 10, check_machine=False)["bound"],
        "inspect other machine",
    )
    moved = activate(alien, now=frozen + 10)
    _assert(moved["machine_code"] == here, moved)
    _assert(moved["kid"] == decode(alien, now=frozen + 10, check_machine=False)["kid"], "keep kid")
    _assert(decode(load_saved_key(), now=frozen + 10)["bound"], "seat moved here")

    old, old_info = mint(1, machine=here, now=frozen)
    new, new_info = renew(old, 30, now=old_info["expires_at"] + 50)
    _assert(new_info["kid"] == old_info["kid"], "renew keeps kid")
    _assert(new_info["machine_code"] == old_info["machine_code"], "renew keeps machine")
    _assert(new != old, "renew new token")
    got_new = decode(new, now=old_info["expires_at"] + 50)
    _assert(got_new["days"] >= 29, got_new)

    print("OK license_key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
