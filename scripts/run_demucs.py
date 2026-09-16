#!/usr/bin/env python3
"""Chay demucs sau khi patch stdlib thread.py (Homebrew tung bi hong)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.stdlib_fix import ensure_thread_stdlib  # noqa: E402

ensure_thread_stdlib()

from demucs.separate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
