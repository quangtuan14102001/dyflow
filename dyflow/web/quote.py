"""Bao gia credit theo do dai clip (probe yt-dlp, khong tai file)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ..downloader import probe_remote_duration
from ..pipeline import extract_douyin_urls
from ..utils import log
from . import billing


def quote_urls(text: str, download_cfg: dict, *, limit: int = 20) -> dict:
    """Parse link + probe duration song song -> bang gia."""
    sources = extract_douyin_urls(text or "", limit=limit)
    items: list[dict] = []
    if not sources:
        return {"items": [], "total_credits": 0, "count": 0}

    def _one(url: str) -> dict:
        duration = None
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
    return {
        "items": items,
        "total_credits": total,
        "count": len(items),
        "block_sec": billing.BLOCK_SEC,
        "max_duration_sec": billing.MAX_DURATION_SEC,
    }
