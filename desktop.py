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


def _wait_up(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(0.25)
    raise RuntimeError(f"Server khong mo duoc {url}: {last}")


def main() -> int:
    from dyflow.desktop_mode import prepare

    prepare()
    os.environ["DYFLOW_DESKTOP"] = "1"
    os.environ.setdefault("PUBLIC_BASE_URL", "http://127.0.0.1:8765")

    from dyflow.stdlib_fix import ensure_thread_stdlib

    ensure_thread_stdlib()

    import uvicorn
    from dyflow.web.app import app

    host = "127.0.0.1"
    port = int(os.getenv("DYFLOW_DESKTOP_PORT") or "8765")
    url = f"http://{host}:{port}/"

    def _serve() -> None:
        uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)

    t = threading.Thread(target=_serve, name="dyflow-uvicorn", daemon=True)
    t.start()
    _wait_up(url)

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

    webview.create_window(
        "VideosCreator",
        url,
        width=1280,
        height=840,
        min_size=(900, 600),
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
