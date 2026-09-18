"""Launch Edge/Chrome — never relaunch VideosCreator.exe.

Frozen PyInstaller: p.chromium.launch() without a real browser path can
start sys.executable (the app) again. That opens extra VideosCreator
windows that immediately quit (Playwright --type= / singleton).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .utils import log


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_self_exe(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        p = Path(path).resolve()
        self = Path(sys.executable).resolve()
        if p == self:
            return True
        name = p.name.lower()
        return name.startswith("videoscreator") and name.endswith(".exe")
    except OSError:
        return False


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
                if p.is_file() and not _is_self_exe(p):
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
        if Path(p).is_file() and not _is_self_exe(p):
            return p
    return None


def _channel_for_exe(exe: str | None) -> str | None:
    """Playwright channel= finds system Edge/Chrome without touching our .exe."""
    if not exe:
        return None
    name = Path(exe).name.lower()
    if name in {"msedge.exe", "microsoft edge"}:
        return "msedge"
    if name in {"chrome.exe", "google chrome", "chromium", "chromium.exe"}:
        return "chrome"
    return None


def _launch_kwargs(*, headless: bool, exe: str | None) -> dict:
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
    channel = _channel_for_exe(exe)
    if channel and sys.platform == "win32":
        # Official way on Windows — never pass our frozen exe as Chromium.
        kwargs["channel"] = channel
    elif exe and not _is_self_exe(exe):
        kwargs["executable_path"] = exe
    return kwargs


def launch_playwright_chromium(playwright_sync, *, headless: bool):
    """Launch Edge/Chrome. Frozen exe never uses sys.executable as Chromium.

    Without a real browser, Playwright can start VideosCreator.exe (browser +
    GPU + renderer) — that is the open/close loop the user sees.
    """
    exe = system_browser_exe()
    if is_frozen() and not exe:
        raise RuntimeError(
            "Can Microsoft Edge (hoac Chrome) de liet ke/tai Douyin. "
            "Cai Edge, khong de app tu mo lai chinh no."
        )
    if is_frozen() and _is_self_exe(exe):
        raise RuntimeError("Browser path trung VideosCreator.exe — abort.")

    kwargs = _launch_kwargs(headless=headless, exe=exe)

    def _launch() -> object:
        if _is_self_exe(kwargs.get("executable_path")):
            raise RuntimeError("Refuse launching VideosCreator.exe as Chromium.")
        log.info(
            "Playwright launch headless=%s channel=%s exe=%s",
            headless,
            kwargs.get("channel") or "-",
            kwargs.get("executable_path") or "channel/bundled",
        )
        return playwright_sync.chromium.launch(**kwargs)

    if is_frozen():
        return _launch()
    if exe or kwargs.get("channel"):
        return _launch()
    try:
        return _launch()
    except Exception as e:
        if not exe:
            raise
        log.warning("bundled Chromium fail (%s) — thu %s", e, exe)
        kwargs = _launch_kwargs(headless=headless, exe=exe)
        kwargs.pop("channel", None)
        kwargs["executable_path"] = exe
        return _launch()


def launch_persistent_context(
    playwright_sync,
    *,
    user_data_dir: str,
    headless: bool = False,
    **extra,
):
    """Persistent profile — same Edge/Chrome rule as ephemeral launch."""
    exe = system_browser_exe()
    if is_frozen() and not exe:
        raise RuntimeError(
            "Can Microsoft Edge (hoac Chrome) de mo Douyin. Cai Edge truoc."
        )
    kwargs = _launch_kwargs(headless=headless, exe=exe)
    kwargs.update(extra)
    kwargs["user_data_dir"] = str(user_data_dir)
    if _is_self_exe(kwargs.get("executable_path")):
        raise RuntimeError("Refuse launching VideosCreator.exe as Chromium.")
    log.info(
        "Playwright persistent headless=%s channel=%s exe=%s",
        headless,
        kwargs.get("channel") or "-",
        kwargs.get("executable_path") or "channel/bundled",
    )
    return playwright_sync.chromium.launch_persistent_context(**kwargs)


def launch_headless_browser(playwright_sync):
    return launch_playwright_chromium(playwright_sync, headless=True)


def looks_like_chromium_argv(argv: list[str] | None = None) -> bool:
    """True when Playwright/Chromium relaunched our frozen .exe as a browser."""
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        return False
    joined = " ".join(args).lower()
    if any(a.startswith("--type=") for a in args):
        return True
    if any(a.startswith("--headless") for a in args):
        return True
    if "--remote-debugging-pipe" in joined or "--remote-debugging-port" in joined:
        return True
    # Main Chromium process (no --type=): need strong signal pair.
    if "--user-data-dir" in joined and "--no-sandbox" in joined:
        return True
    if "--disable-blink-features" in joined and "--no-sandbox" in joined:
        return True
    return False
