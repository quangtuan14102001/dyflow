"""Buoc 1: Tai video tu Douyin — uu tien khong cookie.

Thu tu:
1) Resolve id + dung cache / ban raw cu co audio
2) f2 guest (ttwid/msToken) + CDN HTTP — khong login Douyin / Chrome
3) iesdouyin share/iteminfo guest (fallback may moi khong cookie)
4) yt-dlp CHI khi co cookies_file that / chrome_fallback
5) Chrome sniff chi khi chrome_fallback=true (mac dinh tat)
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import cookies as cookie_util
from .dl_errors import DyDownloadError, classify_download_error, empty_channel_error
from . import dl_cache
from .utils import log, run, check_tool, probe_has_audio, probe_duration, ytdlp_argv, ytdlp_ffmpeg_args

# Cache do dai remote (tranh probe lai khi user goi quote nhieu lan)
_DURATION_CACHE: dict[str, tuple[float, float]] = {}  # key -> (expires, seconds)
_DURATION_CACHE_TTL = 1800.0
_CHANNEL_LIST_GEN = 0
_CHANNEL_LIST_LOCK = threading.Lock()


def extract_url(text: str) -> str:
    """Loc link tu cuc text chia se cua Douyin (app copy ra ca chu Trung)."""
    m = re.search(r"https?://[^\s]+", text.strip())
    return m.group(0) if m else text.strip()


def _duration_cache_get(key: str) -> float | None:
    hit = _DURATION_CACHE.get(key)
    if not hit:
        return None
    exp, sec = hit
    if time.time() > exp:
        _DURATION_CACHE.pop(key, None)
        return None
    return sec


def _duration_cache_set(key: str, seconds: float) -> None:
    _DURATION_CACHE[key] = (time.time() + _DURATION_CACHE_TTL, float(seconds))


def probe_remote_duration(url: str, cfg: dict | None = None) -> float | None:
    """Lay duration (giay) bang yt-dlp --dump-json, khong tai file.

    Tra ve None neu khong doc duoc (cookie/API fail).
    """
    url = extract_url(url)
    cfg = cfg or {}
    cache_key = dl_cache.video_id_from_text(url) or url
    cached = _duration_cache_get(cache_key)
    if cached is not None:
        return cached

    # Neu da co file local/cache -> probe nhanh
    vid = dl_cache.video_id_from_text(url) or dl_cache.resolve_from_short(url)
    if vid:
        local = dl_cache.find_local_good_copy(vid)
        if local:
            try:
                dur = probe_duration(local)
                if dur > 0:
                    _duration_cache_set(cache_key, dur)
                    return dur
            except Exception:  # noqa: BLE001
                pass

    if not check_tool("yt-dlp"):
        return None

    cookies_path: str | None = None
    from_browser = (cfg.get("cookies_from_browser") or "").strip() or None
    try:
        cookies_path, from_browser = _ytdlp_cookies(cfg)
    except Exception as e:  # noqa: BLE001
        log.debug("probe cookies: %s", e)

    cmd = [
        *ytdlp_argv(),
        "--skip-download",
        "--no-playlist",
        "--no-warnings",
        "-J",
        "--socket-timeout", "20",
    ]
    if cookies_path:
        cmd += ["--cookies", cookies_path]
    elif from_browser:
        cmd += ["--cookies-from-browser", from_browser]
    if cfg.get("impersonate"):
        cmd += ["--impersonate", str(cfg["impersonate"])]
    cmd.append(url)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=45,
        )
    except subprocess.TimeoutExpired:
        log.warning("probe_remote_duration timeout: %s", url[:80])
        return None
    if proc.returncode != 0:
        log.debug("probe_remote_duration fail: %s", (proc.stderr or "")[-300:])
        return None
    try:
        info = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    dur = info.get("duration")
    if dur is None and isinstance(info.get("entries"), list) and info["entries"]:
        dur = info["entries"][0].get("duration")
    try:
        seconds = float(dur)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    _duration_cache_set(cache_key, seconds)
    if vid or info.get("id"):
        _duration_cache_set(str(info.get("id") or vid), seconds)
    return seconds


def _ytdlp_cookies(cfg: dict) -> tuple[str | None, str | None]:
    """Cookie tuy chon. Khong tu mo Chrome/Edge de lay cookie."""
    cookies_path = (cfg.get("cookies_file") or "").strip() or None
    from_browser = (cfg.get("cookies_from_browser") or "").strip() or None
    if cookies_path:
        p = Path(cookies_path)
        if p.is_file() and p.stat().st_size > 50:
            return str(p), None
        cookies_path = None
    if from_browser:
        try:
            got = cookie_util.ensure_cookies(cfg)
            if got:
                return got, None
        except Exception as e:  # noqa: BLE001
            log.debug("ytdlp cookies: %s", e)
        return None, from_browser
    return None, None


def public_watch_url(url: str, *, prefer_user: bool = False) -> str:
    """Link share iesdouyin — xem/tai khach, khong can dang nhap.

    prefer_user=True: link kenh kem ?vid= van ra trang user (liet ke full kenh).
    """
    raw = extract_url(url)
    resolved = resolve_share_url(raw)
    from .chrome_dl import sec_uid_from_user_url

    sec = sec_uid_from_user_url(resolved) or sec_uid_from_user_url(raw)
    if prefer_user and sec:
        return f"https://www.iesdouyin.com/share/user/{sec}"
    for text in (resolved, raw):
        m = _AWEME_ID_RE.search(text or "")
        if m:
            return f"https://www.iesdouyin.com/share/video/{m.group(1)}"
    if sec:
        return f"https://www.iesdouyin.com/share/user/{sec}"
    return resolved or raw


_AWEME_ID_RE = re.compile(
    r"(?:douyin\.com/(?:share/)?video/|[?&]vid=|aweme_id[\"'=\s:]+|modal_id=)(\d{10,})",
    re.I,
)
_SHARE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def resolve_share_url(url: str, timeout: float = 12) -> str:
    """Theo redirect short link v.douyin.com -> user/video that."""
    url = extract_url(url)
    if "v.douyin.com" not in (url or "").lower() and "iesdouyin.com" not in (url or "").lower():
        return url
    try:
        import urllib.request

        req = urllib.request.Request(
            url,
            headers={"User-Agent": _SHARE_UA, "Accept": "text/html,*/*"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.geturl() or url
            return final.split("#")[0]
    except Exception as e:  # noqa: BLE001
        log.debug("resolve_share_url %s: %s", url[:80], e)
        return url


def _aweme_id_from_url(url: str) -> int:
    m = re.search(r"/video/(\d{10,})", url or "")
    try:
        return int(m.group(1)) if m else 0
    except ValueError:
        return 0


def _newest_urls(rows: list[tuple[int, str]], *, limit: int) -> list[str]:
    """rows = (sort_key, url); video moi hon co key lon hon (timestamp / aweme id)."""
    rows.sort(key=lambda x: x[0], reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for _key, href in rows:
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(href)
        if len(out) >= limit:
            break
    return out


def _video_urls_from_text(blob: str, *, limit: int) -> list[str]:
    rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    for m in _AWEME_ID_RE.finditer(blob or ""):
        vid = m.group(1)
        href = f"https://www.douyin.com/video/{vid}"
        if href in seen:
            continue
        seen.add(href)
        rows.append((int(vid), href))
    return _newest_urls(rows, limit=limit)


def _entry_to_video_url(entry: dict) -> str | None:
    vid = str(entry.get("id") or "").strip()
    webpage = str(entry.get("webpage_url") or entry.get("url") or "").strip()
    if re.fullmatch(r"\d{10,}", vid):
        return f"https://www.douyin.com/video/{vid}"
    found = _video_urls_from_text(webpage, limit=1)
    if found:
        return found[0]
    if re.fullmatch(r"\d{10,}", webpage):
        return f"https://www.douyin.com/video/{webpage}"
    return None


def _ytdlp_flat_channel(url: str, cfg: dict, limit: int) -> list[str]:
    cookies_path, from_browser = _ytdlp_cookies(cfg)
    cmd = [
        *ytdlp_argv(),
        "--yes-playlist",
        "--flat-playlist",
        "--skip-download",
        "--no-warnings",
        "-j",
        "--socket-timeout", "30",
        "--playlist-end", str(limit),
    ]
    if cookies_path:
        cmd += ["--cookies", cookies_path]
    elif from_browser:
        cmd += ["--cookies-from-browser", from_browser]
    if cfg.get("impersonate"):
        cmd += ["--impersonate", str(cfg["impersonate"])]
    cmd.append(url)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=40,
            **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if sys.platform == "win32" else {}),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Het thoi gian khi lay danh sach kenh Douyin.") from e
    rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(info, dict):
            continue
        href = _entry_to_video_url(info)
        if not href or href in seen:
            continue
        seen.add(href)
        ts = 0
        for key in ("timestamp", "release_timestamp"):
            try:
                ts = int(info.get(key) or 0)
            except (TypeError, ValueError):
                ts = 0
            if ts:
                break
        rows.append((ts or _aweme_id_from_url(href), href))
    out = _newest_urls(rows, limit=limit)
    if out:
        return out
    err = (proc.stderr or "")[-400:]
    if proc.returncode != 0:
        raise RuntimeError(err or "yt-dlp fail")
    return []


def _http_fetch(opener, url: str, *, accept: str = "text/html,application/xhtml+xml") -> str:
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _SHARE_UA,
            "Accept": accept,
            "Referer": "https://www.douyin.com/",
        },
    )
    with opener.open(req, timeout=12) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _ids_from_channel_html(html: str, sec_uid: str) -> list[str]:
    import urllib.parse

    from .chrome_dl import aweme_ids_from_user_post

    ids: list[str] = []
    seen: set[str] = set()

    def _take(chunk: list[str]) -> None:
        for vid in chunk or []:
            if vid in seen:
                continue
            seen.add(vid)
            ids.append(vid)

    for pat in (
        r'id="RENDER_DATA"[^>]*>([^<]+)',
        r'id="SIGI_STATE"[^>]*>([^<]+)',
        r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([^<]+)',
    ):
        m = re.search(pat, html or "")
        if not m:
            continue
        raw = urllib.parse.unquote(m.group(1))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        _take(aweme_ids_from_user_post(data, sec_uid, require_owner=bool(sec_uid)))

        def _walk(obj):
            if isinstance(obj, dict):
                if "aweme_list" in obj or "awemeList" in obj:
                    _take(aweme_ids_from_user_post(obj, sec_uid, require_owner=bool(sec_uid)))
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    _walk(v)

        _walk(data)

    if not ids and sec_uid:
        # Chi nhat aweme gan sec_uid trong HTML (tranh video goi y)
        for m in re.finditer(
            rf"{re.escape(sec_uid)}.{{0,400}}?aweme_id[\"'=\s:]+(\d{{15,}})"
            rf"|aweme_id[\"'=\s:]+(\d{{15,}}).{{0,400}}?{re.escape(sec_uid)}",
            html or "",
            re.I | re.S,
        ):
            vid = m.group(1) or m.group(2)
            if vid and vid not in seen:
                seen.add(vid)
                ids.append(vid)
    return ids


def _http_user_aweme_count(sec_uid: str) -> int:
    """So 作品 public (khong can cookie). Dung de biet list 36 clip la thieu."""
    if not sec_uid:
        return 0
    import urllib.parse
    import urllib.request

    href = (
        "https://www.iesdouyin.com/web/api/v2/user/info/?"
        + urllib.parse.urlencode({"sec_uid": sec_uid})
    )
    try:
        req = urllib.request.Request(
            href,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://www.iesdouyin.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
    except Exception as e:  # noqa: BLE001
        log.debug("user/info count: %s", e)
        return 0
    if not isinstance(data, dict):
        return 0
    ui = data.get("user_info") if isinstance(data.get("user_info"), dict) else {}
    raw = ui.get("aweme_count") or ui.get("awemeCount") or 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _http_ies_aweme_post(sec_uid: str, limit: int) -> list[str]:
    """API public iesdouyin — khong cookie, khong mo Chrome/Edge."""
    if not sec_uid:
        return []
    import urllib.parse
    import urllib.request

    ids: list[str] = []
    seen: set[str] = set()
    cursor = 0
    ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    )
    for _ in range(30):
        if len(ids) >= limit:
            break
        qs = urllib.parse.urlencode({
            "sec_uid": sec_uid,
            "count": "21",
            "max_cursor": str(cursor),
            "aid": "1128",
        })
        href = f"https://www.iesdouyin.com/web/api/v2/aweme/post/?{qs}"
        try:
            req = urllib.request.Request(
                href,
                headers={
                    "User-Agent": ua,
                    "Accept": "application/json,text/plain,*/*",
                    "Referer": "https://www.iesdouyin.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
        except Exception as e:  # noqa: BLE001
            log.debug("ies aweme/post: %s", e)
            break
        if not isinstance(data, dict):
            break
        chunk: list[str] = []
        for item in data.get("aweme_list") or []:
            if not isinstance(item, dict):
                continue
            aid = str(item.get("aweme_id") or item.get("awemeId") or "").strip()
            if not aid.isdigit() or aid in seen:
                continue
            seen.add(aid)
            chunk.append(aid)
        if not chunk:
            break
        ids.extend(chunk)
        try:
            nxt = int(data.get("max_cursor") or data.get("maxCursor") or 0)
        except (TypeError, ValueError):
            nxt = 0
        if nxt <= cursor:
            break
        cursor = nxt
        more = data.get("has_more")
        # Douyin hay gan has_more=0 sau ~2 trang. Chi dung khi trang thuc su ngan.
        if more in (0, False, "0", "false") and len(chunk) < 10:
            break
    if ids:
        log.info("Kenh ies API liet ke %d video", len(ids))
    return ids


def _run_asyncio(coro, *, timeout: float = 90):
    """Chay coroutine tu thread sync (FastAPI co the dang co event loop)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Van boc timeout — tranh TokenManager/CDN treo vo han tren may user.
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=timeout)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=timeout)


