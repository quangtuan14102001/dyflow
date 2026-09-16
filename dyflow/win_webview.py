"""Cua so desktop Windows (WebView2 native). Khong dung pythonnet / trinh duyet."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def _dll_path() -> Path | None:
    names = ("VideosCreatorWebView.dll",)
    cands: list[Path] = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass))
    cands.append(Path(__file__).resolve().parents[1] / "dist" / "native")
    for folder in cands:
        for name in names:
            p = folder / name
            if p.is_file():
                return p
    return None


def run_desktop(url: str, title: str = "VideosCreator", icon: str | None = None) -> None:
    dll_path = _dll_path()
    if dll_path is None:
        raise RuntimeError(
            "Thieu VideosCreatorWebView.dll. Tai lai ban zip Windows moi."
        )
    from dyflow.desktop_mode import data_dir

    user_data = data_dir() / "webview2"
    user_data.mkdir(parents=True, exist_ok=True)
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
    rc = dll.vc_webview_run(
        url,
        title,
        str(user_data),
        icon or "",
    )
    if rc == 2:
        raise RuntimeError(
            "Can Microsoft Edge WebView2 Runtime.\n"
            "Cai tai: https://go.microsoft.com/fwlink/p/?LinkId=2124703"
        )
    if rc != 0:
        raise RuntimeError(f"Khong mo duoc cua so desktop (ma {rc}).")
