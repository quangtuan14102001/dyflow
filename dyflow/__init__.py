"""dyflow - Douyin -> edit -> long tieng Viet -> export pipeline."""
__version__ = "0.1.0"

# Homebrew Python tren may nay tung bi dan shell vao thread.py — patch som.
try:
    from .stdlib_fix import ensure_thread_stdlib
    ensure_thread_stdlib()
except Exception:  # noqa: BLE001
    pass
