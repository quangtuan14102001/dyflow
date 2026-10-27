"""Tien ich dung chung."""
from __future__ import annotations
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
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
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    # PyYAML tu choi C1 controls (vd \x81 tu mojibake comment Tieng Viet).
    text = "".join(ch for ch in text if not (0x80 <= ord(ch) <= 0x9F))
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


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


def _bin_name(tool: str) -> str:
    if sys.platform == "win32" and not tool.lower().endswith(".exe"):
        return f"{tool}.exe"
    return tool


def media_bin_dir() -> Path | None:
    """Thu muc chua ffmpeg/ffprobe that (ke ca ban frozen Windows)."""
    names = (_bin_name("ffmpeg"),)
    cands: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        cands.append(exe_dir)
        cands.append(exe_dir / "_internal")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass))
            cands.append(Path(meipass) / "_internal")
    cands.append(Path(__file__).resolve().parents[1])
    cands.append(Path.cwd())
    extra = os.environ.get("DYFLOW_FFMPEG_DIR", "").strip()
    if extra:
        cands.insert(0, Path(extra))
    seen: set[str] = set()
    for folder in cands:
        try:
            key = str(folder.resolve())
        except OSError:
            key = str(folder)
        if key in seen:
            continue
        seen.add(key)
        if not folder.is_dir():
            continue
        exe = folder / names[0]
        if exe.is_file() and exe.stat().st_size > 5_000_000:
            return folder
    which = shutil.which("ffmpeg")
    if which:
        p = Path(which)
        if p.is_file():
            # Windows: bo Chocolatey shim. Mac/Linux: wrapper nho van chay duoc.
            if sys.platform != "win32" or p.stat().st_size > 5_000_000:
                return p.resolve().parent
    return None


def ffmpeg_path() -> str:
    folder = media_bin_dir()
    if folder:
        p = folder / _bin_name("ffmpeg")
        if p.is_file():
            return str(p)
    return "ffmpeg"


def ffprobe_path() -> str:
    folder = media_bin_dir()
    if folder:
        p = folder / _bin_name("ffprobe")
        if p.is_file():
            return str(p)
    found = shutil.which("ffprobe")
    return found or "ffprobe"


def ensure_media_bins() -> Path | None:
    """Dua ffmpeg vao PATH — bat buoc cho yt-dlp --download-sections."""
    folder = media_bin_dir()
    if folder is None:
        return None
    cur = os.environ.get("PATH", "")
    prefix = str(folder)
    if cur.split(os.pathsep)[:1] != [prefix]:
        os.environ["PATH"] = prefix + os.pathsep + cur
    return folder


def ytdlp_ffmpeg_args() -> list[str]:
    folder = ensure_media_bins()
    if not folder:
        return []
    return ["--ffmpeg-location", str(folder)]


