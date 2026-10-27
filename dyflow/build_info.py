"""Nhan ban build â€” de biet chac dang chay zip nao (CI ghi de khi dong goi)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# CI / build_windows.ps1 ghi de dong duoi + file build.txt canh .exe
BUILD_TAG = "260921-1035.local"



def build_tag() -> str:
    env = (os.getenv("DYFLOW_BUILD_TAG") or "").strip()
    if env:
        return env
    if getattr(sys, "frozen", False):
        marker = Path(sys.executable).resolve().parent / "build.txt"
        try:
            text = marker.read_text(encoding="utf-8").strip()
            if text:
                return text.splitlines()[0][:32]
        except OSError:
            pass
    return BUILD_TAG



