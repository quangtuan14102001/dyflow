"""Cua so desktop Windows (WebView2 native). Khong dung pythonnet / trinh duyet."""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path


def _dll_path() -> Path | None:
    names = ("VideosCreatorWebView.dll",)
    cands: list[Path] = []
    
    # 1. Thu muc exe (frozen hoac Python environment)
    exe_dir = Path(sys.executable).resolve().parent
    cands.append(exe_dir)
    cands.append(exe_dir / "_internal")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(Path(meipass))
        cands.append(Path(meipass) / "_internal")

    # 2. Thu muc du an / repo goc
    cands.append(Path(__file__).resolve().parents[1] / "dist" / "native")
    cands.append(Path(__file__).resolve().parents[1] / "dist" / "VideosCreator")
    cands.append(Path.cwd() / "dist" / "native")
    cands.append(Path.cwd() / "dist" / "VideosCreator")
    cands.append(Path(r"C:\Github\dyflow\dist\native"))
    cands.append(Path(r"C:\Github\dyflow\dist\VideosCreator"))

    # 3. Thu muc AppData / Programs cua VideosCreator
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        la = Path(local_app)
        cands.append(la / "VideosCreator")
        cands.append(la / "VideosCreator" / "patch")
        cands.append(la / "Programs" / "VideosCreator")
        cands.append(la / "Programs" / "VideosCreator" / "_internal")

    for folder in cands:
        for name in names:
            p = folder / name
            if p.is_file():
                return p
    return None


def _clean_stale_webview_locks(user_data: Path) -> None:
    """Xóa stale locks của WebView2 nếu phiên trước bị tắt đột ngột hoặc crash."""
    try:
        eb = user_data / "EBWebView"
        if not eb.is_dir():
            return
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lf = eb / lock_name
            if lf.is_file() or lf.is_symlink():
                try:
                    lf.unlink(missing_ok=True)
                except OSError:
                    pass
    except Exception:
        pass


def run_desktop(
    url: str,
    title: str = "VideosCreator",
    icon: str | None = None,
    ready_event: threading.Event | None = None,
) -> None:
    dll_path = _dll_path()
    if dll_path is None:
        raise RuntimeError(
            "Thieu VideosCreatorWebView.dll. Tai lai ban zip Windows moi."
        )
    from dyflow.desktop_mode import data_dir

    user_data = data_dir() / "webview2"
    user_data.mkdir(parents=True, exist_ok=True)
    _clean_stale_webview_locks(user_data)

    try:
        os.add_dll_directory(str(dll_path.parent))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    dll = ctypes.CDLL(str(dll_path))
    dll.vc_webview_run.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
    ]
    dll.vc_webview_run.restype = ctypes.c_int

    watchdog_killed = [False]
    stop_watchdog = threading.Event()

    def _watchdog_worker():
        # Cho toi da 7 giay de WebView2 render va client nap trang
        start = time.time()
        timeout = 7.0
        while time.time() - start < timeout:
            if stop_watchdog.is_set():
                return
            if ready_event is not None and ready_event.is_set():
                return
            time.sleep(0.3)
        if stop_watchdog.is_set() or (ready_event is not None and ready_event.is_set()):
            return
        # Neu sau 7 giay WebView2 van chua nap trang (treo / lock / loi render):
        # Dong cua so native de app tu dong fallback sang trinh duyet
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW("VideosCreatorWnd", None)
            if hwnd:
                watchdog_killed[0] = True
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:
            pass

    watchdog = threading.Thread(target=_watchdog_worker, name="wv2-watchdog", daemon=True)
    watchdog.start()

    t0 = time.time()
    try:
        rc = dll.vc_webview_run(
            url,
            title,
            str(user_data),
            icon or "",
        )
    finally:
        stop_watchdog.set()

    elapsed = time.time() - t0
    if watchdog_killed[0]:
        raise RuntimeError(
            "Giao diện WebView2 không phản hồi sau 7 giây (xung đột tài nguyên hoặc thiếu Edge runtime). "
            "Đã tự động chuyển sang Trình Duyệt Web để sử dụng ổn định."
        )
    if rc == 2:
        raise RuntimeError(
            "Cần Microsoft Edge WebView2 Runtime.\n"
            "Cài tại: https://go.microsoft.com/fwlink/p/?LinkId=2124703"
        )
    if rc != 0:
        raise RuntimeError(f"Không mở được cửa sổ desktop (mã lỗi {rc}).")
    if elapsed < 2.0:
        raise RuntimeError(
            "Cửa sổ giao diện WebView2 bị đóng bất thường khi khởi động. "
            "Chuyển sang mở trên Trình Duyệt Web để đảm bảo sử dụng ổn định."
        )
