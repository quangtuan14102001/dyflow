"""Tien ich dung chung."""
from __future__ import annotations
import json
import logging
import random
import shutil
import subprocess
from pathlib import Path

import yaml

log = logging.getLogger("dyflow")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


def load_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_jobs(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def deep_merge(base: dict, override: dict) -> dict:
    """Tron override (cua tung job) len config goc."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Chay 1 command, raise neu loi, log gon."""
    log.debug("RUN: %s", " ".join(str(c) for c in cmd))
    proc = subprocess.run(
        [str(c) for c in cmd],
        capture_output=True,
        text=True,
        **kw,
    )
    if proc.returncode != 0:
        log.error("Command failed (%s):\n%s", cmd[0], proc.stderr[-2000:])
        raise RuntimeError(f"{cmd[0]} that bai (code {proc.returncode})")
    return proc


def ensure_dirs(*dirs: str | Path) -> None:
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)


def pick_music(cfg_music: dict) -> str | None:
    """Chon file nhac: chi dinh hoac random tu thu muc."""
    if not cfg_music.get("enabled"):
        return None
    explicit = cfg_music.get("file")
    if explicit and Path(explicit).exists():
        return explicit
    music_dir = Path(cfg_music.get("_music_dir", "./assets/music"))
    if not music_dir.exists():
        return None
    candidates = [
        p for p in music_dir.iterdir()
        if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac"}
    ]
    return str(random.choice(candidates)) if candidates else None


def probe_duration(path: str | Path) -> float:
    """Lay do dai video/audio (giay)."""
    proc = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def probe_has_audio(path: str | Path) -> bool:
    """True neu file co it nhat 1 audio stream."""
    try:
        proc = run([
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(path),
        ])
        return bool(proc.stdout.strip())
    except Exception:
        return False


def probe_video_size(path: str | Path) -> tuple[int, int]:
    """Lay kich thuoc khung hinh video (width, height)."""
    proc = run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        str(path),
    ])
    try:
        w, h = proc.stdout.strip().split("x")
        return int(w), int(h)
    except ValueError:
        return 1080, 1920


def ytdlp_argv() -> list[str]:
    """Goi yt-dlp qua wrapper (patch stdlib + dung python venv hien tai)."""
    import sys

    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "run_ytdlp.py"
    return [sys.executable, str(wrapper)]


def check_tool(name: str) -> bool:
    if name == "yt-dlp":
        wrapper = Path(__file__).resolve().parents[1] / "scripts" / "run_ytdlp.py"
        return wrapper.is_file() or shutil.which("yt-dlp") is not None
    return shutil.which(name) is not None
