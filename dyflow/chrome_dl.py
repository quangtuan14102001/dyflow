"""Tai Douyin bang Chrome that (Playwright) khi yt-dlp bi chan/captcha.

Cookie file khong du: Douyin can browser fingerprint + cookie session.
Mo Chrome, bat URL CDN, tai bang cookie cua context (khong curl tran), merge.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from .utils import log, run, ytdlp_argv

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_CDN_HINTS = (
    "douyinvod",
    "bytevcloud",
    "bytecdn",
    "zjcdn.com",
    "tos-cn-vd",
    "tos-cn-ve",
    "mime_type=video",
    "mime_type=audio",
    "media-video",
    "media-audio",
    "/video/tos/",
    "/audio/tos/",
    "v3-web.douyinvod",
    "v26-",
    "v9-",
)


def _load_netscape_cookies(path: Path) -> list[dict]:
    now = time.time()
    cookies: list[dict] = []
    if not path.exists():
        return cookies
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, cpath, secure, expires, name, value = parts[:7]
        if "douyin" not in domain and "byte" not in domain:
            continue
        try:
            exp = int(float(expires))
        except ValueError:
            exp = -1
        if exp > 10_000_000_000:
            exp //= 1000
        if exp > 0 and (exp < now - 86400 * 365 * 20 or exp > now + 86400 * 365 * 20):
            exp = int(now + 86400 * 30)
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": cpath or "/",
            "secure": secure.upper() == "TRUE",
            "expires": exp if exp > 0 else -1,
        })
    return cookies


def _write_netscape_cookies(cookies: list[dict], path: Path) -> None:
    lines = ["# Netscape HTTP Cookie File", "# dyflow chrome export", ""]
    for c in cookies:
        domain = c.get("domain") or ""
        if "douyin" not in domain and "byte" not in domain:
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = int(c.get("expires") or 0)
        if expires < 0:
            expires = 0
        lines.append(
            "\t".join([
                domain, flag, c.get("path") or "/", secure,
                str(expires), c.get("name") or "", c.get("value") or "",
            ])
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cookie_header(cookies: list[dict]) -> str:
    parts = []
    for c in cookies:
        name = c.get("name") or ""
        if not name:
            continue
        parts.append(f"{name}={c.get('value') or ''}")
    return "; ".join(parts)


def _score_url(u: str, kind: str) -> int:
    low = u.lower()
    if any(x in low for x in (
        ".apk", ".exe", ".js?", ".css", ".png", ".jpg", ".jpeg", ".gif",
        ".webp", ".svg", "byteimg.com", "/obj/", "advert",
    )):
        return 0
    score = 0
    if kind == "video":
        if "media-audio" in low or "mime_type=audio" in low or "/audio/" in low:
            return 0
        if "media-video" in low or "mime_type=video" in low:
            score += 10
        if "avc1" in low or "h264" in low or "hvc1" in low or "hevc" in low:
            score += 2
        if "douyinvod" in low or "bytevcloud" in low or "tos-cn" in low:
            score += 1
    else:
        if "media-video" in low or "mime_type=video" in low:
            return 0
        if "media-audio" in low or "mime_type=audio" in low:
            score += 12
        if "/audio/" in low or "audio_mp4" in low:
            score += 8
        if "douyinvod" in low or "bytevcloud" in low or "tos-cn-vd" in low or "tos-cn-ve" in low:
            score += 1
        if score < 8:
            return 0
    m = re.search(r"[?&]br=(\d+)", u)
    if m:
        score += min(int(m.group(1)) // 100, 20)
    return score


def _pick_best(urls: list[str], kind: str) -> str | None:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        score = _score_url(u, kind)
        if score:
            scored.append((score, u))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _walk_collect_urls(obj, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in {"url_list", "urlList"} and isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item.startswith("http"):
                        out.append(item.replace("\\u002F", "/").replace("\\/", "/"))
            elif isinstance(v, str) and v.startswith("http") and (
                "douyin" in v or "byte" in v or "tos-cn" in v or "zjcdn" in v
            ):
                out.append(v.replace("\\u002F", "/").replace("\\/", "/"))
            else:
                _walk_collect_urls(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_collect_urls(item, out)


def _extract_from_render(html: str) -> tuple[str | None, str | None]:
    urls: list[str] = []
    m = re.search(
        r'<script id="RENDER_DATA" type="application/json">([^<]+)</script>',
        html,
    )
    if m:
        raw = unquote(m.group(1))
        try:
            data = json.loads(raw)
            _walk_collect_urls(data, urls)
        except Exception:  # noqa: BLE001
            urls.extend(
                u.replace("\\u002F", "/").replace("\\/", "/")
                for u in re.findall(r"https:[\\/]+[^\"\\]{20,800}", raw)
            )
    for u in re.findall(
        r"https://[a-zA-Z0-9._/-]+\.(?:douyinvod|bytevcloud|zjcdn)[^\s\"'<>\\]{10,600}",
        html,
    ):
        urls.append(u.replace("&amp;", "&"))
    return _pick_best(urls, "video"), _pick_best(urls, "audio")


def _ytdlp_audio_only(page_url: str, cookies_file: Path, dest: Path) -> bool:
    if not page_url or not cookies_file.exists():
        return False
    try:
        run([
            *ytdlp_argv(),
            "--no-playlist",
            "--no-warnings",
            "-f", "ba/b",
            "--cookies", str(cookies_file),
            "-o", str(dest.with_suffix("")) + ".%(ext)s",
            page_url,
        ])
    except Exception as e:  # noqa: BLE001
        log.warning("yt-dlp audio-only that bai: %s", e)
        return False
    cands = sorted(
        dest.parent.glob(dest.stem + ".*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in cands:
        if p.suffix.lower() in {".m4a", ".mp3", ".webm", ".aac", ".mp4"} and p.stat().st_size > 1000:
            if p != dest:
                p.replace(dest)
            return dest.exists() and dest.stat().st_size > 1000
    return False


def _ytdlp_full(
    page_url: str,
    cookies_file: Path,
    out_path: Path,
    fetch_t: float,
) -> bool:
    from .utils import probe_has_audio

    if not page_url or not cookies_file.exists():
        return False
    out_tmpl = str(out_path.with_suffix("")) + ".%(ext)s"
    cmd = [
        *ytdlp_argv(), "--no-playlist", "--no-warnings",
        "-f", "bv*+ba/b", "--merge-output-format", "mp4",
        "--cookies", str(cookies_file),
        "-o", out_tmpl,
    ]
    if fetch_t > 0:
        cmd += ["--download-sections", f"*0-{fetch_t}", "--force-keyframes-at-cuts"]
    cmd.append(page_url)
    try:
        run(cmd)
    except Exception as e:  # noqa: BLE001
        log.warning("yt-dlp full that bai: %s", e)
        return False
    if out_path.exists() and probe_has_audio(out_path):
        return True
    stem = out_path.stem
    for p in sorted(
        out_path.parent.glob(f"{stem}*.mp4"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    ):
        if ".video." in p.name:
            continue
        if probe_has_audio(p):
            if p != out_path:
                p.replace(out_path)
            return True
    return False


def _fetch_media(
    url: str,
    dest: Path,
    max_sec: float = 0.0,
    *,
    cookie: str = "",
    referer: str = "https://www.douyin.com/",
    user_agent: str = _DEFAULT_UA,
    req_headers: dict[str, str] | None = None,
) -> None:
    """Tai media kem Cookie/Referer — tranh CDN 403 khi curl tran."""
    hdrs = dict(req_headers or {})
    # chuan hoa key
    lower = {k.lower(): v for k, v in hdrs.items()}
    cookie = cookie or lower.get("cookie", "")
    referer = lower.get("referer") or referer
    user_agent = lower.get("user-agent") or user_agent

    header_blob = f"Referer: {referer}\r\n"
    if cookie:
        header_blob += f"Cookie: {cookie}\r\n"
    for k, v in hdrs.items():
        lk = k.lower()
        if lk in {"cookie", "referer", "user-agent", "host", "content-length"}:
            continue
        if lk.startswith("sec-") or lk in {"accept", "accept-encoding", "accept-language", "origin"}:
            header_blob += f"{k}: {v}\r\n"

    if max_sec and max_sec > 0:
        try:
            run([
                "ffmpeg", "-y",
                "-user_agent", user_agent,
                "-headers", header_blob,
                "-t", f"{max_sec:.2f}",
                "-i", url,
                "-c", "copy",
                str(dest),
            ])
            if dest.exists() and dest.stat().st_size > 1000:
                return
        except Exception as e:  # noqa: BLE001
            log.warning("ffmpeg fetch -t that bai (%s) — fallback curl", e)

    curl_cmd = [
        "curl", "-L", "--fail", "--retry", "3",
        "--max-time", "600",
        "-A", user_agent,
        "-H", f"Referer: {referer}",
        "-o", str(dest),
    ]
    if cookie:
        curl_cmd += ["-H", f"Cookie: {cookie}"]
    curl_cmd.append(url)
    run(curl_cmd)


def download_with_chrome(
    url: str,
    out_dir: str | Path,
    cfg: dict | None = None,
) -> Path:
    """Mo Chrome that, bat CDN, tai + merge mp4 (bat buoc co audio)."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "Can playwright de fallback Douyin.\n"
            "  pip install playwright && playwright install chromium"
        ) from e

    from . import dl_cache
    from .utils import probe_has_audio

    cfg = cfg or {}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    max_dur = float(cfg.get("max_duration_sec") or 0)
    fetch_t = (max_dur + 8.0) if max_dur > 0 else 0.0

    profile = Path(cfg.get("chrome_profile_dir") or "./work/_chrome_dy_profile")
    profile.mkdir(parents=True, exist_ok=True)
    headless = bool(cfg.get("chrome_headless", False))
    cookies_file = Path(cfg.get("cookies_file") or "./cookies_douyin.txt")

    # VPS thuong khong co man hinh — can Xvfb cho Chrome headed (bo qua tren Windows)
    if not headless and sys.platform != "win32":
        try:
            from .display import ensure_display
            disp = ensure_display()
            if disp:
                log.info("Chrome headed dung DISPLAY=%s", disp)
        except Exception as e:  # noqa: BLE001
            log.warning("ensure_display: %s", e)

    try:
        from .web.locks import CHROME_LOCK
    except Exception:  # noqa: BLE001
        CHROME_LOCK = None

    log.info("Chrome download: mo Douyin (headless=%s)...", headless)
    if CHROME_LOCK is not None:
        CHROME_LOCK.acquire()
    try:
        return _chrome_session_download(
            url=url,
            out_dir=out_dir,
            profile=profile,
            headless=headless,
            cookies_file=cookies_file,
            fetch_t=fetch_t,
            source_url=url,
        )
    finally:
        if CHROME_LOCK is not None:
            CHROME_LOCK.release()


