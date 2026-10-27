"""Probe cookieless Douyin single-video download."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from curl_cffi import requests as creq

VID = "7679803336194854180"
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
    "Mobile/15E148 Safari/604.1"
)


def main() -> None:
    urls = [
        f"https://www.iesdouyin.com/share/video/{VID}",
        f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={VID}",
        f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={VID}&aid=6383&device_platform=webapp",
    ]
    for u in urls:
        r = creq.get(
            u,
            impersonate="chrome131",
            timeout=25,
            headers={"User-Agent": UA, "Referer": "https://www.iesdouyin.com/"},
        )
        print("---", r.status_code, len(r.content), u[:80])
        t = r.text or ""
        cdn = re.findall(
            r"https://[^\s\"'<>]+(?:douyinvod|bytevcloud|zjcdn|snssdk)[^\s\"'<>]*",
            t,
        )
        print("cdn", len(cdn), (cdn[0][:120] if cdn else ""))
        if t.strip().startswith("{") or t.strip().startswith("["):
            try:
                d = r.json()
                print("keys", list(d.keys())[:12] if isinstance(d, dict) else type(d))
                if isinstance(d, dict):
                    item = (d.get("item_list") or [None])[0] or d.get("aweme_detail")
                    if item:
                        print("has item", bool(item), "id", item.get("aweme_id"))
            except Exception as e:
                print("json", e)

    # f2 detail
    try:
        import asyncio
        import logging

        logging.disable(logging.CRITICAL)
        from f2.apps.douyin.crawler import DouyinCrawler
        from f2.apps.douyin.utils import TokenManager

        async def _f2():
            ttwid = TokenManager.gen_ttwid()
            ms = TokenManager.gen_real_msToken()
            cookie = f"ttwid={ttwid}; msToken={ms}"
            kwargs = {
                "headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.douyin.com/",
                },
                "proxies": {"http://": None, "https://": None},
                "cookie": cookie,
            }
            async with DouyinCrawler(kwargs) as crawler:
                # try common detail methods
                for name in dir(crawler):
                    if "detail" in name.lower() or "aweme" in name.lower():
                        if name.startswith("_"):
                            continue
                        print("crawler method", name)
                if hasattr(crawler, "fetch_one_video"):
                    data = await crawler.fetch_one_video(VID)
                    print("fetch_one_video", type(data), str(data)[:200])
                if hasattr(crawler, "fetch_post_detail"):
                    data = await crawler.fetch_post_detail(VID)
                    print("fetch_post_detail", type(data), str(data)[:200])

        asyncio.run(_f2())
    except Exception as e:
        print("f2 err", e)


if __name__ == "__main__":
    main()
