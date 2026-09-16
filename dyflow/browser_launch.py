"""Launch Edge/Chrome — never relaunch VideosCreator.exe.

Frozen PyInstaller: p.chromium.launch() without executable_path can start
sys.executable (the app) again. That spawned extra VideosCreator windows.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .utils import log


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def system_browser_exe() -> str | None:
    """Installed Edge or Chrome binary — not Playwright bundled Chromium."""
    env = os.environ
    if sys.platform == "win32":
        rels = (
            Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
        )
        roots = [
            env.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)",
            env.get("PROGRAMFILES") or r"C:\Program Files",
            env.get("LOCALAPPDATA") or "",
        ]
        for root in roots:
            if not root:
                continue
            base = Path(root)
            for rel in rels:
                p = base / rel
                if p.is_file():
                    return str(p)
        return None
    for p in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/microsoft-edge",
    ):
        if Path(p).is_file():
            return p
    return None


def launch_playwright_chromium(playwright_sync, *, headless: bool):
    """Launch Edge/Chrome. Frozen exe never uses sys.executable as Chromium.

    Without executable_path, Playwright can start VideosCreator.exe 3 times
    (browser + GPU + renderer) — that is the extra-window bug.
    """
    exe = system_browser_exe()
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
    ]
    if headless:
        args += ["--disable-gpu", "--mute-audio"]
    kwargs: dict = {
        "headless": headless,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
    }

    def _launch() -> object:
        log.info(
            "Playwright launch headless=%s exe=%s",
            headless,
            kwargs.get("executable_path") or "bundled",
        )
        return playwright_sync.chromium.launch(**kwargs)

    if is_frozen():
        if not exe:
            raise RuntimeError(
                "Can Microsoft Edge de liet ke Douyin (cai Edge, khong mo lai app)."
            )
        kwargs["executable_path"] = exe
        return _launch()
    if exe:
        kwargs["executable_path"] = exe
        return _launch()
    try:
        return _launch()
    except Exception as e:
        if not exe:
            raise
        log.warning("bundled Chromium fail (%s) — thu %s", e, exe)
        kwargs["executable_path"] = exe
        return _launch()


def launch_headless_browser(playwright_sync):
    return launch_playwright_chromium(playwright_sync, headless=True)