def _chrome_session_download(
    *,
    url: str,
    out_dir: Path,
    profile: Path,
    headless: bool,
    cookies_file: Path,
    fetch_t: float,
    source_url: str,
) -> Path:
    from playwright.sync_api import sync_playwright

    from . import dl_cache
    from .utils import probe_has_audio

    found: list[str] = []
    # URL -> request headers (de replay Cookie/UA)
    req_hdrs: dict[str, dict[str, str]] = {}

    launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    common_kwargs = {
        "locale": "zh-CN",
        "args": launch_args,
        "viewport": {"width": 1280, "height": 900},
    }
    # Win ARM VM: Edge co san. Chrome/Chromium Playwright thuong chua cai.
    channels = ("msedge", "chrome") if sys.platform == "win32" else ("chrome",)

    with sync_playwright() as p:
        browser = None
        context = None
        launch_errs: list[str] = []

        for ch in channels:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    channel=ch,
                    headless=headless,
                    **common_kwargs,
                )
                log.info("Browser persistent (channel=%s) OK", ch)
                break
            except Exception as e:  # noqa: BLE001
                launch_errs.append(f"{ch}-persistent: {e}")
                log.warning("%s persistent fail: %s", ch, e)
                context = None

        # Chromium Playwright + profile
        if context is None:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    headless=headless,
                    **common_kwargs,
                )
                log.info("Chromium persistent OK")
            except Exception as e:  # noqa: BLE001
                launch_errs.append(f"chromium-persistent: {e}")
                log.warning("Chromium persistent fail: %s", e)

        # Ephemeral Edge / Chrome / Chromium + nap cookie file
        if context is None:
            eph_channels: list = list(channels) + [None]
            for channel in eph_channels:
                try:
                    kwargs = {"headless": headless, "args": launch_args}
                    if channel:
                        kwargs["channel"] = channel
                    browser = p.chromium.launch(**kwargs)
                    context = browser.new_context(
                        locale="zh-CN",
                        user_agent=_DEFAULT_UA,
                        viewport={"width": 1280, "height": 900},
                    )
                    for c in _load_netscape_cookies(cookies_file):
                        try:
                            context.add_cookies([c])
                        except Exception:  # noqa: BLE001
                            pass
                    log.info("Browser ephemeral OK (channel=%s)", channel or "chromium")
                    break
                except Exception as e:  # noqa: BLE001
                    launch_errs.append(f"ephemeral-{channel or 'chromium'}: {e}")
                    browser = None
                    context = None

        if context is None:
            raise RuntimeError(
                "Khong mo duoc Edge/Chrome de tai Douyin. "
                "Cai Microsoft Edge, roi: pip install playwright\n"
                + "\n".join(launch_errs[-4:])
            )

        page = context.pages[0] if context.pages else context.new_page()

        def on_response(resp) -> None:
            if resp.status not in (200, 206):
                return
            u = resp.url
            ct = ""
            try:
                ct = (resp.headers.get("content-type") or "").lower()
            except Exception:  # noqa: BLE001
                pass
            if not (
                any(h in u for h in _CDN_HINTS)
                or "audio" in ct
                or "video" in ct
                or "audio" in u.lower()
            ):
                return
            found.append(u)
            try:
                rh = dict(resp.request.headers)
                if rh:
                    req_hdrs[u] = rh
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)

        for _ in range(20):
            final_url = page.url
            if re.search(r"/video/\d{10,}", final_url):
                break
            page.wait_for_timeout(500)

        page.wait_for_timeout(800)
        final_url = page.url

        for sel in ("video", "[class*='player']", "xg-video-container"):
            try:
                page.click(sel, timeout=2000)
                break
            except Exception:  # noqa: BLE001
                continue
        try:
            page.keyboard.press("Space")
        except Exception:  # noqa: BLE001
            pass

        video_url = audio_url = None
        html_snap = ""
        video_seen_at: float | None = None
        captcha = False

        # Doi audio lau hon (toi da ~55s) — tranh cat som khi CDN cham
        for i in range(55):
            title = page.title() or ""
            final_url = page.url
            if "验证" in title:
                captcha = True
            if found:
                video_url = _pick_best(found, "video")
                audio_url = _pick_best(found, "audio")
            if "验证" not in title:
                try:
                    html_snap = page.content()
                    v2, a2 = _extract_from_render(html_snap)
                    video_url = video_url or v2
                    audio_url = audio_url or a2
                except Exception:  # noqa: BLE001
                    pass

            if video_url and audio_url:
                break
            if video_url and video_seen_at is None:
                video_seen_at = time.time()
            # Sau 40s co video van khong audio -> thu muxed
            if video_url and video_seen_at and (time.time() - video_seen_at) >= 40:
                break
            if i > 0 and i % 5 == 0:
                try:
                    page.evaluate(
                        """() => {
                          const v = document.querySelector('video');
                          if (v) { v.muted = false; v.volume = 1; v.play().catch(()=>{}); }
                        }"""
                    )
                except Exception:  # noqa: BLE001
                    pass
            page.wait_for_timeout(1000)

        if not html_snap:
            try:
                html_snap = page.content()
            except Exception:  # noqa: BLE001
                html_snap = ""
        if not video_url or not audio_url:
            try:
                v2, a2 = _extract_from_render(html_snap)
                video_url = video_url or v2
                audio_url = audio_url or a2
            except Exception:  # noqa: BLE001
                pass
            video_url = video_url or _pick_best(found, "video")
            audio_url = audio_url or _pick_best(found, "audio")

        # Export cookie (job + global) truoc khi tai
        jar = []
        try:
            jar = context.cookies()
            cookie_export = out_dir / "_chrome_cookies.txt"
            _write_netscape_cookies(jar, cookie_export)
            _write_netscape_cookies(jar, cookies_file)
        except Exception as e:  # noqa: BLE001
            log.debug("Export cookie Chrome that bai: %s", e)
            cookie_export = cookies_file if cookies_file.exists() else out_dir / "_chrome_cookies.txt"

        cookie_str = _cookie_header(jar)

        vid = dl_cache.video_id_from_text(final_url or "", html_snap or "", source_url, url)
        if not vid:
            context.close()
            if browser:
                browser.close()
            raise RuntimeError(
                "Khong resolve duoc aweme/video id tu URL Douyin. "
                "Link short chua redirect xong hoac bi captcha — "
                "mo Chrome dang nhap Douyin trong profile roi thu lai."
            )

        # Cache sau khi co id that
        reused = dl_cache.try_reuse(vid, out_dir)
        if reused:
            dl_cache.remember_short(source_url, vid)
            context.close()
            if browser:
                browser.close()
            return reused
        local = dl_cache.find_local_good_copy(vid)
        if local:
            dest = out_dir / f"{vid}.mp4"
            shutil.copy2(local, dest)
            dl_cache.save(vid, dest)
            dl_cache.remember_short(source_url, vid)
            log.info("Chrome: dung lai ban raw cu %s", local)
            context.close()
            if browser:
                browser.close()
            return dest

        if captcha and not video_url:
            context.close()
            if browser:
                browser.close()
            raise RuntimeError(
                "Douyin hien captcha (验证码). "
                f"Mo Chrome profile {profile} dang nhap Douyin (chrome_headless: false) roi thu lai."
            )

        if not video_url:
            # Van thu yt-dlp full voi cookie vua export
            out_path = out_dir / f"{vid}.mp4"
            if _ytdlp_full(final_url or url, Path(cookie_export), out_path, fetch_t):
                dl_cache.save(vid, out_path)
                dl_cache.remember_short(source_url, vid)
                context.close()
                if browser:
                    browser.close()
                return out_path
            context.close()
            if browser:
                browser.close()
            raise RuntimeError(
                "Chrome khong bat duoc stream Douyin (co the bi captcha). "
                "Thu chrome_headless: false va dang nhap Douyin trong Chrome profile."
            )

        v_path = out_dir / f"{vid}.video.mp4"
        a_path = out_dir / f"{vid}.audio.m4a"
        out_path = out_dir / f"{vid}.mp4"

        log.info(
            "Stream: id=%s video=%s audio=%s (found=%d)",
            vid,
            "yes" if video_url else "no",
            "yes" if audio_url else "no",
            len(found),
        )

        def _hdr_for(media_url: str) -> dict[str, str]:
            h = dict(req_hdrs.get(media_url) or {})
            if cookie_str and "cookie" not in {k.lower() for k in h}:
                h["Cookie"] = cookie_str
            if "referer" not in {k.lower() for k in h}:
                h["Referer"] = "https://www.douyin.com/"
            if "user-agent" not in {k.lower() for k in h}:
                h["User-Agent"] = _DEFAULT_UA
            return h

        have_audio = False
        if audio_url and audio_url != video_url:
            log.info("Dang tai stream audio (authenticated)...")
            try:
                _fetch_media(
                    audio_url, a_path, fetch_t,
                    cookie=cookie_str, req_headers=_hdr_for(audio_url),
                )
                have_audio = a_path.exists() and a_path.stat().st_size > 1000
            except Exception as e:  # noqa: BLE001
                log.warning("Tai audio CDN that bai: %s", e)

        if not have_audio:
            for alt in found:
                if alt in {video_url, audio_url}:
                    continue
                if _score_url(alt, "audio") < 8:
                    continue
                try:
                    log.info("Thu audio URL phu...")
                    _fetch_media(
                        alt, a_path, fetch_t,
                        cookie=cookie_str, req_headers=_hdr_for(alt),
                    )
                    have_audio = a_path.exists() and a_path.stat().st_size > 1000
                    if have_audio:
                        break
                except Exception as e:  # noqa: BLE001
                    log.warning("Audio URL phu that bai: %s", e)

        if not have_audio:
            log.info("Thu yt-dlp audio-only...")
            have_audio = _ytdlp_audio_only(final_url or url, Path(cookie_export), a_path)

        # Muxed: tai video truoc, neu da co audio track thi dung luon
        if not have_audio:
            log.warning("Chua co audio rieng — thu muxed video (co the da mux audio)")
            try:
                _fetch_media(
                    video_url, v_path, fetch_t,
                    cookie=cookie_str, req_headers=_hdr_for(video_url),
                )
                if v_path.exists() and probe_has_audio(v_path):
                    v_path.replace(out_path)
                    dl_cache.save(vid, out_path)
                    dl_cache.remember_short(source_url, vid)
                    log.info("Tai xong (muxed Chrome): %s", out_path.name)
                    context.close()
                    if browser:
                        browser.close()
                    return out_path
            except Exception as e:  # noqa: BLE001
                log.warning("Muxed fetch that bai: %s", e)

            if _ytdlp_full(final_url or url, Path(cookie_export), out_path, fetch_t):
                dl_cache.save(vid, out_path)
                dl_cache.remember_short(source_url, vid)
                log.info("Tai xong (yt-dlp trong Chrome path): %s", out_path.name)
                context.close()
                if browser:
                    browser.close()
                return out_path

            context.close()
            if browser:
                browser.close()
            raise RuntimeError(
                "Khong bat duoc audio Douyin (CDN + yt-dlp). "
                f"Mo Chrome dang nhap trong {profile} roi thu lai."
            )

        log.info("Dang tai stream video%s...", f" (~{fetch_t:.0f}s)" if fetch_t else "")
        if not v_path.exists() or v_path.stat().st_size < 1000:
            _fetch_media(
                video_url, v_path, fetch_t,
                cookie=cookie_str, req_headers=_hdr_for(video_url),
            )

        # Neu video vua tai da co audio (muxed) va ta cung co a_path — uu tien merge neu a ok
        if probe_has_audio(v_path) and (
            not a_path.exists() or a_path.stat().st_size < 1000
        ):
            v_path.replace(out_path)
        else:
            try:
                run([
                    "ffmpeg", "-y",
                    "-i", str(v_path), "-i", str(a_path),
                    "-c", "copy", "-shortest", str(out_path),
                ])
            except Exception:
                run([
                    "ffmpeg", "-y",
                    "-i", str(v_path), "-i", str(a_path),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", str(out_path),
                ])

        context.close()
        if browser:
            browser.close()

    if out_path.exists() and out_path.stat().st_size > 1000 and probe_has_audio(out_path):
        log.info("Tai xong (Chrome): %s", out_path.name)
        dl_cache.save(vid, out_path)
        dl_cache.remember_short(source_url, vid)
        return out_path
    raise RuntimeError("Tai Chrome that bai (file rong / khong audio)")


