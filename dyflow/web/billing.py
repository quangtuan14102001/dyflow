"""Cau hinh gia + goi nap credit.

Quy uoc: 1 credit = 1 video (clip <= 2 phut nhu hien tai).
Job that bai se duoc hoan credit.
"""
from __future__ import annotations

# Chi phi credit cho moi video hoan tat
CREDITS_PER_VIDEO = 1

# Cac goi nap: id -> (so credit, gia USD, nhan hien thi)
# price_usd dung de hien thi so tien can chuyen (USDT).
PACKAGES: dict[str, dict] = {
    "starter": {"credits": 10, "price_usd": 5.0, "label": "Starter"},
    "pro": {"credits": 50, "price_usd": 20.0, "label": "Pro"},
    "max": {"credits": 100, "price_usd": 35.0, "label": "Max"},
}

# Credit tang khi dang ky (dung thu). 0 = tat.
SIGNUP_BONUS_CREDITS = 2

# Trial khi join channel Telegram (web). Free slot truoc, het slot moi tru credit.
TRIAL_DAYS = 15
TRIAL_VIDEOS_PER_DAY = 5
# Override bang TELEGRAM_CHANNEL trong .env (vd @dyflow_app)
DEFAULT_TELEGRAM_CHANNEL = "@dyflow_app"


def get_package(pkg_id: str) -> dict | None:
    pkg = PACKAGES.get(pkg_id)
    if not pkg:
        return None
    return {"id": pkg_id, **pkg}


def list_packages() -> list[dict]:
    return [{"id": k, **v} for k, v in PACKAGES.items()]
