#!/usr/bin/env python3
"""Harvest Douyin video IDs from Bing/DuckDuckGo until TARGET unique links."""
from __future__ import annotations

import re
import time
import urllib.parse
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "douyin_guoman_links.txt"
TARGET = 1000
VID_RE = re.compile(r"douyin\.com/video/(\d{15,25})")

TITLES = [
    "国漫", "漫剧", "AI漫剧", "国产动画", "修仙动画",
    "凡人修仙传", "斗破苍穹", "剑来", "仙逆", "沧元图",
    "光阴之外", "吞噬星空", "完美世界", "遮天", "牧神记",
    "斗罗大陆", "神印王座", "哪吒之魔童闹海", "狐妖小红娘",
    "一人之下", "万妖图录", "发配边关", "一斩苍穹", "择日飞升",
    "熊出没", "开心锤锤", "奶龙", "铠甲勇士", "叶罗丽",
    "画江湖之不良人", "灵笼", "炼气十万年", "一念永恒",
    "全职高手", "秦时明月", "天行九歌", "武庚纪", "镇魂街",
    "罗小黑战记", "雾山五行", "深海", "大鱼海棠", "白蛇缘起",
    "姜子牙", "长安三万里", "雄狮少年", "大理寺日志",
    "百妖谱", "少年歌行", "剑王朝", "雪中悍刀行", "将夜",
    "诡秘之主", "不良人", "侠岚", "超神学院", "凹凸世界",
    "一人之下冯宝宝", "凡人修仙传韩立", "斗破萧炎",
    "完美世界石昊", "遮天叶凡", "吞噬星空罗峰",
    "斗罗大陆唐三", "神印王座龙皓晨", "仙逆王林",
    "剑来陈平安", "沧元图孟川", "牧神记秦牧",
    "狐妖小红娘涂山苏苏", "哪吒敖丙", "熊出没光头强",
    "叶罗丽冰公主", "开心锤锤动画", "奶龙动画",
    "国漫名场面", "国漫高燃", "国漫剪辑", "国漫推荐",
    "国漫热血", "修仙国漫推荐", "3D国漫", "2D国漫",
    "国漫2024", "国漫2025", "国漫2026", "腾讯动漫",
    "哔哩哔哩国创", "优酷国漫", "爱奇艺国漫",
    "一人之下动画", "凡人修仙传动画", "斗破苍穹年番",
    "完美世界年番", "遮天年番", "吞噬星空年番",
    "斗罗大陆绝世唐门", "斗罗大陆神界传说",
    "画江湖不良人第六季", "灵笼第二季",
    "炼气十万年动画", "一念永恒传承篇",
    "万古最强宗", "开局一座山", "我在修仙界开饭店",
    "仙武帝尊", "武神主宰", "逆天邪神", "万界神主",
    "帝尊", "元尊", "圣墟", "大奉打更人", "宿命之环",
    "斩神", "仙王的日常生活", "我是大神仙",
    "时光代理人", "凸变英雄", "非人哉", "万国志",
    "眷思量", "遇龙", "少年锦衣卫", " vynil",
]

SUFFIXES = [
    "", " 动画", " 剪辑", " 名场面", " 高燃", " 第1集",
    " 第10集", " 第50集", " 推荐", " 漫剧",
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


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"  fetch fail {e}", flush=True)
        return ""


def harvest(html: str, ids: list[str], seen: set[str]) -> int:
    n = 0
    for vid in VID_RE.findall(html or ""):
        if vid not in seen:
            seen.add(vid)
            ids.append(vid)
            n += 1
            if len(ids) >= TARGET:
                break
    return n


def bing(q: str, first: int = 1) -> str:
    qs = urllib.parse.quote(f"site:www.douyin.com/video {q}")
    return fetch(f"https://www.bing.com/search?q={qs}&first={first}&count=50")


def ddg(q: str) -> str:
    qs = urllib.parse.quote(f"site:www.douyin.com/video {q}")
    return fetch(f"https://html.duckduckgo.com/html/?q={qs}")


def main() -> None:
    ids = load_ids()
    seen = set(ids)
    print(f"start={len(ids)} target={TARGET}", flush=True)
    queries: list[str] = []
    for t in TITLES:
        if t.strip() == "vynil":
            continue
        for s in SUFFIXES:
            queries.append((t + s).strip())

    for i, q in enumerate(queries):
        if len(ids) >= TARGET:
            break
        before = len(ids)
        html = bing(q, 1)
        added = harvest(html, ids, seen)
        if added == 0:
            html2 = bing(q, 11)
            added += harvest(html2, ids, seen)
        if added == 0:
            added += harvest(ddg(q), ids, seen)
        if added:
            save(ids)
        print(f"[{i+1}/{len(queries)}] {q!r} +{len(ids)-before} total={len(ids)}", flush=True)
        time.sleep(0.35)

    save(ids)
    print(f"DONE total={len(ids)} file={OUT}", flush=True)


if __name__ == "__main__":
    main()
