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


def main() -> int:
    from dyflow.desktop_mode import prepare

    prepare()
    os.environ["DYFLOW_DESKTOP"] = "1"
    os.environ["DYFLOW_WEB_WORKERS"] = "1"
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    os.environ.setdefault("PUBLIC_BASE_URL", "http://127.0.0.1:8765")
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
        _win_unblock_install()
        # pywebview Windows luon import pythonnet (clr). Frozen exe (ke ca
        # ARM emulation) hay fail Python.Runtime.dll — khong ep o C:.
        _log_startup(f"frozen win shell {url} from {_win_install_dir()}")
        _win_browser_shell(url)
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


def _win_browser_shell(url: str) -> None:
    """Giu process song + mo trinh duyet. Khong dung pythonnet."""
    webbrowser.open(url)
    if sys.platform != "win32":
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return
        return
    try:
        _win_status_window(url)
        return
    except Exception as e:
        _log_startup(f"status window: {e}")
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            f"VideosCreator dang chay tren trinh duyet.\n\n{url}\n\n"
            "Bam OK de thoat ung dung.",
            "VideosCreator",
            0x40,
        )
    except Exception:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return


def _win_status_window(url: str) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HCURSOR),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    WM_DESTROY = 0x0002
    WM_COMMAND = 0x0111
    WS_OVERLAPPED = 0x00CF0000
    WS_VISIBLE = 0x10000000
    WS_CHILD = 0x40000000
    WS_TABSTOP = 0x00010000
    BS_DEFPUSHBUTTON = 1
    COLOR_WINDOW = 5
    IDC_ARROW = 32512
    ID_REOPEN = 1001
    ID_QUIT = 1002
    SW_SHOW = 5

    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = LRESULT
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadCursorW.restype = wintypes.HCURSOR
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_COMMAND:
            cmd = int(wparam) & 0xFFFF
            if cmd == ID_REOPEN:
                webbrowser.open(url)
                return 0
            if cmd == ID_QUIT:
                user32.DestroyWindow(hwnd)
                return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    hinst = kernel32.GetModuleHandleW(None)
    cls_name = "VideosCreatorShell"
    wc = WNDCLASSW()
    wc.style = 0x0003
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.hCursor = user32.LoadCursorW(None, ctypes.cast(IDC_ARROW, wintypes.LPCWSTR))
    wc.hbrBackground = COLOR_WINDOW + 1
    wc.lpszClassName = cls_name
    atom = user32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        err = ctypes.get_last_error()
        if err not in (0, 1410):  # 1410 = class already exists
            raise ctypes.WinError(err)

    hwnd = user32.CreateWindowExW(
        0,
        cls_name,
        "VideosCreator",
        WS_OVERLAPPED | WS_VISIBLE,
        280,
        220,
        520,
        260,
        None,
        None,
        hinst,
        None,
    )
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    body = (
        "App dang chay tren trinh duyet mac dinh.\n"
        f"{url}\n\n"
        "Giu cua so nay mo. Dong cua so de thoat."
    )
    user32.CreateWindowExW(
        0,
        "STATIC",
        body,
        WS_CHILD | WS_VISIBLE,
        24,
        20,
        460,
        110,
        hwnd,
        None,
        hinst,
        None,
    )
    user32.CreateWindowExW(
        0,
        "BUTTON",
        "Mo lai trinh duyet",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
        24,
        150,
        200,
        36,
        hwnd,
        wintypes.HMENU(ID_REOPEN),
        hinst,
        None,
    )
    user32.CreateWindowExW(
        0,
        "BUTTON",
        "Thoat",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP,
        240,
        150,
        120,
        36,
        hwnd,
        wintypes.HMENU(ID_QUIT),
        hinst,
        None,
    )
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


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

    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception as e:
        _log_startup(f"fatal: {e}")
        _win_alert(f"{e}\n\nLog: {_startup_log()}")
        raise

