"""Patch stdlib concurrent.futures.thread neu file tren may bi hong."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def ensure_thread_stdlib() -> bool:
    """Nap ban thread.py sach tu .stdlib_fix neu import that bai. Tra True neu da patch."""
    try:
        import concurrent.futures.thread  # noqa: F401
        return False
    except (IndentationError, SyntaxError, ImportError):
        pass

    root = Path(__file__).resolve().parents[1]
    good = root / ".stdlib_fix" / "concurrent" / "futures" / "thread.py"
    if not good.exists():
        return False

    spec = importlib.util.spec_from_file_location("concurrent.futures.thread", good)
    if spec is None or spec.loader is None:
        return False
    mod = importlib.util.module_from_spec(spec)
    sys.modules["concurrent.futures.thread"] = mod
    spec.loader.exec_module(mod)
    import concurrent.futures as cf
    cf.thread = mod
    return True
