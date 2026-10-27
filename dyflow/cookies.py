"""Cookie Douyin cho yt-dlp — chi dung file cookies.txt (khong Playwright).

Uu tien:
1) cookies_file neu con dung
2) (tuy chon) --cookies-from-browser khi cookies_from_browser duoc bat
Khong mo Chromium/Playwright de lay cookie.
"""
from __future__ import annotations
import subprocess
import sys
import time
from pathlib import Path

from .utils import log, check_tool, ytdlp_argv

# Cookie thuong con song 1-7 ngay; refresh som de tranh fail giua job
_COOKIE_MAX_AGE_SEC = 12 * 3600  # 12 gio


def cookies_stale(path: str | Path, max_age_sec: float = _COOKIE_MAX_AGE_SEC) -> bool:
    p = Path(path)
    if not p.exists() or p.stat().st_size < 50:
        return True
    age = time.time() - p.stat().st_mtime
    return age > max_age_sec


def is_cookie_error(stderr: str) -> bool:
    s = (stderr or "").lower()
    needles = (
        "cookies are required",
        "cookie file",
        "login required",
        "sign in to confirm",
        "http error 403",
        "confirmed your age",
    )
    return any(n in s for n in needles)


def write_netscape_cookies(cookies: list[dict], out_path: Path) -> Path:
    """Ghi cookie Playwright/browser dict -> Netscape cookies.txt (yt-dlp)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File", "# https://curl.haxx.se/rfc/cookie_spec.html", ""]
    for c in cookies:
        domain = str(c.get("domain") or "")
        if not domain:
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = str(c.get("path") or "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = str(int(c.get("expires") or 0))
        name = str(c.get("name") or "")
        value = str(c.get("value") or "")
        if not name:
            continue
        lines.append("\t".join([domain, flag, path, secure, expires, name, value]))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def _browser_candidates(from_browser: str) -> list[str]:
    raw = (from_browser or "").strip().lower()
    if raw in ("", "none", "off", "false", "0"):
        return []
    if raw == "auto":
        if sys.platform == "darwin":
            return ["chrome", "edge", "chromium", "brave", "firefox", "safari"]
        return ["chrome", "edge", "chromium", "brave", "firefox"]
    return [raw]


def refresh_from_browser(browser: str, out_path: Path) -> Path:
    """yt-dlp --cookies-from-browser -> cookies.txt (can browser da dang nhap Douyin)."""
    check_tool("yt-dlp")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        *ytdlp_argv(),
        "--cookies-from-browser", browser,
        "--cookies", str(out_path),
        "--skip-download",
        "https://www.douyin.com/",
    ]
    log.info("Lay cookie tu %s -> %s", browser, out_path)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 50:
        err = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"cookies-from-browser ({browser}) that bai: {err}")
    return out_path


def refresh_with_playwright(*_a, **_kw) -> Path:
    """Da tat — khong dung Playwright lay cookie."""
    raise RuntimeError(
        "Playwright cookie refresh da tat. "
        "Dat cookies_douyin.txt thu cong (export tu Chrome) "
        "hoac bat cookies_from_browser trong config."
    )


def ensure_cookies(cfg: dict) -> str | None:
    """Dam bao co cookies_file. Tra ve path hoac None.

    Khong Playwright. Mac dinh chi dung file co san.
    """
    cookies_file = (cfg.get("cookies_file") or "").strip()
    from_browser = (cfg.get("cookies_from_browser") or "").strip()
    auto = bool(cfg.get("auto_refresh_cookies", False))
    method = str(cfg.get("cookie_refresh_method") or "none").lower()
    max_age = float(cfg.get("cookie_max_age_hours", 12)) * 3600

    # Khach: chi dung file co san; khong trom Edge/Chrome, khong Playwright.
    if not from_browser:
        if not cookies_file:
            return None
        path = Path(cookies_file)
        if path.is_file() and path.stat().st_size > 50:
            return str(path)
        return None

    if not cookies_file:
        cookies_file = "./cookies_douyin.txt"

    path = Path(cookies_file)
    need_refresh = auto and cookies_stale(path, max_age_sec=max_age)

    if not need_refresh and path.is_file() and path.stat().st_size > 50:
        return str(path)

    if method in ("none", "file", "off", ""):
        if path.is_file() and path.stat().st_size > 50:
            return str(path)
        return None

    log.warning("Cookie het han / chua co -> thu cookies-from-browser...")
    errors: list[str] = []

    if method in ("browser", "auto"):
        for browser in _browser_candidates(from_browser):
            try:
                refresh_from_browser(browser, path)
                return str(path)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{browser}:{e}")
                log.warning("Lay cookie tu %s that bai: %s", browser, e)

    if method in ("playwright",):
        log.warning("cookie_refresh_method=playwright da bi tat — bo qua")

    if path.exists() and path.stat().st_size > 50:
        log.warning("dung cookie cu (refresh fail)")
        return str(path)

    if errors:
        raise RuntimeError(
            "Khong tu dong lay duoc cookie Douyin.\n"
            + "\n".join(errors)
            + "\n\nCach thu cong: mo douyin.com tren Chrome, export cookies.txt, "
            "dat cookies_file trong config.yaml"
        )
    return str(path) if path.exists() else None


def force_refresh(cfg: dict) -> Path:
    """Bat buoc lay cookie moi (khi yt-dlp bao cookie can thiet). Khong Playwright."""
    cookies_file = Path((cfg.get("cookies_file") or "./cookies_douyin.txt").strip())
    from_browser = (cfg.get("cookies_from_browser") or "").strip()
    method = str(cfg.get("cookie_refresh_method") or "browser").lower()

    if cookies_file.exists():
        cookies_file.unlink(missing_ok=True)

    if method in ("browser", "auto") and from_browser:
        last_err: Exception | None = None
        for browser in _browser_candidates(from_browser):
            try:
                return refresh_from_browser(browser, cookies_file)
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("force refresh %s fail: %s", browser, e)
        if last_err:
            raise RuntimeError(
                f"Khong lay duoc cookie (Playwright da tat): {last_err}"
            ) from last_err

    raise RuntimeError(
        "Khong the force-refresh cookie: Playwright da tat, "
        "va cookies_from_browser chua bat. "
        "Export cookies_douyin.txt thu cong."
    )
