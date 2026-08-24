"""Cau hinh gia + goi nap credit.

Gia theo thoi luong:
- 1 credit = toi da 2 phut (lam tron len theo block).
- Clip dai toi da 30 phut = 15 credit.
- Job that bai se duoc hoan credit.
- Trial Telegram free: chi clip <= 1 credit (2 phut).
"""
from __future__ import annotations

import math

# Alias / mac dinh khi khong probe duoc do dai
CREDITS_PER_VIDEO = 1
BLOCK_SEC = 120
CREDITS_PER_BLOCK = 1
MAX_DURATION_SEC = 30 * 60
MAX_CREDITS_PER_VIDEO = MAX_DURATION_SEC // BLOCK_SEC  # 15

# Goi nap — Trial $5 dung de keo user
PACKAGES: dict[str, dict] = {
    "trial": {
        "credits": 500,
        "price_usd": 5.0,
        "label": "Trial",
        "blurb": "",
        "highlight": True,
    },
    "pro": {
        "credits": 50,
        "price_usd": 20.0,
        "label": "Pro",
        "blurb": "",
        "highlight": False,
    },
    "max": {
        "credits": 100,
        "price_usd": 35.0,
        "label": "Max",
        "blurb": "",
        "highlight": False,
    },
}

SIGNUP_BONUS_CREDITS = 2

# Trial Telegram (mien phi join channel) — khac voi goi nap Trial $5
TRIAL_DAYS = 15
TRIAL_VIDEOS_PER_DAY = 5
DEFAULT_TELEGRAM_CHANNEL = "@dyflow_app"


def credits_for_duration(seconds: float | None) -> int:
    """So credit can theo do dai (ceil theo block 2 phut, cap 15)."""
    if seconds is None or seconds <= 0:
        return CREDITS_PER_BLOCK
    capped = min(float(seconds), float(MAX_DURATION_SEC))
    blocks = int(math.ceil(capped / BLOCK_SEC))
    return max(CREDITS_PER_BLOCK, min(MAX_CREDITS_PER_VIDEO, blocks))


def seconds_for_credits(credits: int) -> int:
    """Thoi luong xu ly toi da tuong ung so credit da tru.

    credits <= 0 (trial free): 2 phut.
    credits > 0: credits * 2 phut, toi da 30 phut.
    """
    c = max(0, int(credits))
    if c <= 0:
        return BLOCK_SEC
    return min(c * BLOCK_SEC, MAX_DURATION_SEC)


def format_duration_label(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    sec = int(round(float(seconds)))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def get_package(pkg_id: str) -> dict | None:
    pkg = PACKAGES.get(pkg_id)
    if not pkg:
        return None
    return {"id": pkg_id, **pkg}


def list_packages() -> list[dict]:
    return [{"id": k, **v} for k, v in PACKAGES.items()]
