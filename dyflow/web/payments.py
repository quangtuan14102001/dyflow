"""Cong thanh toan crypto tu dong: NOWPayments.

Luong:
1. User chon goi -> create_invoice() goi API NOWPayments -> tra ve invoice_url.
2. User thanh toan tren trang NOWPayments.
3. NOWPayments goi IPN webhook -> verify_ipn_signature() -> cong credit.

Bien moi truong (.env):
    NOWPAYMENTS_API_KEY     - API key tu dashboard NOWPayments
    NOWPAYMENTS_IPN_SECRET  - IPN secret de verify webhook
    PUBLIC_BASE_URL         - URL cong khai cua server (cho callback/redirect)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request

from ..utils import log

API_BASE = "https://api.nowpayments.io/v1"


def is_configured() -> bool:
    return bool(os.getenv("NOWPAYMENTS_API_KEY"))


def _public_base() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8080").rstrip("/")


def create_invoice(payment_id: str, amount_usd: float, description: str) -> dict:
    """Tao invoice NOWPayments. Tra ve dict co 'invoice_url' + 'id'.

    Neu chua cau hinh API key -> raise (app se bao loi ro rang)."""
    api_key = os.getenv("NOWPAYMENTS_API_KEY")
    if not api_key:
        raise RuntimeError("Chua cau hinh NOWPAYMENTS_API_KEY trong .env")

    base = _public_base()
    payload = {
        "price_amount": round(float(amount_usd), 2),
        "price_currency": "usd",
        "order_id": payment_id,
        "order_description": description,
        "ipn_callback_url": f"{base}/api/payments/webhook",
        "success_url": f"{base}/billing?paid=1",
        "cancel_url": f"{base}/billing?cancel=1",
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API_BASE}/invoice",
        data=data,
        headers={
            "x-api-key": api_key.strip(),
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare chan urllib mac dinh (403 error 1010) neu khong co User-Agent
            "User-Agent": "dyflow/1.0 (+https://github.com/dyflow)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        log.error("NOWPayments loi %s: %s", e.code, detail)
        msg = f"NOWPayments tu choi (HTTP {e.code})"
        try:
            err = json.loads(detail)
            if err.get("message"):
                msg = f"NOWPayments: {err['message']}"
            elif err.get("code"):
                msg = f"NOWPayments: {err['code']}"
        except Exception:  # noqa: BLE001
            if "1010" in detail:
                msg = "NOWPayments bi Cloudflare chan (thu lai sau khi cap nhat app)"
        raise RuntimeError(msg) from e

    log.info("Tao invoice NOWPayments cho %s: %s", payment_id, body.get("id"))
    return body


def verify_ipn_signature(raw_body: bytes, signature: str) -> bool:
    """Xac minh chu ky IPN: HMAC-SHA512 tren JSON da sort key."""
    secret = os.getenv("NOWPAYMENTS_IPN_SECRET")
    if not secret or not signature:
        return False
    try:
        parsed = json.loads(raw_body.decode())
    except Exception:  # noqa: BLE001
        return False
    sorted_json = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(secret.encode(), sorted_json.encode(), hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


# Trang thai NOWPayments coi la da tra tien
PAID_STATUSES = {"finished", "confirmed", "sending", "partially_paid"}
