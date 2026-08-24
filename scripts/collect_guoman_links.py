#!/usr/bin/env python3
"""Thu thap link Douyin quoc man cho den TARGET, ghi file txt."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "douyin_guoman_links.txt"
PROFILE = ROOT / "work" / "_chrome_dy_profile"
TARGET = 1000
VID_RE = re.compile(r"(?:aweme_id|group_id|video_id|/video/)[\"'=/:_ ]*(\d{15,25})")

QUERIES = [
    "国漫", "漫剧", "AI漫剧", "二次元动画", "修仙国漫",
    "凡人修仙传动画", "斗破苍穹动画", "剑来动画", "仙逆动画",
    "沧元图", "光阴之外", "吞噬星空动画", "完美世界动画",
    "遮天动画", "牧神记动画", "斗罗大陆动画", "神印王座动画",
    "哪吒之魔童闹海", "狐妖小红娘", "一人之下动画",
    "万妖图录传", "发配边关罪妻", "一斩苍穹", "择日飞升",
    "熊出没", "开心锤锤", "奶龙", "铠甲勇士", "叶罗丽",
    "画江湖之不良人", "灵笼", "炼气十万年", "一念永恒动画",
]


def load_ids() -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    if OUT.exists():
        for ln in OUT.read_text(encoding="utf-8").splitlines():
            m = re.search(r"(\d{15,25})", ln.strip())
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                ids.append(m.group(1))
    return ids


def save(ids: list[str]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "\n".join(f"https://www.douyin.com/video/{i}" for i in ids) + "\n",
        encoding="utf-8",
    )


def harvest_text(text: str, ids: list[str], seen: set[str]) -> int:
    n = 0
    for m in VID_RE.finditer(text or ""):
        vid = m.group(1)
        if vid not in seen and len(vid) >= 15:
            seen.add(vid)
            ids.append(vid)
            n += 1
            if len(ids) >= TARGET:
                return n
    # JSON nested aweme_id
    try:
        data = json.loads(text)
    except Exception:
        return n
    stack = [data]
    while stack and len(ids) < TARGET:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in {"aweme_id", "awemeId", "group_id", "video_id"} and isinstance(v, (str, int)):
                    vid = str(v)
                    if vid.isdigit() and len(vid) >= 15 and vid not in seen:
                        seen.add(vid)
                        ids.append(vid)
                        n += 1
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur[:80])
    return n


def main() -> None:
    ids = load_ids()
    seen = set(ids)
    print(f"start={len(ids)} target={TARGET}", flush=True)
    if len(ids) >= TARGET:
        print("already enough")
        return

    PROFILE.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                channel="chrome",
                headless=False,
                locale="zh-CN",
                viewport={"width": 1400, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                headless=False,
                locale="zh-CN",
                viewport={"width": 1400, "height": 900},
            )

        def on_response(resp) -> None:
            try:
                url = resp.url
                if "douyin.com" not in url and "iesdouyin" not in url:
                    return
                if resp.status != 200:
                    return
                body = resp.text()
                if not body or len(body) < 40:
                    return
                added = harvest_text(body, ids, seen)
                if added:
                    save(ids)
                    print(f"+{added} total={len(ids)} via {url[:90]}", flush=True)
            except Exception:
                return

        page = ctx.new_page()
        page.on("response", on_response)

        # Snowball related videos from known IDs (search HTML has no aweme_id).
        idx = 0
        while len(ids) < TARGET and idx < len(ids):
            vid = ids[idx]
            idx += 1
            try:
                page.goto(
                    f"https://www.douyin.com/video/{vid}",
                    wait_until="domcontentloaded",
                    timeout=35000,
                )
                page.wait_for_timeout(2800)
                harvest_text(page.content(), ids, seen)
                try:
                    hrefs = page.eval_on_selector_all(
                        "a[href*='/video/']",
                        "els => els.map(e => e.href)",
                    )
                    harvest_text("\n".join(hrefs or []), ids, seen)
                except Exception:
                    pass
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(1200)
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(900)
                save(ids)
                if idx % 5 == 0 or len(ids) != idx:
                    print(f"video {idx}/{len(ids)} total={len(ids)}", flush=True)
            except Exception as e:
                print(f"fail video {vid}: {e}", flush=True)
            time.sleep(0.4)

        ctx.close()

    save(ids)
    print(f"DONE total={len(ids)} file={OUT}", flush=True)


if __name__ == "__main__":
    main()
