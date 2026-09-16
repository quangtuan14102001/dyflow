# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir — chay tren Windows (CI hoac VM), khong build .exe tu macOS."""
from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "config.yaml"), "."),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "dyflow" / "web" / "templates"), "dyflow/web/templates"),
    (str(ROOT / "dyflow" / "web" / "static"), "dyflow/web/static"),
    (str(ROOT / "profiles"), "profiles"),
    (str(ROOT / "scripts" / "WINDOWS_README.txt"), "."),
]
binaries: list = []
hidden: list[str] = collect_submodules("dyflow")
hidden += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "anyio._backends._asyncio",
    "dotenv",
]

for pkg in (
    "edge_tts",
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "fastapi",
    "starlette",
    "jinja2",
    "yaml",
    "torch",
    "torchaudio",
    "demucs",
    "cv2",
    "rapidocr_onnxruntime",
    "yt_dlp",
    "curl_cffi",
    "openai",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hidden)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "webview", "pythonnet", "clr", "clr_loader"],
    noarchive=False,
)
pyz = PYZ(a.pure)

_icon = ROOT / "dyflow" / "web" / "static" / "favicon.ico"

_ver = ROOT / "packaging" / "win_version_info.txt"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideosCreator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    icon=str(_icon) if _icon.exists() else None,
    version=str(_ver) if _ver.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VideosCreator",
)