def _resolve_media_cmd(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    name = Path(str(cmd[0])).name.lower()
    if name in {"ffmpeg", "ffmpeg.exe"}:
        return [ffmpeg_path(), *[str(c) for c in cmd[1:]]]
    if name in {"ffprobe", "ffprobe.exe"}:
        return [ffprobe_path(), *[str(c) for c in cmd[1:]]]
    return [str(c) for c in cmd]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Chay 1 command, raise neu loi, log gon."""
    cmd = _resolve_media_cmd(cmd)
    log.debug("RUN: %s", " ".join(str(c) for c in cmd))
    if sys.platform == "win32" and "creationflags" not in kw:
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        **kw,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        log.error("Command failed (%s):\n%s", cmd[0], err[-2000:])
        # Goi y file input (arg cuoi neu la path) de UI khong chi bao "ffprobe code 1".
        hint = ""
        for a in reversed(cmd[1:]):
            s = str(a)
            if s.startswith("-"):
                continue
            if any(s.lower().endswith(ext) for ext in (
                ".mp3", ".mp4", ".wav", ".m4a", ".aac", ".mov", ".mkv",
            )) or ("\\" in s or "/" in s):
                hint = f" | file={s}"
                break
        tail = ""
        if err:
            line = err.strip().splitlines()[-1].strip()
            if line:
                tail = f" | {line[:180]}"
        raise RuntimeError(
            f"{cmd[0]} that bai (code {proc.returncode}){hint}{tail}"
        )
    return proc


def win_hidden_kwargs(below_normal: bool = False) -> dict:
    """An cua so console ffmpeg/ffprobe tren Windows (tranh nhay man hinh)."""
    if sys.platform != "win32":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    if below_normal:
        flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
    return {"creationflags": flags}


# MP4 hop le toi thieu — duoi nguong nay coi nhu ffmpeg "thanh cong" ao / o dia day.
MIN_OUTPUT_MP4_BYTES = 10_000


def verify_output_mp4(
    path: Path | str,
    *,
    min_bytes: int = MIN_OUTPUT_MP4_BYTES,
    label: str = "output",
) -> Path:
    """Bat buoc file MP4 ton tai + size hop ly. Raise RuntimeError neu khong.

    Dung SAU ffmpeg/compose, TRUOC khi mark_completed / danh dau DONE.
    """
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(
            f"{label}: khong co file sau khi xuat — path={p}. "
            f"Kiem tra o dia / quyen ghi / duong dan."
        )
    try:
        size = int(p.stat().st_size)
    except OSError as e:
        raise RuntimeError(f"{label}: khong doc duoc file {p}: {e}") from e
    if size < int(min_bytes):
        raise RuntimeError(
            f"{label}: file qua nho ({size} byte, can >= {min_bytes}) — path={p}. "
            f"Co the o dia day hoac ffmpeg khong ghi duoc noi dung."
        )
    log.info("%s OK: %s (%d byte)", label, p, size)
    return p.resolve()


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


def probe_duration_retry(
    path: str | Path,
    *,
    fallback: float | None = None,
    retries: int = 4,
) -> float:
    """Probe co retry — Windows/AV hay lock file vua ghi xong.

    Delay 120–200ms * attempt (toi da ~5 lan). Neu van loi va co fallback
    (vd play_end-start da tinh) thi dung fallback, khong pha ca job.
    """
    path = Path(path)
    last_err: Exception | None = None
    n = max(1, int(retries) + 1)
    for attempt in range(n):
        try:
            if not path.exists() or path.stat().st_size < 50:
                raise RuntimeError(f"file thieu/rong: {path}")
            dur = probe_duration(path)
            if dur > 0.05:
                return dur
            raise RuntimeError(f"duration={dur}")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt + 1 < n:
                # 0.12, 0.24, 0.36... — AV scan thuong <200ms
                time.sleep(0.12 * (attempt + 1))
                continue
            break
    if fallback is not None and float(fallback) > 0.05:
        log.warning(
            "ffprobe loi tren %s (%s) — dung fallback %.2fs",
            path.name, last_err, float(fallback),
        )
        return float(fallback)
    raise RuntimeError(
        f"ffprobe that bai | file={path} | {last_err}"
    ) from last_err


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
    """Goi yt-dlp. Frozen exe dung --ytdlp (khong mo cua so app moi)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ytdlp"]
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "run_ytdlp.py"
    if wrapper.is_file():
        return [sys.executable, str(wrapper)]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    return [sys.executable, "-m", "yt_dlp"]


def check_tool(name: str) -> bool:
    if name == "yt-dlp":
        if getattr(sys, "frozen", False):
            return True
        wrapper = Path(__file__).resolve().parents[1] / "scripts" / "run_ytdlp.py"
        return wrapper.is_file() or shutil.which("yt-dlp") is not None
    if name in {"ffmpeg", "ffprobe"}:
        folder = media_bin_dir()
        if folder and (folder / _bin_name(name)).is_file():
            return True
        return shutil.which(name) is not None
    return shutil.which(name) is not None
