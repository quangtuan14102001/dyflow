"""Bao gia credit theo do dai clip (probe yt-dlp, khong tai file)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ..downloader import list_channel_videos, probe_remote_duration, resolve_share_url
from ..pipeline import embedded_video_urls, extract_douyin_urls, is_channel_url
from ..utils import log
from . import billing


def expand_sources(
    text: str,
    download_cfg: dict,
    *,
    limit: int = 20,
    channel_limit: int | None = None,
    allow_chrome: bool = False,
) -> list[str]:
    """Parse link Douyin; user/kênh -> N video moi nhat (channel_limit)."""
    raw = extract_douyin_urls(text or "", limit=max(limit, 20))
    ch_lim = int(channel_limit) if channel_limit is not None else int(limit)
    ch_lim = max(1, ch_lim)
    overall = max(int(limit), ch_lim)
    out: list[str] = []
    seen: set[str] = set()
    has_channel = any(
        is_channel_url(u) or is_channel_url(resolve_share_url(u)) for u in raw
    )
    # vid= tren link kenh khong tinh la da co video — phai liet ke kenh
    if not has_channel:
        for href in embedded_video_urls(text or ""):
            if href in seen:
                continue
            seen.add(href)
            out.append(href)
            if len(out) >= overall:
                return out
    for u in raw:
        resolved = resolve_share_url(u)
        if is_channel_url(u) or is_channel_url(resolved):
            room = min(ch_lim, overall - len(out))
            if room <= 0:
                break
            try:
                chunk = list_channel_videos(
                    resolved or u,
                    download_cfg,
                    limit=room,
                    allow_chrome=allow_chrome,
                )
            except Exception as e:  # noqa: BLE001
                # vid= tren link kenh: dung tam 1 clip, UI bao thieu danh sach
                fallback = []
                if has_channel:
                    # embedded_video_urls bo qua channel URL — lay vid= truc tiep
                    import re as _re
                    for m in _re.finditer(r"[?&]vid=(\d{10,})", text or "", _re.I):
                        fallback.append(f"https://www.douyin.com/video/{m.group(1)}")
                if out:
                    log.warning("Kenh liet ke loi, dung %d video da co: %s", len(out), e)
                    chunk = []
                elif fallback:
                    log.warning("Kenh liet ke loi, tam dung clip dang mo (vid=): %s", e)
                    chunk = fallback
                else:
                    raise ValueError(
                        "Khong lay duoc danh sach video cua kenh. "
                        "Khong mo duoc danh sach kenh (cookie / Douyin chan). "
                        f"({e})"
                    ) from e
            if not chunk and not out:
                raise ValueError(
                    "Kenh khong co video (hoac Douyin chan). "
                    "Thu lai sau khi mo Douyin tren trinh duyet, hoac dan link 1 video."
                )
        else:
            chunk = [resolved or u]
        for href in chunk:
            if href in seen:
                continue
            seen.add(href)
            out.append(href)
            if len(out) >= overall:
                return out
    return out


def quote_urls(
    text: str,
    download_cfg: dict,
    *,
    limit: int = 20,
    channel_limit: int | None = None,
    allow_chrome: bool = False,
) -> dict:
    """Parse link + probe duration song song -> bang gia."""
    sources = expand_sources(
        text or "",
        download_cfg,
        limit=limit,
        channel_limit=channel_limit,
        allow_chrome=allow_chrome,
    )
    items: list[dict] = []
    if not sources:
        return {"items": [], "total_credits": 0, "count": 0}
    skip_probe = len(sources) > 5

    def _one(url: str) -> dict:
        duration = None
        if not skip_probe:
            try:
                duration = probe_remote_duration(url, download_cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("quote probe loi %s: %s", url[:60], e)
        credits = billing.credits_for_duration(duration)
        return {
            "url": url,
            "duration_sec": round(duration, 1) if duration is not None else None,
            "duration_label": billing.format_duration_label(duration),
            "credits": credits,
            "unknown": duration is None,
            "capped": bool(duration and duration > billing.MAX_DURATION_SEC),
        }

    # Probe song song (toi da 4) de UI khong doi qua lau
    by_url: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
        futs = {pool.submit(_one, u): u for u in sources}
        for fut in as_completed(futs):
            row = fut.result()
            by_url[row["url"]] = row

    # Giu thu tu link user dan
    for u in sources:
        items.append(by_url[u])

    total = sum(int(i["credits"]) for i in items)
    raw = extract_douyin_urls(text or "", limit=max(limit, 20))
    want_channel = any(
        is_channel_url(u) or is_channel_url(resolve_share_url(u)) for u in raw
    )
    listed = len(items)
    ch_lim = int(channel_limit) if channel_limit is not None else int(limit)
    truncated = want_channel and listed in (18, 20, 36) and ch_lim > listed
    return {
        "items": items,
        "total_credits": total,
        "count": listed,
        "block_sec": billing.BLOCK_SEC,
        "max_duration_sec": billing.MAX_DURATION_SEC,
        "channel_incomplete": bool(want_channel and (listed <= 1 or truncated)),
        "channel_listed": listed,
    }