def sec_uid_from_user_url(url: str) -> str:
    m = re.search(
        r"(?:douyin\.com/(?:user|share/user)/|iesdouyin\.com/share/user/)([^/?#]+)",
        url or "",
        re.I,
    )
    return unquote(m.group(1)).strip() if m else ""


def _author_sec_uid(item: dict) -> str:
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    if not author:
        author = item.get("authorInfo") if isinstance(item.get("authorInfo"), dict) else {}
    return str(author.get("sec_uid") or author.get("secUid") or "").strip()


def post_has_more(data) -> bool | None:
    """True/False neu payload aweme/post co has_more; None neu khong ro."""
    if not isinstance(data, dict):
        return None
    for blob in (data, data.get("data") if isinstance(data.get("data"), dict) else {}):
        if "has_more" in blob:
            v = blob.get("has_more")
            return v in (1, True, "1", "true", "True")
    return None


def aweme_ids_from_user_post(
    data,
    sec_uid: str = "",
    *,
    require_owner: bool = False,
) -> list[str]:
    """Chi lay aweme_list cua API 作品. require_owner=True khi parse HTML lan (co video goi y)."""
    if not isinstance(data, dict):
        return []
    lists: list = []
    for key in ("aweme_list", "awemeList"):
        v = data.get(key)
        if isinstance(v, list):
            lists.append(v)
    inner = data.get("data")
    if isinstance(inner, dict):
        for key in ("aweme_list", "awemeList"):
            v = inner.get(key)
            if isinstance(v, list):
                lists.append(v)
    out: list[str] = []
    seen: set[str] = set()
    for lst in lists:
        for item in lst:
            if not isinstance(item, dict):
                continue
            aid = item.get("aweme_id") or item.get("awemeId")
            s = str(aid or "").strip()
            if not re.fullmatch(r"\d{10,}", s) or s in seen:
                continue
            owner = _author_sec_uid(item)
            if require_owner:
                if not sec_uid or owner != sec_uid:
                    continue
            seen.add(s)
            out.append(s)
    return out


