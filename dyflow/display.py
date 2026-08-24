"""Dam bao co DISPLAY ao (Xvfb) khi chay Chrome headed tren VPS khong man hinh."""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path

from .utils import log

_XVFB_PROC: subprocess.Popen | None = None
_OWNED_DISPLAY: str | None = None


def _display_alive(display: str) -> bool:
    """Kiem tra X server dang lang nghe (vd :99)."""
    d = display.lstrip(":")
    if not d.isdigit():
        return False
    sock = Path(f"/tmp/.X11-unix/X{d}")
    return sock.exists()


def ensure_display(preferred: str = ":99") -> str | None:
    """Neu khong co DISPLAY (hoac chet), khoi Xvfb va set os.environ['DISPLAY'].

    Tra ve display dang dung, hoac None neu khong can / khong the tao.
    An toan goi nhieu lan; chi spawn 1 Xvfb cho process.
    """
    global _XVFB_PROC, _OWNED_DISPLAY

    current = (os.environ.get("DISPLAY") or "").strip()
    if current and _display_alive(current):
        return current
    if current and not _display_alive(current):
        log.warning("DISPLAY=%s khong song -> thu Xvfb", current)

    if _OWNED_DISPLAY and _display_alive(_OWNED_DISPLAY):
        os.environ["DISPLAY"] = _OWNED_DISPLAY
        return _OWNED_DISPLAY

    if not shutil.which("Xvfb"):
        log.warning("Khong co Xvfb — Chrome headed se fail neu thieu man hinh")
        return current or None

    display = preferred if preferred.startswith(":") else f":{preferred}"
    # Tim slot trong neu preferred dang dung boi process khac
    for n in range(99, 120):
        cand = f":{n}"
        if not _display_alive(cand):
            display = cand
            break

    log.info("Khoi Xvfb %s cho Chrome headed...", display)
    try:
        _XVFB_PROC = subprocess.Popen(
            [
                "Xvfb", display,
                "-screen", "0", "1280x900x24",
                "-ac",
                "+extension", "RANDR",
                "-nolisten", "tcp",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log.warning("Khong start duoc Xvfb: %s", e)
        _XVFB_PROC = None
        return None

    # Cho socket xuat hien
    for _ in range(50):
        if _display_alive(display):
            break
        if _XVFB_PROC.poll() is not None:
            log.warning("Xvfb thoat som (code=%s)", _XVFB_PROC.returncode)
            _XVFB_PROC = None
            return None
        time.sleep(0.1)
    else:
        log.warning("Xvfb timeout — khong thay socket %s", display)
        _XVFB_PROC.terminate()
        _XVFB_PROC = None
        return None

    os.environ["DISPLAY"] = display
    _OWNED_DISPLAY = display
    atexit.register(_shutdown_xvfb)
    return display


def _shutdown_xvfb() -> None:
    global _XVFB_PROC, _OWNED_DISPLAY
    proc = _XVFB_PROC
    _XVFB_PROC = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    _OWNED_DISPLAY = None
