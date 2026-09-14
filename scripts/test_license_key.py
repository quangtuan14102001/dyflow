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
    current,
    decode,
    gate_ok,
    mint,
    parse_duration,
    public_status,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def main() -> int:
    path = Path(os.environ["VC_LICENSE_FILE"])
    if path.exists():
        path.unlink()

    _assert(parse_duration("30") == 30, "30 days")
    _assert(parse_duration("12m") == 360, "12m")
    _assert(parse_duration("1y") == 365, "1y")

    frozen = 1_700_000_000
    key, info = mint(30, now=frozen)
    _assert(key.startswith("VC1-"), key)
    got = decode(key, now=frozen + 10)
    _assert(got["expires_at"] == info["expires_at"], "exp match")

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

    path.write_text('{"key": "%s"}' % key, encoding="utf-8")
    st2 = public_status(now=info["expires_at"] + 5)
    _assert(not st2["ok"] and st2["reason"] == "expired", st2)

    print("OK license_key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
