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
            uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
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
    if icon:
        try:
            webview.start(icon=icon)
        except TypeError:
            webview.start()
    else:
        webview.start()
    return 0


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


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())

