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


def _yaml_path(p: Path | str) -> str:
    """YAML quoted string khong chap nhan \\W (Windows). Dung dau /."""
    return Path(p).as_posix().replace("\\", "/")


def default_font() -> str:
    if sys.platform == "win32":
        p = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
        return _yaml_path(p)
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
    """ffmpeg.exe di kem .exe (cung thu muc hoac trong _internal)."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "_internal" / "ffmpeg.exe").is_file():
            return exe_dir / "_internal"
        return exe_dir
    return None


def _read_openai_key(path: Path | None) -> str:
    if not path or not path.is_file():
        return ""
    try:
        from dotenv import dotenv_values

        return (dotenv_values(path).get("OPENAI_API_KEY") or "").strip()
    except Exception:
        return ""


def _bundled_env_file() -> Path | None:
    """File .env di kem exe (CI ghi key vao day). Khong commit key vao git."""
    cands = [resource_root() / ".env"]
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / ".env")
    for p in cands:
        if _read_openai_key(p):
            return p
    for p in cands:
        if p.is_file():
            return p
    return None


def _seed_openai_key(user_env: Path, bundled: Path | None) -> None:
    """Lan dau (hoac AppData thieu key): chep OPENAI_API_KEY tu ban dong goi."""
    if _read_openai_key(user_env):
        return
    key = _read_openai_key(bundled)
    if not key:
        return
    if user_env.is_file():
        lines = user_env.read_text(encoding="utf-8").splitlines(True)
        out: list[str] = []
        replaced = False
        for line in lines:
            if line.startswith("OPENAI_API_KEY="):
                out.append(f"OPENAI_API_KEY={key}\n")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(f"OPENAI_API_KEY={key}\n")
        user_env.write_text("".join(out), encoding="utf-8")
        return
    user_env.write_text(f"OPENAI_API_KEY={key}\n", encoding="utf-8")


def prepare() -> Path:
    """Tao thu muc data, copy config, chdir. Goi TRUOC khi import web app."""
    os.environ["DYFLOW_DESKTOP"] = "1"
    # Windows proxy he thong lam urlopen(127.0.0.1) timeout — exe khong mo duoc.
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    # Luon 1 worker local. Khong lay DYFLOW_WEB_WORKERS=64 / NO_WORKER tu .env SaaS.
    root = data_dir()
    work = root / "work"
    out = root / "output"
    work.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)

    bins = extra_bin_dir()
    if bins and bins.is_dir():
        os.environ["PATH"] = str(bins) + os.pathsep + os.environ.get("PATH", "")
    try:
        from .utils import ensure_media_bins

        ensure_media_bins()
    except Exception:
        pass

    cfg_path = root / "config.yaml"
    bundled = resource_root() / "config.yaml"
    if not cfg_path.exists() and bundled.exists():
        shutil.copy2(bundled, cfg_path)
    bundled_ck = resource_root() / "cookies_douyin.txt"
    local_ck = root / "cookies_douyin.txt"
    # Cookie Mac khong dung duoc tren Win — lay tu Chrome/Edge local.
    if sys.platform != "win32" and bundled_ck.exists() and not local_ck.exists():
        shutil.copy2(bundled_ck, local_ck)
    _rewrite_paths(cfg_path, work, out, cookies_file=local_ck)
    os.environ["DYFLOW_CONFIG"] = str(cfg_path)

    env_path = root / ".env"
    example = resource_root() / ".env.example"
    bundled_env = _bundled_env_file()
    if not env_path.exists():
        # Chi copy bundled co key that — tranh .env.example (OPENAI_API_KEY= rong).
        if bundled_env and _read_openai_key(bundled_env):
            shutil.copy2(bundled_env, env_path)
        elif bundled_env and bundled_env.exists():
            shutil.copy2(bundled_env, env_path)
        elif example.exists():
            shutil.copy2(example, env_path)
    _seed_openai_key(env_path, bundled_env)
    # Neu AppData dang de key rong ma exe co key — seed lai ngay.
    if not _read_openai_key(env_path) and bundled_env:
        _seed_openai_key(env_path, bundled_env)
    try:
        from dotenv import load_dotenv

        # Bundled key (canh .exe / _internal) truoc AppData. Chuoi rong khong che key that.
        if bundled_env and bundled_env.exists():
            load_dotenv(bundled_env, override=False)
        load_dotenv(env_path, override=False)
        from .preflight import hydrate_openai_key

        hydrate_openai_key()
    except Exception:
        pass
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        print(
            "LOI: Khong tim thay OPENAI_API_KEY trong .env (source hoac VideosCreator). "
            "Job se FAIL o pre-flight. Dien key that, khong de trong nhu .env.example.",
            flush=True,
        )

    # Desktop: mac dinh 2 worker song song tren may >= 4 core de xu ly hang cho nhanh
    w_default = "2" if (os.cpu_count() or 4) >= 4 else "1"
    env_w = os.getenv("DYFLOW_WEB_WORKERS", "").strip()
    if not (env_w.isdigit() and 1 <= int(env_w) <= 4):
        os.environ["DYFLOW_WEB_WORKERS"] = w_default
    if not os.getenv("DYFLOW_MAX_CONCURRENT_PER_USER"):
        os.environ["DYFLOW_MAX_CONCURRENT_PER_USER"] = os.environ["DYFLOW_WEB_WORKERS"]
    os.environ.pop("DYFLOW_WEB_NO_WORKER", None)
    # Desktop Win: ffmpeg Transform encode rat cham (ARM VM 1h+) — tat de job di tiep.
    if sys.platform == "win32":
        os.environ["DYFLOW_SKIP_TRANSFORM"] = "1"

    os.chdir(root)
    return root


def _rewrite_paths(
    cfg_path: Path,
    work: Path,
    out: Path,
    cookies_file: Path | None = None,
) -> None:
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    font = default_font()
    skip_tf = sys.platform == "win32"
    section = ""
    lines = []
    for line in text.splitlines(True) if text else []:
        if line[:1] not in (" ", "\t", "#", "\n", "\r") and ":" in line:
            section = line.split(":", 1)[0].strip()
            if skip_tf and section == "transform":
                lines.append(line)
                lines.append("  enabled: false\n")
                continue
        if skip_tf and section == "transform" and line.startswith("  enabled:"):
            continue
        if line.startswith("  work_dir:"):
            line = f'  work_dir: "{_yaml_path(work)}"\n'
        elif line.startswith("  output_dir:"):
            line = f'  output_dir: "{_yaml_path(out)}"\n'
        elif line.startswith("  font_path:") and font:
            line = f'  font_path: "{_yaml_path(font)}"\n'
        elif line.startswith("  cookies_file:") and cookies_file is not None:
            line = f'  cookies_file: "{_yaml_path(cookies_file)}"\n'
        elif line.startswith("  cookies_from_browser:"):
            line = '  cookies_from_browser: ""\n'
        elif line.startswith("  auto_refresh_cookies:"):
            line = "  auto_refresh_cookies: false\n"
        elif line.startswith("  chrome_headless:"):
            line = "  chrome_headless: true\n"
        elif line.startswith("  ytdlp_first:"):
            line = "  ytdlp_first: false\n"
        elif line.startswith("  chrome_fallback:"):
            line = "  chrome_fallback: false\n"
        elif line.startswith("  backend:"):
            line = '  backend: "yt-dlp"\n'
        elif line.startswith("  dub_qa_fail_closed:"):
            line = "  dub_qa_fail_closed: false\n"
        elif line.startswith("  workers:"):
            line = "  workers: 1\n"
        elif sys.platform == "win32" and line.lstrip().startswith("whisper_model:"):
            # Tra lai medium — ban va truoc ha xuong small
            line = "    whisper_model: medium\n"
        elif sys.platform == "win32" and line.lstrip().startswith("retry_on_loop:"):
            line = "    retry_on_loop: true\n"
        elif sys.platform == "win32" and line.lstrip().startswith("strip_original_vocals:"):
            line = "  strip_original_vocals: true\n"
        elif line.startswith("  retention_days:"):
            line = "  retention_days: 7\n"
        lines.append(line)
    if lines:
        cfg_path.write_text("".join(lines), encoding="utf-8")
