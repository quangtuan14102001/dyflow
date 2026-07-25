"""Buoc 1: Tai video tu Douyin — uu tien on dinh cho san pham.

Thu tu:
1) Resolve id + dung cache / ban raw cu co audio
2) yt-dlp (neu backend cho phep)
3) Chrome sniff video+audio (bat buoc co audio truoc khi tai nang)
4) yt-dlp full fallback (cookie Chrome)
5) Cache ket qua thanh cong
"""
from __future__ import annotations
import re
import shutil
import subprocess
from pathlib import Path

from . import cookies as cookie_util
from . import dl_cache
from .utils import log, run, check_tool, probe_has_audio


def extract_url(text: str) -> str:
    """Loc link tu cuc text chia se cua Douyin (app copy ra ca chu Trung)."""
    m = re.search(r"https?://[^\s]+", text.strip())
    return m.group(0) if m else text.strip()


def _build_ytdlp_cmd(
    url: str,
    out_tmpl: str,
    cfg: dict,
    *,
    cookies_file: str | None = None,
    cookies_from_browser: str | None = None,
) -> list[str]:
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--retries", str(cfg.get("retries", 3)),
        "--no-warnings",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", out_tmpl,
    ]
    max_dur = float(cfg.get("max_duration_sec") or 0)
    if max_dur > 0:
        # chi tai doan dau — tranh keo clip 40+ phut
        end = max(5.0, max_dur + 5.0)
        cmd += ["--download-sections", f"*{0}-{end}"]
        cmd += ["--force-keyframes-at-cuts"]
    if cfg.get("fetch_subs", True):
        cmd += [
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", "zh-Hans,zh,zh-CN,zh-TW",
            "--convert-subs", "srt",
        ]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    elif cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    if cfg.get("impersonate"):
        cmd += ["--impersonate", str(cfg["impersonate"])]
    if cfg.get("rate_limit"):
        cmd += ["--limit-rate", cfg["rate_limit"]]
    cmd.append(url)
    return cmd


def _run_ytdlp(cmd: list[str]) -> subprocess.CompletedProcess:
    log.debug("RUN: %s", " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True)


def _latest_mp4(out_dir: Path) -> Path | None:
    files = sorted(
        [
            p for p in out_dir.glob("*.mp4")
            if ".video." not in p.name and not p.name.endswith(".ocrblur.mp4")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files:
        if probe_has_audio(p):
            return p
    return files[0] if files else None


def _accept(path: Path | None, video_id: str | None, source_url: str = "") -> Path | None:
    if not path or not path.exists() or path.stat().st_size < 10_000:
        return None
    if not probe_has_audio(path):
        log.warning("File tai ve khong co audio, bo: %s", path.name)
        return None
    dl_cache.save(video_id, path)
    if source_url:
        dl_cache.remember_short(source_url, video_id)
    return path


def download_one(url: str, out_dir: str | Path, cfg: dict) -> Path:
    """Tai 1 video Douyin -> tra ve duong dan file mp4 (bat buoc co audio)."""
    url = extract_url(url)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    video_id = dl_cache.video_id_from_text(url) or dl_cache.resolve_from_short(url)

    # 1) Cache / ban cu
    reused = dl_cache.try_reuse(video_id, out_dir)
    if reused:
        return reused
    local = dl_cache.find_local_good_copy(video_id)
    if local:
        dest = out_dir / f"{video_id}.mp4"
        shutil.copy2(local, dest)
        log.info("Tai dung lai ban raw cu: %s -> %s", local, dest.name)
        dl_cache.save(video_id, dest)
        dl_cache.remember_short(url, video_id)
        return dest

    backend = str(cfg.get("backend", "yt-dlp")).lower()
    last_err = ""

    def _try_ytdlp(label: str) -> Path | None:
        nonlocal last_err
        if not check_tool("yt-dlp"):
            return None
        out_tmpl = str(out_dir / "%(id)s.%(ext)s")
        cookies_path: str | None = None
        from_browser = (cfg.get("cookies_from_browser") or "").strip() or None
        chrome_ck = out_dir / "_chrome_cookies.txt"
        if chrome_ck.exists():
            cookies_path = str(chrome_ck)
        elif cfg.get("auto_refresh_cookies", True) or cfg.get("cookies_file") or from_browser:
            try:
                cookies_path = cookie_util.ensure_cookies(cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("ensure_cookies: %s", e)
                cookies_path = (cfg.get("cookies_file") or "").strip() or None

        cmd = _build_ytdlp_cmd(
            url, out_tmpl, cfg,
            cookies_file=cookies_path,
            cookies_from_browser=None if cookies_path else from_browser,
        )
        proc = _run_ytdlp(cmd)
        if proc.returncode != 0 and cookie_util.is_cookie_error(proc.stderr or ""):
            log.warning("yt-dlp bao can cookie -> force refresh...")
            if cfg.get("auto_refresh_cookies", True):
                try:
                    cookies_path = str(cookie_util.force_refresh(cfg))
                    proc = _run_ytdlp(
                        _build_ytdlp_cmd(url, out_tmpl, cfg, cookies_file=cookies_path)
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("Force refresh cookie that bai: %s", e)
        if proc.returncode == 0:
            got = _latest_mp4(out_dir)
            vid = dl_cache.video_id_from_text(got.name if got else "", url) or video_id
            ok = _accept(got, vid, url)
            if ok:
                log.info("Tai xong (%s): %s", label, ok.name)
                return ok
        last_err = (proc.stderr or "")[-800:]
        return None

    # 2) yt-dlp soft/first — ke ca backend=chrome (cookie tot thi xong nhanh)
    if cfg.get("ytdlp_first", True) or backend not in {"chrome", "playwright"}:
        got = _try_ytdlp("yt-dlp")
        if got:
            return got
        if backend not in {"chrome", "playwright"}:
            log.warning("yt-dlp fail -> fallback Chrome.\n%s", last_err)
        else:
            log.info("yt-dlp soft-fail -> Chrome sniff")

    # 3) Chrome sniff + authenticated CDN fetch
    if backend in {"chrome", "playwright"} or cfg.get("chrome_fallback", True):
        from .chrome_dl import download_with_chrome

        try:
            got = download_with_chrome(url, out_dir, cfg)
            vid = dl_cache.video_id_from_text(got.name, url) or video_id
            m = re.search(r"(\d{10,})", got.name)
            if m:
                vid = m.group(1)
            ok = _accept(got, vid, url)
            if ok:
                return ok
            last_err = "Chrome tra file khong co audio"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            log.warning("Chrome download that bai: %s", e)

        # 4) yt-dlp full lan cuoi (uu tien cookie vua export tu Chrome)
        got = _try_ytdlp("yt-dlp fallback")
        if got:
            return got

    raise RuntimeError(
        "Khong tai duoc video Douyin co audio sau moi lop fallback. "
        f"Chi tiet: {last_err or 'unknown'}"
    )


def download_profile_f2(profile_url: str, out_dir: str | Path) -> list[Path]:
    """Keo toan bo video cua 1 user bang f2."""
    if not check_tool("f2"):
        raise RuntimeError(
            "Chua cai f2. Chay: pip install f2  (xem README de cau hinh cookie)"
        )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["f2", "dy", "-c", "f2_config.yaml", "-u", profile_url, "-p", str(out_dir)])
    return sorted(out_dir.glob("**/*.mp4"))
