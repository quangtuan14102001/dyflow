#!/usr/bin/env python3
"""Mo VideosCreator nhu app desktop (cua so + pipeline local).

Mac / Linux (dev):
  python desktop.py

Windows .exe: build bang GitHub Actions (workflow build-windows.yml)
hoac tren may Windows: powershell -File scripts/build_windows.ps1
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _no_proxy_opener():
    """Windows: proxy he thong lam GET 127.0.0.1 bi timeout."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _wait_up(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    opener = _no_proxy_opener()
    while time.time() < deadline:
        try:
            opener.open(url, timeout=2)
            return
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(0.25)
    raise RuntimeError(f"Server khong mo duoc {url}: {last}")


def _startup_log() -> Path:
    try:
        from dyflow.desktop_mode import data_dir

        return data_dir() / "startup.log"
    except Exception:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VideosCreator"
        base.mkdir(parents=True, exist_ok=True)
        return base / "startup.log"


def _log_startup(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n"
    try:
        p = _startup_log()
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(msg, flush=True)


def _win_focus_existing() -> bool:
    """Dua cua so VideosCreator dang mo len foreground. True neu tim thay."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "VideosCreator")
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _win_claim_gui() -> bool:
    """True = instance dau (duoc mo UI). Instance 2 thoat — tranh 3 cua so app."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, True, "Global\\VideosCreatorDesktopSingleton")
        return int(kernel32.GetLastError()) != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _win_already_running_exit() -> int:
    """Khi mutex da co: thu focus cua so; neu khong — bao user tat process zombie."""
    _log_startup("already running — skip extra window")
    if _win_focus_existing():
        return 0
    # Co process giu mutex nhung khong co cua so (treo sau Playwright / crash WebView).
    try:
        opener = _no_proxy_opener()
        opener.open("http://127.0.0.1:8765/", timeout=2)
        webbrowser.open("http://127.0.0.1:8765/")
        _win_alert(
            "VideosCreator dang chay ngam (khong thay cua so).\n"
            "Da mo http://127.0.0.1:8765/ tren trinh duyet.\n\n"
            "Muon cua so app: Task Manager → End task VideosCreator.exe → mo lai."
        )
        return 0
    except Exception:
        pass
    _win_alert(
        "VideosCreator dang chay nhung khong mo duoc cua so.\n\n"
        "Lam theo:\n"
        "1) Ctrl+Shift+Esc (Task Manager)\n"
        "2) End task tat ca VideosCreator.exe\n"
        "3) Mo lai VideosCreator.exe"
    )
    return 0


def main() -> int:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        if not _win_claim_gui():
            return _win_already_running_exit()
    from dyflow.desktop_mode import prepare

    prepare()
    os.environ["DYFLOW_DESKTOP"] = "1"
    os.environ["DYFLOW_WEB_WORKERS"] = "1"
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    for _k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        os.environ.pop(_k, None)
    os.environ.setdefault("PUBLIC_BASE_URL", "http://127.0.0.1:8765")
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        _win_unblock_install()
    # FastAPI startup khong start worker: uvicorn trong thread Windows
    # de soat startup. Ta start worker o main thread cho chac.
    os.environ["DYFLOW_WEB_NO_WORKER"] = "1"

    from dyflow.stdlib_fix import ensure_thread_stdlib

    ensure_thread_stdlib()

    import uvicorn
    from dyflow.web.app import app
    from dyflow.web import models, worker

    models.init_db()
    recovered = models.recover_stale(include_queued=True)
    models.ensure_desktop_owner()
    cfg_path = os.getenv("DYFLOW_CONFIG") or "config.yaml"
    if recovered:
        print(f"Da go {recovered} job cu bi ket (running) — hang doi se chay tiep.")

    host = "127.0.0.1"
    port = int(os.getenv("DYFLOW_DESKTOP_PORT") or "8765")
    url = f"http://{host}:{port}/"
    serve_err: list[BaseException] = []

    def _serve() -> None:
        try:
            _log_startup(f"uvicorn bind {host}:{port}")
            # PyInstaller: dictConfig uvicorn (formatter 'default') crash.
            uvicorn.run(
                app,
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
                log_config=None,
            )
        except Exception as e:  # noqa: BLE001
            serve_err.append(e)
            _log_startup(f"uvicorn crash: {e!r}")

    # Mo HTTP truoc, warmup worker sau — lan dau Win nạp torch co the >30s.
    t = threading.Thread(target=_serve, name="dyflow-uvicorn", daemon=True)
    t.start()
    wait_sec = 180.0 if getattr(sys, "frozen", False) else 45.0
    try:
        _wait_up(url, timeout=wait_sec)
    except RuntimeError as e:
        extra = f" | uvicorn: {serve_err[0]!r}" if serve_err else ""
        _log_startup(f"wait_up fail: {e}{extra}")
        raise RuntimeError(
            f"{e}{extra}. Tat proxy Windows cho localhost, hoac xem "
            f"{_startup_log()}"
        ) from e
    worker.start_background(cfg_path)

    if sys.platform == "win32" and getattr(sys, "frozen", False):
        from dyflow.win_webview import run_desktop

        _log_startup(f"frozen win webview {url} from {_win_install_dir()}")
        run_desktop(url, title="VideosCreator", icon=_app_icon())
        return 0

    try:
        import webview
    except ImportError:
        print("Mo trinh duyet (pip install pywebview de co cua so rieng):", url)
        webbrowser.open(url)
        try:
            while t.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 0
        return 0

    icon = _app_icon()
    try:
        win = webview.create_window(
            "VideosCreator",
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
            maximized=True,
        )
    except TypeError:
        win = webview.create_window(
            "VideosCreator",
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
        )
    if icon and win is not None:
        win.events.shown += lambda: _mac_title_icon(win, icon)
    _start_webview(icon)
    return 0


def _win_install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


def _win_unblock_install() -> None:
    """Bo Mark-of-the-Web (Downloads) — khong chan thu muc, khong ep o C:."""
    here = _win_install_dir()
    n = 0
    try:
        for p in here.rglob("*"):
            if not p.is_file():
                continue
            ads = f"{p}:Zone.Identifier"
            try:
                os.remove(ads)
                n += 1
            except OSError:
                pass
    except Exception as e:
        _log_startup(f"unblock: {e}")
        return
    if n:
        _log_startup(f"unblocked {n} files in {here}")


def _start_webview(icon: str | None) -> None:
    import webview

    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["gui"] = "edgechromium"
    if icon:
        kwargs["icon"] = icon
    try:
        webview.start(**kwargs)
        return
    except TypeError:
        kwargs.pop("icon", None)
        try:
            webview.start(**kwargs)
            return
        except TypeError:
            pass
    except Exception as e:
        raise RuntimeError(
            "Khong mo duoc cua so app. Cai Microsoft Edge WebView2 "
            "(Evergreen Runtime) roi chay lai.\n"
            f"({e})"
        ) from e
    webview.start()


def _app_icon() -> str | None:
    png = ROOT / "dyflow" / "web" / "static" / "logo-512.png"
    ico = ROOT / "dyflow" / "web" / "static" / "favicon.ico"
    try:
        from dyflow.desktop_mode import resource_root

        bundled = resource_root()
        p2 = bundled / "dyflow" / "web" / "static" / "logo-512.png"
        i2 = bundled / "dyflow" / "web" / "static" / "favicon.ico"
        if p2.is_file():
            png = p2
        if i2.is_file():
            ico = i2
    except Exception:
        pass
    if sys.platform == "win32" and ico.is_file():
        return str(ico)
    if png.is_file():
        return str(png)
    if ico.is_file():
        return str(ico)
    return None


def _mac_title_icon(window, icon_path: str) -> None:
    """Logo nho tren thanh title macOS (canh chu VideosCreator)."""
    if sys.platform != "darwin" or not icon_path:
        return
    try:
        from AppKit import NSImage
        from Foundation import NSURL

        img = NSImage.alloc().initWithContentsOfFile_(icon_path)
        native = getattr(window, "native", None)
        if img is None or native is None:
            return
        native.setRepresentedURL_(NSURL.fileURLWithPath_(icon_path))
        btn = native.standardWindowButton_(4)
        if btn is not None:
            btn.setImage_(img)
            btn.setHidden_(False)
    except Exception:
        pass


def _win_alert(msg: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg[:1500], "VideosCreator", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    import multiprocessing

    # freeze_support PHAI chay som tren Windows frozen.
    multiprocessing.freeze_support()

    # Playwright co the spawn VideosCreator.exe nhu Chromium — thoat ngay, khong UI.
    # Khong import dyflow o day (tranh crash truoc khi mo cua so).
    if getattr(sys, "frozen", False) and len(sys.argv) > 1:
        _args = sys.argv[1:]
        _join = " ".join(_args).lower()
        _is_chrome = (
            any(a.startswith("--type=") for a in _args)
            or any(a.startswith("--headless") for a in _args)
            or "--remote-debugging" in _join
            or ("--user-data-dir" in _join and "--no-sandbox" in _join)
            or ("--disable-blink-features" in _join and "--no-sandbox" in _join)
        )
        if _is_chrome:
            raise SystemExit(0)
        argv1 = _args[0]
        if argv1 == "--ytdlp":
            from dyflow.utils import ensure_media_bins, ytdlp_ffmpeg_args

            ensure_media_bins()
            rest = list(sys.argv[2:])
            if "--ffmpeg-location" not in rest:
                rest = [*ytdlp_ffmpeg_args(), *rest]
            from yt_dlp import main as ytdlp_main

            sys.argv = ["yt-dlp", *rest]
            raise SystemExit(ytdlp_main())
        if argv1 == "--demucs":
            from demucs.separate import main as demucs_main

            sys.argv = ["demucs", *sys.argv[2:]]
            raise SystemExit(demucs_main())
    try:
        raise SystemExit(main())
    except Exception as e:
        _log_startup(f"fatal: {e}")
        _win_alert(f"{e}\n\nLog: {_startup_log()}")
        raise