def _call_timeout(fn, *, timeout: float, label: str):
    """Chay ham sync co timeout (TokenManager hay treo tren mang cham/chan)."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except FutTimeout as e:
            raise RuntimeError(f"{label} timeout {timeout:.0f}s") from e


def _f2_guest_kwargs(path: str | None = None) -> dict:
    from f2.apps.douyin.utils import TokenManager

    # Tat Bark push cua f2 (mac dinh hay bat → spam loi api.day.app).
    try:
        from f2.apps.bark.utils import ClientConfManager as _BarkCM

        _BarkCM.enable_bark = classmethod(lambda cls: False)  # type: ignore[method-assign]
    except Exception:
        pass

    # Timeout rieng — neu thieu, UI dung "Dang tai video..." mai.
    ttwid = _call_timeout(TokenManager.gen_ttwid, timeout=20, label="ttwid")
    ms = _call_timeout(TokenManager.gen_real_msToken, timeout=25, label="msToken")
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    out: dict = {
        "headers": {
            "User-Agent": ua,
            "Referer": "https://www.douyin.com/",
        },
        "proxies": {"http://": None, "https://": None},
        "cookie": f"ttwid={ttwid}; msToken={ms}",
    }
    if path:
        out["path"] = path
    return out


def _http_get_bytes(url: str, *, timeout: float = 120, referer: str = "https://www.douyin.com/") -> bytes:
    """Tai CDN bang curl_cffi (guest) — khong can cookie dang nhap."""
    from curl_cffi import requests as creq

    r = creq.get(
        url,
        impersonate="chrome131",
        timeout=timeout,
        headers={
            "Referer": referer,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
    )
    if r.status_code != 200 or not r.content or len(r.content) < 10_000:
        raise RuntimeError(f"CDN HTTP {r.status_code} size={len(r.content or b'')}")
    return r.content


_CDN_HINTS = ("douyinvod", "bytevcloud", "zjcdn", "snssdk", "tos-cn", "vlabvod", ".mp4")


def _play_urls_from_obj(obj) -> list[str]:
    """Nhat URL CDN tu JSON RENDER_DATA / iteminfo."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(u: str) -> None:
        u = str(u or "").strip()
        if not u.startswith("http") or u in seen:
            return
        low = u.lower()
        if not any(h in low for h in _CDN_HINTS):
            return
        seen.add(u)
        found.append(u)

    def _walk(node) -> None:
        if isinstance(node, dict):
            for key in ("url_list", "urlList", "play_addr", "playAddr", "download_addr"):
                val = node.get(key)
                if isinstance(val, list):
                    for u in val:
                        _add(u if isinstance(u, str) else str(u.get("url") or "") if isinstance(u, dict) else "")
                elif isinstance(val, dict):
                    for u in val.get("url_list") or val.get("urlList") or []:
                        _add(str(u))
                elif isinstance(val, str):
                    _add(val)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
        elif isinstance(node, str) and node.startswith("http"):
            _add(node)

    _walk(obj)
    return found


