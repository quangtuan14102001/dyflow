"""Gui thong bao Telegram cho admin (HTTP Bot API, khong can chay bot polling).

Bien moi truong:
    TELEGRAM_BOT_TOKEN       - token @BotFather
    TELEGRAM_ADMIN_CHAT_ID   - chat id admin (so, vd 123456789)
    PUBLIC_BASE_URL          - de tao link /admin/payments
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..utils import log


def is_configured() -> bool:
    return bool(
        (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        and (os.getenv("TELEGRAM_ADMIN_CHAT_ID") or "").strip()
    )


def _public_base() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8080").rstrip("/")


def _tronscan_url(tx_hash: str) -> str:
    h = (tx_hash or "").strip()
    if not h:
        return ""
    return f"https://tronscan.org/#/transaction/{h}"


def notify_topup_result(
    pay: dict,
    *,
    user_email: str = "",
    auto_credited: bool,
    verify_msg: str = "",
) -> bool:
    """Bao admin ket qua verify auto (thanh cong / cho duyet tay)."""
    if not is_configured():
        return False
    if auto_credited:
        title = "✅ <b>Nap credit THANH CONG (tu dong)</b>"
    else:
        title = "⏳ <b>Nap credit can admin duyet</b>"
    return _send_topup_message(pay, user_email=user_email, title=title, extra=verify_msg)


def notify_topup_submitted(pay: dict, user_email: str = "") -> bool:
    """Bao admin: user da gui TX hash (fallback cu)."""
    return notify_topup_result(
        pay, user_email=user_email, auto_credited=False, verify_msg="Cho kiem tra",
    )


def _send_topup_message(
    pay: dict,
    *,
    user_email: str = "",
    title: str,
    extra: str = "",
) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("Telegram notify tat (thieu TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID)")
        return False

    tx = (pay.get("tx_hash") or "").strip()
    admin_url = f"{_public_base()}/admin/payments"
    explorer = _tronscan_url(tx)

    lines = [
        title,
        f"User: <code>{user_email or pay.get('user_id')}</code>",
        f"Goi: {pay.get('package_id')} · +{pay.get('credits')} credit",
        f"So tien: <b>${pay.get('amount_usd')}</b> USDT",
        f"Ma don: <code>{pay.get('id')}</code>",
    ]
    if tx:
        lines.append(f"TX: <code>{tx}</code>")
    if pay.get("note"):
        lines.append(f"Note: {pay['note']}")
    if extra:
        lines.append(f"Chi tiet: {extra}")
    lines.append("")
    lines.append(f'<a href="{admin_url}">Mo trang duyet</a>')
    if explorer:
        lines.append(f'<a href="{explorer}">Xem tren Tronscan</a>')

    text = "\n".join(lines)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    rows = [[{"text": "Trang admin", "url": admin_url}]]
    if explorer:
        rows.append([{"text": "Tronscan", "url": explorer}])
    payload["reply_markup"] = {"inline_keyboard": rows}

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
        if not body.get("ok"):
            log.warning("Telegram notify that bai: %s", body)
            return False
        log.info("Da gui Telegram notify cho admin (payment %s)", pay.get("id"))
        return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        log.warning("Telegram notify HTTP %s: %s", e.code, detail[:500])
        return False
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram notify loi: %s", e)
        return False


def notify_password_reset(email: str, reset_url: str) -> bool:
    """Gui link reset cho admin Telegram (huu ich khi chua co SMTP)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    text = (
        "🔑 <b>Yeu cau dat lai mat khau</b>\n"
        f"Email: <code>{email}</code>\n"
        f'<a href="{reset_url}">Mo link reset</a>'
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [[{"text": "Dat lai mat khau", "url": reset_url}]]
        },
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
        return bool(body.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram reset notify loi: %s", e)
        return False
