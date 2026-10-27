"""Bao gia credit + nhan dien video/kenh tu 1 o URL."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ..dl_errors import (
    DyDownloadError,
    classify_download_error,
    empty_channel_error,
    invalid_url_error,
)
from ..downloader import (
    check_aweme_guest,
    list_channel_video_items,
    probe_remote_duration,
    resolve_share_url,
)
from ..pipeline import (
    embedded_video_urls,
    extract_douyin_urls,
    is_channel_url,
    normalize_douyin_url,
)
from ..utils import log
from . import billing

_DOUYIN_HINT = (
    "douyin.com",
    "iesdouyin.com",
    "v.douyin.com",
)


def _looks_like_douyin(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _DOUYIN_HINT)


def _require_douyin_input(text: str) -> None:
    """Validate format TRUOC khi goi f2 — tranh nham network/not_found."""
    raw = (text or "").strip()
    if not raw:
        raise invalid_url_error("empty")
    # Co the la nhieu dong / share text — can it nhat 1 manh Douyin
    if extract_douyin_urls(raw, limit=5) or embedded_video_urls(raw):
        return
    if _looks_like_douyin(raw):
        # Co chuoi douyin nhung khong parse duoc — van invalid
        raise invalid_url_error(raw[:120])
    raise invalid_url_error(raw[:120])


def _aweme_id_from_url(url: str) -> str:
    import re

    m = re.search(r"/video/(\d{10,})", url or "")
    return m.group(1) if m else ""


def expand_sources(
    text: str,
    download_cfg: dict,
    *,
    limit: int = 20,
    channel_limit: int | None = None,
    allow_chrome: bool = False,
) -> list[str]:
    """Parse link Douyin (tuong thich cu — mo rong kenh thanh list URL).

    - /video/... hoặc /user/...?modal_id= → 1 clip đang mở
    - /user/... (±?vid=, không modal_id) → N video / cả kênh
    """
    raw = extract_douyin_urls(text or "", limit=max(limit, 20))
    ch_lim = int(channel_limit) if channel_limit is not None else int(limit)
    ch_lim = max(1, ch_lim)
    overall = max(int(limit), ch_lim)
    out: list[str] = []
    seen: set[str] = set()

    for href in embedded_video_urls(text or ""):
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
        if len(out) >= overall:
            return out

    for u in raw:
        resolved = resolve_share_url(u)
        try:
            resolved = normalize_douyin_url(resolved or u)
        except ValueError:
            resolved = resolved or u
        if is_channel_url(resolved):
            room = min(ch_lim, overall - len(out))
            if room <= 0:
                break
            try:
                chunk = [
                    str(r["url"])
                    for r in list_channel_video_items(
                        resolved or u,
                        download_cfg,
                        limit=room,
                        allow_chrome=False,
                    )
                ]
            except DyDownloadError:
                if out:
                    log.warning("Kenh liet ke loi, dung %d video da co", len(out))
                    chunk = []
                else:
                    raise
            except Exception as e:  # noqa: BLE001
                if out:
                    log.warning("Kenh liet ke loi, dung %d video da co: %s", len(out), e)
                    chunk = []
                else:
                    raise classify_download_error(e) from e
            if not chunk and not out:
                raise empty_channel_error(str(resolved or u)[:120])
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


def _item_from_meta(meta: dict, *, duration: float | None = None) -> dict:
    dur = duration if duration is not None else meta.get("duration_sec")
    try:
        dur_f = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur_f = None
    credits = billing.credits_for_duration(dur_f)
    aid = str(meta.get("id") or _aweme_id_from_url(str(meta.get("url") or ""))).strip()
    return {
        "url": str(meta.get("url") or f"https://www.douyin.com/video/{aid}"),
        "id": aid,
        "title": str(meta.get("title") or "").strip(),
        "thumb": str(meta.get("thumb") or "").strip(),
        "duration_sec": round(dur_f, 1) if dur_f is not None else None,
        "duration_label": billing.format_duration_label(dur_f),
        "credits": credits,
        "unknown": dur_f is None,
        "capped": bool(dur_f and dur_f > billing.MAX_DURATION_SEC),
    }


def _quote_channel(
    channel_url: str,
    download_cfg: dict,
    *,
    channel_limit: int,
) -> dict:
    """Tra danh sach de user CHON — khong tu dong tao job tai het."""
    try:
        rows = list_channel_video_items(
            channel_url,
            download_cfg,
            limit=channel_limit,
            allow_chrome=False,
        )
    except DyDownloadError:
        raise
    except Exception as e:  # noqa: BLE001
        raise classify_download_error(e) from e
    if not rows:
        raise empty_channel_error(channel_url[:120])
    items = [_item_from_meta(r) for r in rows]
    total = sum(int(i["credits"]) for i in items)
    return {
        "kind": "channel",
        "items": items,
        "videos": items,
        "total_credits": total,
        "count": len(items),
        "block_sec": billing.BLOCK_SEC,
        "max_duration_sec": billing.MAX_DURATION_SEC,
        "channel_incomplete": len(items) <= 1,
        "channel_listed": len(items),
        "selectable": True,
        "selected_default": False,
    }


def _quote_videos(sources: list[str], download_cfg: dict) -> dict:
    """Bao gia 1+ video don (da resolve)."""
    items: list[dict] = []
    if not sources:
        return {
            "kind": "video",
            "items": [],
            "total_credits": 0,
            "count": 0,
            "block_sec": billing.BLOCK_SEC,
            "max_duration_sec": billing.MAX_DURATION_SEC,
            "channel_incomplete": False,
            "channel_listed": 0,
            "selectable": False,
        }

    # Kiem tra guest ton tai cho video dau (ID gia / da xoa)
    first_id = _aweme_id_from_url(sources[0])
    if first_id and len(sources) == 1:
        check_aweme_guest(first_id)

    skip_probe = len(sources) > 5

    def _one(url: str) -> dict:
        duration = None
        if not skip_probe:
            try:
                duration = probe_remote_duration(url, download_cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("quote probe loi %s: %s", url[:60], e)
        aid = _aweme_id_from_url(url)
        return _item_from_meta(
            {"id": aid, "url": url, "title": "", "thumb": ""},
            duration=duration,
        )

    by_url: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
        futs = {pool.submit(_one, u): u for u in sources}
        for fut in as_completed(futs):
            row = fut.result()
            by_url[row["url"]] = row

    for u in sources:
        items.append(by_url[u])

    total = sum(int(i["credits"]) for i in items)
    return {
        "kind": "video",
        "items": items,
        "total_credits": total,
        "count": len(items),
        "block_sec": billing.BLOCK_SEC,
        "max_duration_sec": billing.MAX_DURATION_SEC,
        "channel_incomplete": False,
        "channel_listed": len(items),
        "selectable": False,
    }


def quote_urls(
    text: str,
    download_cfg: dict,
    *,
    limit: int = 20,
    channel_limit: int | None = None,
    allow_chrome: bool = False,
) -> dict:
    """Nhan 1 url (hoac text) — tu dong video vs kenh.

    - kind=video: items bao gia (Start tao job ngay)
    - kind=channel: items/videos de user chon checkbox (khong tu tai het)
    """
    del allow_chrome  # giu chu ky; luon guest HTTP
    ch_lim = int(channel_limit) if channel_limit is not None else int(limit)
    ch_lim = max(1, min(ch_lim, 100_000))

    _require_douyin_input(text or "")

    # Clip gan tren URL (modal_id) uu tien — 1 video
    embedded = embedded_video_urls(text or "")
    if embedded:
        return _quote_videos(embedded[: max(limit, ch_lim)], download_cfg)

    raw = extract_douyin_urls(text or "", limit=max(limit, 20))
    if not raw:
        raise invalid_url_error((text or "")[:120])

    # Neu co bat ky link kenh thuan -> picker (1 kenh dau)
    for u in raw:
        resolved = resolve_share_url(u)
        try:
            resolved = normalize_douyin_url(resolved or u)
        except ValueError:
            resolved = resolved or u
        if is_channel_url(resolved):
            return _quote_channel(resolved, download_cfg, channel_limit=ch_lim)

    # Con lai: nhieu /video/
    sources = expand_sources(
        text or "",
        download_cfg,
        limit=limit,
        channel_limit=ch_lim,
        allow_chrome=False,
    )
    return _quote_videos(sources, download_cfg)