def _ies_share_play_urls(aweme_id: str) -> list[str]:
    """Lay play URL cong khai tu iesdouyin (khong cookie login)."""
    from curl_cffi import requests as creq
    from urllib.parse import unquote

    aweme_id = str(aweme_id or "").strip()
    if not aweme_id.isdigit():
        return []
    ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    )
    headers = {"User-Agent": ua, "Referer": "https://www.iesdouyin.com/"}
    urls: list[str] = []

    # 1) iteminfo API
    try:
        r = creq.get(
            f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={aweme_id}",
            impersonate="chrome131",
            timeout=25,
            headers=headers,
        )
        if r.status_code == 200 and r.content:
            data = r.json()
            urls.extend(_play_urls_from_obj(data))
    except Exception as e:  # noqa: BLE001
        log.debug("ies iteminfo: %s", e)

    # 2) share HTML RENDER_DATA
    if not urls:
        try:
            r = creq.get(
                f"https://www.iesdouyin.com/share/video/{aweme_id}",
                impersonate="chrome131",
                timeout=25,
                headers=headers,
            )
            html = r.text or ""
            m = re.search(
                r'<script[^>]+id="RENDER_DATA"[^>]*>([^<]+)</script>',
                html,
            )
            if m:
                raw = unquote(m.group(1))
                data = json.loads(raw)
                urls.extend(_play_urls_from_obj(data))
            if not urls:
                urls.extend(
                    re.findall(
                        r"https://[^\s\"'<>]+(?:douyinvod|bytevcloud|zjcdn|snssdk|tos-cn)[^\s\"'<>]*",
                        html,
                    )
                )
        except Exception as e:  # noqa: BLE001
            log.debug("ies share html: %s", e)

    # unique keep order
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        u = u.replace("\\u002F", "/").replace("\\/", "/")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def download_ies_share_guest(aweme_id: str, dest: Path) -> Path:
    """Fallback cookieless: iesdouyin share/iteminfo + CDN HTTP."""
    aweme_id = str(aweme_id or "").strip()
    if not aweme_id.isdigit():
        raise RuntimeError(f"aweme_id khong hop le: {aweme_id}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    play_urls = _ies_share_play_urls(aweme_id)
    if not play_urls:
        raise RuntimeError(f"ies guest: khong co play_addr cho {aweme_id}")
    last_err: Exception | None = None
    raw: bytes | None = None
    for u in play_urls[:8]:
        try:
            raw = _http_get_bytes(str(u), timeout=90, referer="https://www.iesdouyin.com/")
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.debug("ies CDN fail: %s", e)
    if not raw:
        raise RuntimeError(f"ies guest: tai CDN that bai ({last_err})") from last_err
    tmp = dest.with_suffix(".iespart.mp4")
    tmp.write_bytes(raw)
    if not probe_has_audio(tmp):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ies guest: file khong co audio ({aweme_id})")
    tmp.replace(dest)
    log.info("Tai ies guest (khong cookie): %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


def download_f2_guest(aweme_id: str, dest: Path) -> Path:
    """Tai 1 video bang f2 + guest token (ttwid/msToken) — KHONG cookie login / browser.

    Lay play_addr tu aweme/detail da ky, tai CDN bang HTTP.
    """
    aweme_id = str(aweme_id or "").strip()
    if not aweme_id.isdigit():
        raise RuntimeError(f"aweme_id khong hop le: {aweme_id}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        from f2.apps.douyin.handler import DouyinHandler
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Chua cai f2 (pip install f2): {e}") from e

    last_meta_err: Exception | None = None
    detail = None
    for attempt in range(1, 4):
        try:
            async def _meta():
                h = DouyinHandler(_f2_guest_kwargs(path=str(dest.parent)))
                return await h.fetch_one_video(aweme_id)

            detail = _run_asyncio(_meta(), timeout=60)
            break
        except Exception as e:  # noqa: BLE001
            last_meta_err = e
            log.warning("f2 guest meta attempt %d fail: %s", attempt, e)
            time.sleep(0.6 * attempt)
    if detail is None:
        raise RuntimeError(f"f2 guest: meta fail ({last_meta_err})") from last_meta_err

    play_urls = list(detail.video_play_addr or [])
    if not play_urls:
        raise RuntimeError(f"f2 guest: khong co play_addr cho {aweme_id}")

    # Luu title clip de dat ten file khi user bam Download MP4.
    try:
        title = ""
        for attr in ("desc", "share_title", "image_desc", "caption", "title"):
            val = getattr(detail, attr, None)
            if val:
                title = str(val).strip()
                break
        if title:
            dest.with_suffix(".meta.json").write_text(
                json.dumps(
                    {"title": title[:300], "aweme_id": aweme_id},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
    except Exception as e:  # noqa: BLE001
        log.debug("f2 meta title: %s", e)

    last_err: Exception | None = None
    raw: bytes | None = None
    for u in play_urls:
        try:
            raw = _http_get_bytes(str(u), timeout=90)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.debug("f2 CDN fail: %s", e)
    if not raw:
        raise RuntimeError(f"f2 guest: tai CDN that bai ({last_err})") from last_err

    tmp = dest.with_suffix(".f2part.mp4")
    tmp.write_bytes(raw)
    # Neu mp4 thieu audio — ghep music_play_url (mot so clip tach stream).
    if not probe_has_audio(tmp):
        music = getattr(detail, "music_play_url", None) or ""
        if isinstance(music, (list, tuple)):
            music = music[0] if music else ""
        music = str(music or "").strip()
        if music:
            try:
                mbytes = _http_get_bytes(music, timeout=120)
                mpath = dest.with_suffix(".f2music.mp3")
                mpath.write_bytes(mbytes)
                muxed = dest.with_suffix(".f2mux.mp4")
                run([
                    "ffmpeg", "-y",
                    "-i", str(tmp), "-i", str(mpath),
                    "-c:v", "copy", "-c:a", "aac", "-shortest",
                    str(muxed),
                ])
                if muxed.exists() and muxed.stat().st_size > 10_000 and probe_has_audio(muxed):
                    tmp.unlink(missing_ok=True)
                    mpath.unlink(missing_ok=True)
                    muxed.replace(dest)
                    log.info("Tai f2 guest + mux audio: %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
                    return dest
                muxed.unlink(missing_ok=True)
                mpath.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                log.warning("f2 mux audio loi: %s", e)
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"f2 guest: file khong co audio ({aweme_id})")

    tmp.replace(dest)
    log.info("Tai f2 guest (khong cookie): %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


def _thumb_from_aweme(item: dict) -> str:
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    for src in (
        video.get("cover"),
        video.get("origin_cover"),
        video.get("dynamic_cover"),
        item.get("cover"),
        item.get("video"),
    ):
        if isinstance(src, dict):
            urls = src.get("url_list") or src.get("urlList") or []
            if urls:
                return str(urls[0]).strip()
        elif isinstance(src, str) and src.startswith("http"):
            return src.strip()
    return ""


def _duration_sec_from_aweme(item: dict) -> float | None:
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    raw = item.get("duration")
    if raw is None:
        raw = video.get("duration")
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    # Douyin aweme_list: duration thuong la ms (>1000).
    if n > 1000:
        n = n / 1000.0
    return n


def _meta_from_aweme(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    aid = str(item.get("aweme_id") or item.get("awemeId") or "").strip()
    if not aid.isdigit():
        return None
    title = str(item.get("desc") or item.get("desc_raw") or "").strip()
    if len(title) > 120:
        title = title[:117] + "..."
    dur = _duration_sec_from_aweme(item)
    return {
        "id": aid,
        "url": f"https://www.douyin.com/video/{aid}",
        "title": title,
        "thumb": _thumb_from_aweme(item),
        "duration_sec": round(dur, 1) if dur is not None else None,
    }


def _http_f2_aweme_items(sec_uid: str, limit: int) -> list[dict]:
    """Liet ke 作品 + meta (title/thumb) bang f2 guest — KHONG Chrome/cookie login."""
    if not sec_uid:
        return []
    try:
        from f2.apps.douyin.crawler import DouyinCrawler
        from f2.apps.douyin.model import UserPost
    except Exception as e:  # noqa: BLE001
        log.warning("f2 chua cai (pip install f2): %s", e)
        return []

    limit = max(1, min(int(limit or 40), 100_000))

    async def _fetch() -> list[dict]:
        kwargs = _f2_guest_kwargs()
        out: list[dict] = []
        seen: set[str] = set()
        cursor = 0
        pages = 0
        while len(out) < limit and pages < max(25, (limit // 18) + 10):
            pages += 1
            need = min(18, limit - len(out))
            async with DouyinCrawler(kwargs) as crawler:
                params = UserPost(
                    max_cursor=cursor,
                    count=need,
                    sec_user_id=sec_uid,
                )
                data = await crawler.fetch_user_post(params)
            if not isinstance(data, dict):
                break
            if data.get("status_code") not in (0, None, "0"):
                log.debug(
                    "f2 aweme/post status=%s msg=%s",
                    data.get("status_code"),
                    data.get("status_msg"),
                )
                break
            chunk = 0
            for item in data.get("aweme_list") or []:
                meta = _meta_from_aweme(item)
                if not meta or meta["id"] in seen:
                    continue
                seen.add(meta["id"])
                out.append(meta)
                chunk += 1
                if len(out) >= limit:
                    break
            try:
                nxt = int(data.get("max_cursor") or 0)
            except (TypeError, ValueError):
                nxt = 0
            more = data.get("has_more")
            if chunk == 0:
                break
            if nxt <= cursor:
                break
            if more in (0, False, "0", "false") and chunk < 10:
                break
            cursor = nxt
        return out

    try:
        rows = _run_asyncio(_fetch(), timeout=90)
    except Exception as e:  # noqa: BLE001
        log.warning("f2 aweme/post fail: %s", e)
        return []
    if rows:
        log.info("Kenh f2 HTTP liet ke %d video (khong browser)", len(rows))
    return rows


def _http_f2_aweme_post(sec_uid: str, limit: int) -> list[str]:
    """Wrapper cu: chi tra aweme_id."""
    return [str(r["id"]) for r in _http_f2_aweme_items(sec_uid, limit)]

def _http_aweme_post(opener, sec_uid: str, limit: int) -> list[str]:
    """Goi API 作品 bang cookie — khong mo Chrome."""
    import urllib.parse

    from .chrome_dl import aweme_ids_from_user_post, post_next_cursor

    if not sec_uid:
        return []
    ids: list[str] = []
    seen: set[str] = set()
    cursor = 0
    for _ in range(25):
        if len(ids) >= limit:
            break
        qs = urllib.parse.urlencode({
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "sec_user_id": sec_uid,
            "max_cursor": str(cursor),
            "locate_query": "false",
            "show_live_replay_strategy": "1",
            "need_time_list": "1",
            "time_list_query": "0",
            "count": "18",
            "publish_video_strategy_type": "2",
            "from_user_page": "1",
            "update_version_code": "170400",
            "pc_client_type": "1",
            "version_code": "170400",
            "version_name": "17.4.0",
        })
        href = f"https://www.douyin.com/aweme/v1/web/aweme/post/?{qs}"
        try:
            raw = _http_fetch(opener, href, accept="application/json,text/plain,*/*")
            data = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            log.debug("aweme/post HTTP: %s", e)
            break
        if not isinstance(data, dict):
            break
        chunk = aweme_ids_from_user_post(data, sec_uid, require_owner=False)
        added = 0
        for vid in chunk:
            if vid in seen:
                continue
            seen.add(vid)
            ids.append(vid)
            added += 1
        if added == 0:
            break
        nxt = post_next_cursor(data, cursor)
        if nxt <= cursor:
            break
        cursor = nxt
    return ids


def _http_scrape_channel(url: str, cookies_path: str | None, limit: int) -> list[str]:
    from http.cookiejar import MozillaCookieJar

    from .chrome_dl import sec_uid_from_user_url

    jar = MozillaCookieJar()
    if cookies_path:
        try:
            jar.load(cookies_path, ignore_discard=True, ignore_expires=True)
        except Exception as e:  # noqa: BLE001
            log.debug("load cookies scrape: %s", e)
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    sec_uid = sec_uid_from_user_url(url)
    ids: list[str] = []
    seen_id: set[str] = set()

    def _take(chunk: list[str]) -> None:
        for vid in chunk or []:
            if vid in seen_id:
                continue
            seen_id.add(vid)
            ids.append(vid)

    if sec_uid:
        _take(_http_ies_aweme_post(sec_uid, limit))
    if len(ids) < limit and sec_uid:
        _take(_http_aweme_post(opener, sec_uid, limit))
    if len(ids) < limit:
        pages = [url]
        if sec_uid:
            pages.extend((
                f"https://www.douyin.com/user/{sec_uid}",
                f"https://www.iesdouyin.com/share/user/{sec_uid}",
            ))
        seen_page: set[str] = set()
        for page in pages:
            if len(ids) >= limit:
                break
            key = page.split("?")[0]
            if key in seen_page:
                continue
            seen_page.add(key)
            try:
                html = _http_fetch(opener, page)
            except Exception as e:  # noqa: BLE001
                log.debug("scrape page %s: %s", page[:70], e)
                continue
            _take(_ids_from_channel_html(html, sec_uid))
    if not ids:
        return []
    rows = [(int(v), f"https://www.douyin.com/video/{v}") for v in ids if v.isdigit()]
    return _newest_urls(rows, limit=limit)


def list_channel_video_items(
    url: str,
    cfg: dict | None = None,
    *,
    limit: int = 40,
    allow_chrome: bool = False,
) -> list[dict]:
    """Liet ke video kenh + meta (id, title, thumb). Guest HTTP — khong cookie login."""
    from .chrome_dl import pc_user_url, sec_uid_from_user_url

    global _CHANNEL_LIST_GEN
    with _CHANNEL_LIST_LOCK:
        _CHANNEL_LIST_GEN += 1
        my_gen = _CHANNEL_LIST_GEN

    def _stale() -> bool:
        return my_gen != _CHANNEL_LIST_GEN

    raw = resolve_share_url(extract_url(url))
    pc = pc_user_url(raw) or raw
    sec = sec_uid_from_user_url(pc) or sec_uid_from_user_url(raw)
    cfg = cfg or {}
    limit = max(1, min(int(limit or 40), 100_000))
    expected = _http_user_aweme_count(sec) if sec else 0
    if expected:
        log.info("Kenh %s: profile 作品=%d", pc[:70], expected)
    errors: list[str] = []
    by_id: dict[str, dict] = {}
    order: list[str] = []

    def _absorb_meta(rows: list[dict]) -> None:
        for row in rows or []:
            aid = str((row or {}).get("id") or "").strip()
            if not aid.isdigit():
                continue
            if aid not in by_id:
                order.append(aid)
                by_id[aid] = {
                    "id": aid,
                    "url": str(row.get("url") or f"https://www.douyin.com/video/{aid}"),
                    "title": str(row.get("title") or "").strip(),
                    "thumb": str(row.get("thumb") or "").strip(),
                    "duration_sec": row.get("duration_sec"),
                }
            else:
                cur = by_id[aid]
                if not cur.get("title") and row.get("title"):
                    cur["title"] = str(row["title"]).strip()
                if not cur.get("thumb") and row.get("thumb"):
                    cur["thumb"] = str(row["thumb"]).strip()
                if cur.get("duration_sec") is None and row.get("duration_sec") is not None:
                    cur["duration_sec"] = row.get("duration_sec")

    def _absorb_ids(vids: list[str]) -> None:
        _absorb_meta([
            {
                "id": v,
                "url": f"https://www.douyin.com/video/{v}",
                "title": "",
                "thumb": "",
                "duration_sec": None,
            }
            for v in (vids or [])
            if str(v).isdigit()
        ])

    if allow_chrome:
        log.debug("list_channel: allow_chrome ignored (khong dung browser)")

    if not sec:
        raise classify_download_error(
            "Khong doc duoc sec_uid tu link kenh",
            default_message="Khong tim thay kenh (link khong hop le).",
        )

    try:
        _absorb_ids(_http_ies_aweme_post(sec, limit))
        if len(order) < limit and not _stale():
            _absorb_meta(_http_f2_aweme_items(sec, limit))
        if len(order) < max(3, min(limit, 10)) and not _stale():
            cookies_path = None
            try:
                from pathlib import Path
                import os
                cands: list[Path] = []
                ck = str((cfg or {}).get("cookies_file") or "").strip()
                if ck:
                    cands.append(Path(ck))
                cands.append(
                    Path(os.path.expandvars(r"%LOCALAPPDATA%\VideosCreator\cookies_douyin.txt"))
                )
                cands.append(Path("cookies_douyin.txt"))
                for cand in cands:
                    if cand.is_file() and cand.stat().st_size > 50:
                        cookies_path = str(cand)
                        break
            except Exception:  # noqa: BLE001
                cookies_path = None
            for href in _http_scrape_channel(pc, cookies_path, limit) or []:
                aid = _aweme_id_from_url(href) or ""
                if aid:
                    _absorb_ids([aid])
        if order:
            log.info("Kenh %s: HTTP liet ke %d video", pc[:70], len(order))
        else:
            errors.append("API kenh khong tra video (can: pip install f2)")
    except DyDownloadError:
        raise
    except Exception as e:  # noqa: BLE001
        errors.append(f"scrape: {e}")
        log.warning("list_channel scrape: %s", e)

    if not order and _stale():
        raise classify_download_error("cancelled", default_message="Da huy lay danh sach kenh.")

    if not order:
        raise empty_channel_error(
            " | ".join(errors) or "API kenh khong tra video",
        )

    ranked = sorted(order, key=lambda a: int(a), reverse=True)[:limit]
    out = [by_id[a] for a in ranked if a in by_id]
    log.info(
        "Kenh %s: tong %d video (HTTP f2, 作品=%s)",
        pc[:70],
        len(out),
        expected or "?",
    )
    return out


def list_channel_videos(
    url: str,
    cfg: dict | None = None,
    *,
    limit: int = 40,
    allow_chrome: bool = False,
) -> list[str]:
    """Liet ke URL video kenh (tuong thich cu)."""
    return [
        str(r["url"])
        for r in list_channel_video_items(
            url, cfg, limit=limit, allow_chrome=allow_chrome
        )
    ]



def _build_ytdlp_cmd(
    url: str,
    out_tmpl: str,
    cfg: dict,
    *,
    cookies_file: str | None = None,
    cookies_from_browser: str | None = None,
) -> list[str]:
    ff_args = ytdlp_ffmpeg_args()
    cmd = [
        *ytdlp_argv(),
        *ff_args,
        "--no-playlist",
        "--retries", str(cfg.get("retries", 3)),
        "--no-warnings",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", out_tmpl,
    ]
    max_dur = float(cfg.get("max_duration_sec") or 0)
    if max_dur > 0 and ff_args:
        # chi tai doan dau — tranh keo clip 40+ phut. Can ffmpeg that.
        end = max(5.0, max_dur + 5.0)
        cmd += ["--download-sections", f"*{0}-{end}"]
        cmd += ["--force-keyframes-at-cuts"]
    elif max_dur > 0:
        log.warning("Bo --download-sections: khong thay ffmpeg (yt-dlp se abort neu van cat doan)")
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
    from .utils import ensure_media_bins

    ensure_media_bins()
    log.debug("RUN: %s", " ".join(str(c) for c in cmd))
    kw: dict = {"capture_output": True, "text": True, "env": os.environ.copy()}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.run([str(c) for c in cmd], **kw)


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
    """Tai 1 video Douyin -> tra ve duong dan file mp4 (bat buoc co audio).

    Mac dinh cookieless: f2 guest -> ies share. yt-dlp/Chrome chi khi co cookie
    hoac chrome_fallback (may developer) — may user moi khong can Chrome.
    """
    url = extract_url(url)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    public_url = public_watch_url(url)

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
    guest_err: Exception | None = None

    aweme = video_id or dl_cache.video_id_from_text(public_url) or ""
    if not str(aweme).isdigit():
        m = re.search(r"/video/(\d{10,})", public_url or url or "")
        aweme = m.group(1) if m else ""

    # 2) f2 guest — khong can cookie dang nhap / Chrome / Edge
    if str(aweme).isdigit():
        dest = out_dir / f"{aweme}.mp4"
        try:
            got = download_f2_guest(str(aweme), dest)
            ok = _accept(got, str(aweme), url)
            if ok:
                return ok
            last_err = "f2 guest: file khong hop le / thieu audio"
            guest_err = RuntimeError(last_err)
        except Exception as e:  # noqa: BLE001
            guest_err = e
            last_err = str(e)
            log.warning("f2 guest download that bai: %s", e)

        # 3) iesdouyin share — fallback cho may moi khong cookie AppData
        try:
            got = download_ies_share_guest(str(aweme), dest)
            ok = _accept(got, str(aweme), url)
            if ok:
                return ok
            last_err = "ies guest: file khong hop le / thieu audio"
            guest_err = RuntimeError(last_err)
        except Exception as e:  # noqa: BLE001
            guest_err = e
            last_err = str(e)
            log.warning("ies guest download that bai: %s", e)

    cookies_path, from_browser = _ytdlp_cookies(cfg)
    has_cookies = bool(cookies_path) or bool(from_browser)
    allow_chrome = bool(cfg.get("chrome_fallback", False))

    def _try_ytdlp(label: str) -> Path | None:
        nonlocal last_err
        if not check_tool("yt-dlp"):
            return None
        out_tmpl = str(out_dir / "%(id)s.%(ext)s")
        ck, br = _ytdlp_cookies(cfg)
        chrome_ck = out_dir / "_chrome_cookies.txt"
        if chrome_ck.exists() and chrome_ck.stat().st_size > 50:
            ck = str(chrome_ck)
        cmd = _build_ytdlp_cmd(
            public_url, out_tmpl, cfg,
            cookies_file=ck,
            cookies_from_browser=None if ck else br,
        )
        proc = _run_ytdlp(cmd)
        if proc.returncode != 0 and cookie_util.is_cookie_error(proc.stderr or ""):
            log.warning("yt-dlp bao can cookie -> force refresh...")
            if cfg.get("auto_refresh_cookies", False) and (
                cfg.get("cookies_from_browser") or ""
            ).strip():
                try:
                    ck = str(cookie_util.force_refresh(cfg))
                    proc = _run_ytdlp(
                        _build_ytdlp_cmd(url, out_tmpl, cfg, cookies_file=ck)
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

    # 4) yt-dlp — CHI khi co cookie that. May user moi: bo qua de khong bao Chrome oan.
    if has_cookies or allow_chrome:
        if cfg.get("ytdlp_first", True) or backend not in {"chrome", "playwright"}:
            got = _try_ytdlp("yt-dlp")
            if got:
                return got
            if backend not in {"chrome", "playwright"}:
                log.warning("yt-dlp fail.\n%s", last_err)
            else:
                log.info("yt-dlp soft-fail")
    else:
        log.info("Bo qua yt-dlp (khong cookie) — giu che do khach")

    # 5) Chrome sniff — CHI khi chrome_fallback=true
    if allow_chrome:
        from .chrome_dl import download_with_chrome

        try:
            got = download_with_chrome(public_url, out_dir, cfg)
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

        got = _try_ytdlp("yt-dlp fallback")
        if got:
            return got

    if guest_err is not None:
        # Ghep them last_err (yt-dlp/Chrome) de user/support thay nguyen nhan that
        detail = str(guest_err)
        if last_err and last_err not in detail:
            detail = f"{detail} | {last_err[-400:]}"
        raise classify_download_error(
            detail,
            default_message=(
                "Khong tai duoc video o che do khach. "
                "Thu lai sau, doi link cong khai, hoac thu video khac."
            ),
        )

    raise classify_download_error(
        last_err or "unknown",
        default_message="Khong tai duoc video Douyin co audio (f2 / ies guest).",
    )


def check_aweme_guest(aweme_id: str) -> dict:
    """Kiem tra video ton tai bang f2 guest (khong tai file). Tra meta nhe."""
    aweme_id = str(aweme_id or "").strip()
    if not aweme_id.isdigit():
        raise classify_download_error(
            f"aweme_id khong hop le: {aweme_id}",
            default_message="Khong tim thay video (ID khong hop le).",
        )
    try:
        from f2.apps.douyin.handler import DouyinHandler
    except Exception as e:  # noqa: BLE001
        raise classify_download_error(e, default_message="Chua cai f2.") from e

    async def _meta():
        h = DouyinHandler(_f2_guest_kwargs())
        return await h.fetch_one_video(aweme_id)

    try:
        detail = _run_asyncio(_meta(), timeout=60)
    except Exception as e:  # noqa: BLE001
        raise classify_download_error(e) from e

    play = list(getattr(detail, "video_play_addr", None) or [])
    if not play:
        # Mot so video rieng tu / bi xoa van tra detail rong
        raise classify_download_error(
            "Khong co play_addr",
            default_message="Khong tim thay video (da xoa hoac khong cong khai).",
        )
    title = str(getattr(detail, "desc", None) or "").strip()
    if len(title) > 120:
        title = title[:117] + "..."
    cover = str(getattr(detail, "cover", None) or "").strip()
    return {
        "id": aweme_id,
        "url": f"https://www.douyin.com/video/{aweme_id}",
        "title": title,
        "thumb": cover if cover.startswith("http") else "",
    }


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
