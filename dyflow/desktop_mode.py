"""Che do app desktop: 1 may, khong credit, data nam o thu muc user."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def enabled() -> bool:
    return os.getenv("DYFLOW_DESKTOP", "").strip().lower() in ("1", "true", "yes")


def resource_root() -> Path:
    """Thu muc chua config.yaml + templates khi chay source hoac PyInstaller."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    path = base / "VideosCreator"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_font() -> str:
    if sys.platform == "win32":
        p = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
        return str(p)
    if sys.platform == "darwin":
        return "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
    for cand in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if Path(cand).exists():
            return cand
    return ""


def extra_bin_dir() -> Path | None:
    """ffmpeg.exe di kem .exe (cung thu muc)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def prepare() -> Path:
    """Tao thu muc data, copy config, chdir. Goi TRUOC khi import web app."""
    os.environ["DYFLOW_DESKTOP"] = "1"
    os.environ.setdefault("DYFLOW_WEB_WORKERS", "1")
    root = data_dir()
    work = root / "work"
    out = root / "output"
    work.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)

    bins = extra_bin_dir()
    if bins and bins.is_dir():
        os.environ["PATH"] = str(bins) + os.pathsep + os.environ.get("PATH", "")

    cfg_path = root / "config.yaml"
    bundled = resource_root() / "config.yaml"
    if not cfg_path.exists() and bundled.exists():
        shutil.copy2(bundled, cfg_path)
    _rewrite_paths(cfg_path, work, out)
    os.environ["DYFLOW_CONFIG"] = str(cfg_path)

    env_path = root / ".env"
    example = resource_root() / ".env.example"
    if not env_path.exists() and example.exists():
        shutil.copy2(example, env_path)
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except Exception:
        pass

    os.chdir(root)
    return root


def _rewrite_paths(cfg_path: Path, work: Path, out: Path) -> None:
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    font = default_font()
    lines = []
    for line in text.splitlines(True) if text else []:
        if line.startswith("  work_dir:"):
            line = f'  work_dir: "{work.as_posix()}"\n'
        elif line.startswith("  output_dir:"):
            line = f'  output_dir: "{out.as_posix()}"\n'
        elif line.startswith("  font_path:") and font:
            line = f'  font_path: "{font}"\n'
        elif line.startswith("  workers:"):
            line = "  workers: 1\n"
        elif line.startswith("  retention_days:"):
            line = "  retention_days: 7\n"
        lines.append(line)
    if lines:
        cfg_path.write_text("".join(lines), encoding="utf-8")
