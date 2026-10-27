"""Try f2 fetch_one_video + yt-dlp cookieless on ies share."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VID = "7679803336194854180"
OUT = ROOT / "work" / "_dl_nocookie_test"
OUT.mkdir(parents=True, exist_ok=True)


async def try_f2() -> None:
    from f2.apps.douyin.handler import DouyinHandler
    from f2.apps.douyin.utils import TokenManager

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
        "path": str(OUT),
    }
    h = DouyinHandler(kwargs)
    data = await h.fetch_one_video(VID)
    print("f2 type", type(data))
    # filter object?
    if hasattr(data, "_to_dict"):
        d = data._to_dict()
    elif hasattr(data, "model_dump"):
        d = data.model_dump()
    elif isinstance(data, dict):
        d = data
    else:
        d = getattr(data, "__dict__", {})
        print("attrs", [a for a in dir(data) if not a.startswith("_")][:40])
    # common fields
    for key in ("aweme_id", "desc", "video_play_addr", "downloads", "video"):
        if isinstance(d, dict) and key in d:
            print(key, str(d[key])[:200])
    # filter helpers
    for name in ("video_play_addr", "downloads", "desc", "aweme_id", "uri"):
        if hasattr(data, name):
            try:
                val = getattr(data, name)
                if callable(val):
                    val = val()
                print("prop", name, str(val)[:200])
            except Exception as e:
                print("prop fail", name, e)


def try_ytdlp() -> None:
    from dyflow.utils import ytdlp_argv

    url = f"https://www.iesdouyin.com/share/video/{VID}"
    out = str(OUT / "%(id)s.%(ext)s")
    cmd = [*ytdlp_argv(), "-o", out, "--no-playlist", url]
    print("ytdlp", " ".join(cmd[-4:]))
    p = subprocess.run(cmd, capture_output=True, text=True)
    print("rc", p.returncode)
    print((p.stderr or p.stdout or "")[-800:])
    files = list(OUT.glob("*"))
    print("files", [(f.name, f.stat().st_size) for f in files])


async def main() -> None:
    print("=== f2 ===")
    try:
        await try_f2()
    except Exception as e:
        print("f2 ERR", type(e).__name__, e)
    print("=== ytdlp ===")
    try_ytdlp()


if __name__ == "__main__":
    asyncio.run(main())
