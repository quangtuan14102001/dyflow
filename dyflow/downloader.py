"""Buoc 1: Tai video tu Douyin — uu tien on dinh cho san pham.

Thu tu:
1) Resolve id + dung cache / ban raw cu co audio
2) yt-dlp (neu backend cho phep)
3) Chrome sniff video+audio (bat buoc co audio truoc khi tai nang)
4) yt-dlp full fallback (cookie Chrome)
5) Cache ket qua thanh cong
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import cookies as cookie_util
from . import dl_cache
from .utils import log, run, check_tool, probe_has_audio, probe_duration, ytdlp_argv

# Cache do dai remote (tranh probe lai khi user goi quote nhieu lan)
_DURATION_CACHE: dict[str, tuple[float, float]] = {}  # key -> (expires, seconds)
_DURATION_CACHE_TTL = 1800.0


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
        if cfg.get("auto_refresh_cookies", True) or cfg.get("cookies_file") or from_browser:
            cookies_path = cookie_util.ensure_cookies(cfg)
    except Exception as e:  # noqa: BLE001
        log.debug("probe cookies: %s", e)
        cookies_path = (cfg.get("cookies_file") or "").strip() or None

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
    """(cookies_file, cookies_from_browser) — giong probe/download."""
    cookies_path: str | None = None
    from_browser = (cfg.get("cookies_from_browser") or "").strip() or None
    try:
        if cfg.get("auto_refresh_cookies", True) or cfg.get("cookies_file") or from_browser:
            cookies_path = cookie_util.ensure_cookies(cfg)
    except Exception as e:  # noqa: BLE001
        log.debug("ytdlp cookies: %s", e)
        cookies_path = (cfg.get("cookies_file") or "").strip() or None
    return cookies_path, None if cookies_path else from_browser


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
            cmd, capture_output=True, text=True, timeout=90 + min(120, limit),
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
    with opener.open(req, timeout=20) as resp:
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


def _http_aweme_post(opener, sec_uid: str, limit: int) -> list[str]:
    """Goi API 作品 bang cookie — khong mo Chrome."""
    import urllib.parse

    from .chrome_dl import aweme_ids_from_user_post, post_has_more

    if not sec_uid:
        return []
    ids: list[str] = []
    seen: set[str] = set()
    cursor = 0
    for _ in range(12):
        if len(ids) >= limit:
            break
        qs = urllib.parse.urlencode({
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "sec_user_id": sec_uid,
            "max_cursor": str(cursor),
            "count": "20",
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
        if added == 0 or post_has_more(data) is False:
            break
        nxt = data.get("max_cursor") or (data.get("data") or {}).get("max_cursor")
        try:
            nxt_i = int(nxt or 0)
        except (TypeError, ValueError):
            nxt_i = 0
        if nxt_i <= cursor:
            break
        cursor = nxt_i
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
    pages = [url]
    if sec_uid:
        pages.extend((
            f"https://www.douyin.com/user/{sec_uid}",
            f"https://www.iesdouyin.com/share/user/{sec_uid}",
        ))
    seen_page: set[str] = set()
    ids: list[str] = []
    seen_id: set[str] = set()
    for page in pages:
        key = page.split("?")[0]
        if key in seen_page:
            continue
        seen_page.add(key)
        try:
            html = _http_fetch(opener, page)
        except Exception as e:  # noqa: BLE001
            log.debug("scrape page %s: %s", page[:70], e)
            continue
        for vid in _ids_from_channel_html(html, sec_uid):
            if vid in seen_id:
                continue
            seen_id.add(vid)
            ids.append(vid)
        if len(ids) >= limit:
            break
    if len(ids) < limit and sec_uid:
        for vid in _http_aweme_post(opener, sec_uid, limit):
            if vid in seen_id:
                continue
            seen_id.add(vid)
            ids.append(vid)
    if not ids:
        return []
    rows = [(int(v), f"https://www.douyin.com/video/{v}") for v in ids if v.isdigit()]
    return _newest_urls(rows, limit=limit)


def list_channel_videos(
    url: str,
    cfg: dict | None = None,
    *,
    limit: int = 40,
    allow_chrome: bool = False,
) -> list[str]:
    """Liet ke video cua user/kênh Douyin, khong tai file.

    Chrome (allow_chrome) chi khi yt-dlp + scrape chua du, va co timeout.
    """
    url = resolve_share_url(extract_url(url))
    cfg = cfg or {}
    limit = max(1, min(int(limit or 40), 200))
    errors: list[str] = []
    merged: list[str] = []
    seen: set[str] = set()

    def _absorb(chunk: list[str]) -> None:
        for href in chunk or []:
            if href in seen:
                continue
            seen.add(href)
            merged.append(href)

    if check_tool("yt-dlp"):
        from .chrome_dl import sec_uid_from_user_url

        ytdlp_urls = [url]
        sec = sec_uid_from_user_url(url)
        if sec:
            ytdlp_urls.append(f"https://www.iesdouyin.com/share/user/{sec}")
        last_err = ""
        for href in ytdlp_urls:
            try:
                got = _ytdlp_flat_channel(href, cfg, limit)
                if got:
                    log.info("Kenh %s: yt-dlp liet ke %d video", href[:70], len(got))
                    _absorb(got)
                    break
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                log.warning("list_channel yt-dlp: %s", e)
        if not merged:
            errors.append(f"yt-dlp: {last_err or 'khong thay video'}")

    if len(merged) < limit:
        cookies_path, _from_browser = _ytdlp_cookies(cfg)
        try:
            got = _http_scrape_channel(url, cookies_path, limit)
            if got:
                log.info("Kenh %s: scrape HTML %d video", url[:70], len(got))
                _absorb(got)
            else:
                errors.append("trang kenh khong co danh sach video")
        except Exception as e:  # noqa: BLE001
            errors.append(f"scrape: {e}")
            log.warning("list_channel scrape: %s", e)

    # Chrome chi khi yt-dlp + scrape chua du. Timeout 40s, khong giu UI vo tan.
    if allow_chrome and len(merged) < limit:
        try:
            from .chrome_dl import list_user_videos_with_chrome

            got = list_user_videos_with_chrome(
                url, cfg, limit=limit, timeout_sec=70, headed_fallback=True,
            )
            if got:
                _absorb(got)
            else:
                errors.append("Chrome khong thay video")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Chrome: {e}")
            log.warning("list_channel Chrome: %s", e)

    if not merged:
        raise RuntimeError(" | ".join(errors) or "Khong liet ke duoc kenh")
    rows = [(_aweme_id_from_url(h) or i, h) for i, h in enumerate(merged)]
    out = _newest_urls(rows, limit=limit)
    log.info("Kenh %s: tong %d video (gop nguon)", url[:70], len(out))
    return out


def _build_ytdlp_cmd(
    url: str,
    out_tmpl: str,
    cfg: dict,
    *,
    cookies_file: str | None = None,
    cookies_from_browser: str | None = None,
) -> list[str]:
    cmd = [
        *ytdlp_argv(),
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
