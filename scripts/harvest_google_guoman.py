#!/usr/bin/env python3
"""Search Google via Playwright for Douyin video IDs until TARGET."""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "douyin_guoman_links.txt"
PROFILE = ROOT / "work" / "_chrome_search_profile"
TARGET = 1000
VID_RE = re.compile(r"(?:douyin\.com/video/|iesdouyin\.com/share/video/)(\d{15,25})")

QUERIES = []
titles = [
    "国漫", "漫剧", "凡人修仙传", "斗破苍穹", "剑来", "仙逆", "沧元图",
    "吞噬星空", "完美世界", "遮天", "牧神记", "斗罗大陆", "神印王座",
    "哪吒之魔童闹海", "狐妖小红娘", "一人之下", "熊出没", "叶罗丽",
    "画江湖之不良人", "灵笼", "炼气十万年", "一念永恒", "全职高手",
    "秦时明月", "罗小黑战记", "雾山五行", "镇魂街", "武庚纪",
    "少年歌行", "百妖谱", "大理寺日志", "斩神", "元尊", "圣墟",
    "大奉打更人", "时光代理人", "铠甲勇士", "开心锤锤", "奶龙",
    "不良人", "超神学院", "凹凸世界", "万古最强宗", "仙武帝尊",
]
for t in titles:
    QUERIES.append(f"site:www.douyin.com/video {t}")
    QUERIES.append(f"site:www.douyin.com/video {t} 动画")
    QUERIES.append(f"site:www.douyin.com/video {t} 剪辑")
for ep in range(1, 80, 3):
    QUERIES.append(f"site:www.douyin.com/video 完美世界 第{ep}集")
    QUERIES.append(f"site:www.douyin.com/video 凡人修仙传 第{ep}集")
    QUERIES.append(f"site:www.douyin.com/video 斗破苍穹 第{ep}集")
    QUERIES.append(f"site:www.douyin.com/video 吞噬星空 第{ep}集")
    QUERIES.append(f"site:www.douyin.com/video 斗罗大陆 第{ep}集")


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
    OUT.write_text(
        "\n".join(f"https://www.douyin.com/video/{i}" for i in ids) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    ids = load_ids()
    seen = set(ids)
    print(f"start={len(ids)} target={TARGET} queries={len(QUERIES)}", flush=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="chrome",
            headless=True,
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()
        for i, q in enumerate(QUERIES):
            if len(ids) >= TARGET:
                break
            url = "https://www.google.com/search?num=20&q=" + quote(q)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(1800)
                html = page.content()
                added = 0
                for vid in VID_RE.findall(html):
                    if vid not in seen:
                        seen.add(vid)
                        ids.append(vid)
                        added += 1
                # also bing as fallback
                if added == 0:
                    page.goto(
                        "https://www.bing.com/search?count=50&q=" + quote(q),
                        wait_until="domcontentloaded",
                        timeout=40000,
                    )
                    page.wait_for_timeout(1500)
                    html = page.content()
                    for vid in VID_RE.findall(html):
                        if vid not in seen:
                            seen.add(vid)
                            ids.append(vid)
                            added += 1
                if added:
                    save(ids)
                print(f"[{i+1}/{len(QUERIES)}] +{added} total={len(ids)} {q[:40]}", flush=True)
            except Exception as e:
                print(f"fail {q}: {e}", flush=True)
            time.sleep(0.8)
        ctx.close()
    save(ids)
    print(f"DONE total={len(ids)}", flush=True)


if __name__ == "__main__":
    main()
