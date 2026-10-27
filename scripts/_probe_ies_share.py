from __future__ import annotations

import json
import re
from urllib.parse import unquote

from curl_cffi import requests as creq

VID = "7679803336194854180"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"


def main() -> None:
    r = creq.get(
        f"https://www.iesdouyin.com/share/video/{VID}",
        impersonate="chrome131",
        timeout=25,
        headers={"User-Agent": UA, "Referer": "https://www.iesdouyin.com/"},
    )
    t = r.text or ""
    print("len", len(t))
    for pat in (
        "playAddr", "play_addr", "download_addr", "url_list",
        "RENDER_DATA", "_ROUTER_DATA", ".mp4", "bit_rate",
    ):
        print(pat, t.count(pat))

    m2 = re.search(
        r'<script[^>]+id="RENDER_DATA"[^>]*>([^<]+)</script>',
        t,
    )
    print("RENDER match", bool(m2))
    if m2:
        raw = unquote(m2.group(1))
        print("render len", len(raw), "head", raw[:180])
        try:
            d = json.loads(raw)
            print("keys", list(d.keys())[:30])
            blob = json.dumps(d, ensure_ascii=False)
            urls = re.findall(r"https://[^\"\\]+", blob)
            print("urls in render", len(urls))
            for u in urls:
                if any(x in u for x in ("vod", "byte", "tos", "snssdk", "mp4", "video")):
                    print(" ", u[:160])
        except Exception as e:
            print("json err", e)

    m3 = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*;?\s*</script>", t, re.S)
    print("ROUTER match", bool(m3))
    if m3:
        try:
            d = json.loads(m3.group(1))
            blob = json.dumps(d)
            urls = re.findall(r"https://[^\"\\]+", blob)
            print("router urls", len(urls))
            for u in urls[:40]:
                if any(x in u for x in ("vod", "byte", "tos", "mp4", "play")):
                    print(" ", u[:160])
        except Exception as e:
            print("router json", e)

    # Save snippet for inspection
    Path = __import__("pathlib").Path
    Path("work/_ies_share_snip.html").write_text(t[:8000], encoding="utf-8")
    print("wrote work/_ies_share_snip.html")


if __name__ == "__main__":
    main()
