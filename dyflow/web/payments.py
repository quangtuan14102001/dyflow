"""Nap crypto bang tay (khong dung cong ben thu 3).

Luong:
1. User chon goi -> tao payment pending.
2. User chuyen USDT (TRC20) toi vi cau hinh, roi gui ma giao dich (TX hash).
3. Admin duyet -> cong credit.

Bien moi truong (.env):
    CRYPTO_WALLET_ADDRESS   - dia chi nhan (vd USDT TRC20)
    CRYPTO_NETWORK          - nhan mang (mac dinh TRC20)
    CRYPTO_ASSET            - ten asset (mac dinh USDT)
    ADMIN_EMAIL             - email admin duoc phep duyet nap
"""
from __future__ import annotations

import os


def wallet_config() -> dict:
    address = (os.getenv("CRYPTO_WALLET_ADDRESS") or "").strip()
    return {
        "address": address,
        "network": (os.getenv("CRYPTO_NETWORK") or "TRC20").strip(),
        "asset": (os.getenv("CRYPTO_ASSET") or "USDT").strip(),
        "ready": bool(address),
    }


def is_configured() -> bool:
    return wallet_config()["ready"]