def list_user_videos_with_chrome(
    url: str,
    cfg: dict | None = None,
    *,
    limit: int = 40,
    timeout_sec: int = 40,
    headed_fallback: bool = True,
) -> list[str]:
    """Mo Edge/Chrome, bat API aweme/post cua trang user, lay video moi nhat."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Can playwright de liet ke kenh Douyin.") from e

    cfg = cfg or {}
    limit = max(1, min(int(limit or 40), 200))
    profile = Path(cfg.get("chrome_profile_dir") or "./work/_chrome_dy_profile")
    profile.mkdir(parents=True, exist_ok=True)
    cookies_file = Path(cfg.get("cookies_file") or "./cookies_douyin.txt")
    launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    channels = ("msedge", "chrome") if sys.platform == "win32" else ("chrome",)
    common_kwargs = {
        "locale": "zh-CN",
        "args": launch_args,
        "viewport": {"width": 1280, "height": 900},
    }

    try:
        from .web.locks import CHROME_LOCK
    except Exception:  # noqa: BLE001
        CHROME_LOCK = None

    sec_uid = sec_uid_from_user_url(url)
    clean_url = (url or "").split("#")[0].split("?")[0]
    ids: list[str] = []
    seen: set[str] = set()
    has_more = True
    deadline = time.monotonic() + max(8, int(timeout_sec or 40))

    def _remaining_ms(floor: int = 1500) -> int:
        left = int((deadline - time.monotonic()) * 1000)
        return max(floor, left) if left > 0 else 0

    def _add_post_payload(blob) -> None:
        nonlocal has_more
        if not isinstance(blob, dict):
            return
        more = post_has_more(blob)
        if more is False:
            has_more = False
        for vid in aweme_ids_from_user_post(blob, sec_uid, require_owner=False):
            if vid in seen:
                continue
            seen.add(vid)
            ids.append(vid)

    def _run(headless: bool) -> None:
        nonlocal ids, has_more
        has_more = True
        with sync_playwright() as p:
            browser = None
            context = None
            for ch in channels:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(profile),
                        channel=ch,
                        headless=headless,
                        **common_kwargs,
                    )
                    break
                except Exception as e:  # noqa: BLE001
                    log.debug("list-channel %s persistent: %s", ch, e)
                    context = None
            if context is None:
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(profile),
                        headless=headless,
                        **common_kwargs,
                    )
                except Exception as e:  # noqa: BLE001
                    log.debug("list-channel chromium persistent: %s", e)
            if context is None:
                for channel in list(channels) + [None]:
                    try:
                        kwargs = {"headless": headless, "args": launch_args}
                        if channel:
                            kwargs["channel"] = channel
                        browser = p.chromium.launch(**kwargs)
                        context = browser.new_context(
                            locale="zh-CN",
                            user_agent=_DEFAULT_UA,
                            viewport={"width": 1280, "height": 900},
                        )
                        for c in _load_netscape_cookies(cookies_file):
                            try:
                                context.add_cookies([c])
                            except Exception:  # noqa: BLE001
                                pass
                        break
                    except Exception as e:  # noqa: BLE001
                        log.debug("list-channel ephemeral %s: %s", channel, e)
                        browser = None
                        context = None
            if context is None:
                raise RuntimeError("Khong mo duoc Edge/Chrome de liet ke kenh.")

            for c in _load_netscape_cookies(cookies_file):
                try:
                    context.add_cookies([c])
                except Exception:  # noqa: BLE001
                    pass

            page = context.pages[0] if context.pages else context.new_page()

            def on_response(resp) -> None:
                u = (resp.url or "").lower()
                if resp.status != 200:
                    return
                # API 作品 — mot so ban Douyin dung path ngan hon
                if not any(
                    x in u
                    for x in (
                        "aweme/v1/web/aweme/post",
                        "/aweme/post",
                        "aweme/v1/web/aweme/list",
                        "user/post",
                    )
                ):
                    return
                if sec_uid and sec_uid.lower() not in u and "sec_user_id=" in u:
                    return
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    return
                _add_post_payload(data)
                extra = aweme_ids_from_user_post(data, sec_uid, require_owner=False)
                for vid in extra:
                    if vid in seen:
                        continue
                    seen.add(vid)
                    ids.append(vid)

            page.on("response", on_response)
            goto_ms = _remaining_ms(2000)
            if goto_ms <= 0:
                return
            page.goto(
                clean_url or url,
                wait_until="domcontentloaded",
                timeout=min(45000, goto_ms),
            )
            page.wait_for_timeout(min(2000, max(200, _remaining_ms(200))))
            for sel in (
                '[data-e2e="user-post"]',
                'span:has-text("作品")',
                'div:has-text("作品")',
            ):
                if time.monotonic() >= deadline:
                    break
                try:
                    page.locator(sel).first.click(timeout=1500)
                    page.wait_for_timeout(min(1500, max(200, _remaining_ms(200))))
                    break
                except Exception:  # noqa: BLE001
                    continue
            stale = 0
            for _ in range(24):
                if time.monotonic() >= deadline:
                    break
                if len(ids) >= limit:
                    break
                if not has_more and stale >= 1:
                    break
                before = len(ids)
                try:
                    page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight);"
                        "document.querySelector('[data-e2e=\"user-post-list\"]')"
                        "?.scrollBy(0, 2000);"
                    )
                except Exception:  # noqa: BLE001
                    try:
                        page.mouse.wheel(0, 2800)
                    except Exception:  # noqa: BLE001
                        break
                page.wait_for_timeout(min(1400, max(200, _remaining_ms(200))))
                if len(ids) == before:
                    stale += 1
                else:
                    stale = 0
                if stale >= 3:
                    break
            try:
                dom_ids = page.evaluate(
                    """() => {
                      const seen = new Set();
                      const out = [];
                      const root =
                        document.querySelector('[data-e2e="user-post-list"]')
                        || document.querySelector('[data-e2e="scroll-list"]')
                        || document.body;
                      const re = /\\/video\\/(\\d{15,})/;
                      for (const a of root.querySelectorAll('a[href*="/video/"]')) {
                        const m = (a.getAttribute('href') || a.href || '').match(re);
                        if (m && !seen.has(m[1])) { seen.add(m[1]); out.push(m[1]); }
                      }
                      return out;
                    }"""
                )
                for vid in dom_ids or []:
                    if vid in seen:
                        continue
                    seen.add(vid)
                    ids.append(vid)
            except Exception as e:  # noqa: BLE001
                log.debug("list-channel DOM: %s", e)
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
            if browser:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass

    if CHROME_LOCK is not None:
        CHROME_LOCK.acquire()
    try:
        try:
            _run(True)
        except Exception as e:  # noqa: BLE001
            log.warning("list-channel headless fail: %s", e)
            if (
                not ids
                and headed_fallback
                and time.monotonic() < deadline - 5
            ):
                _run(False)
    finally:
        if CHROME_LOCK is not None:
            CHROME_LOCK.release()

    if not ids:
        raise RuntimeError("Chrome mo kenh nhung khong bat duoc video.")
    # aweme id lon = moi hon
    uniq = sorted(set(ids), key=lambda x: int(x), reverse=True)
    out = [f"https://www.douyin.com/video/{v}" for v in uniq[:limit]]
    log.info("Kenh Chrome liet ke %d video", len(out))
    return out
