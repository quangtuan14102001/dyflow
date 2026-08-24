"""Gui email dat lai mat khau (SMTP tuy chon)."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from ..brand import BRAND_NAME
from ..utils import log


def smtp_configured() -> bool:
    return bool((os.getenv("SMTP_HOST") or "").strip() and (os.getenv("SMTP_FROM") or "").strip())


def send_reset_email(to_email: str, reset_url: str) -> bool:
    if not smtp_configured():
        return False
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT") or "587")
    user = (os.getenv("SMTP_USER") or "").strip()
    password = os.getenv("SMTP_PASSWORD") or ""
    mail_from = os.getenv("SMTP_FROM", "").strip()
    use_tls = (os.getenv("SMTP_TLS") or "1").strip() not in {"0", "false", "False"}

    msg = EmailMessage()
    msg["Subject"] = f"{BRAND_NAME} — Dat lai mat khau"
    msg["From"] = mail_from
    msg["To"] = to_email
    msg.set_content(
        f"Ban (hoac ai do) yeu cau dat lai mat khau {BRAND_NAME}.\n\n"
        f"Mo link sau (het han trong 60 phut):\n{reset_url}\n\n"
        "Neu khong phai ban, bo qua email nay.\n"
    )
    msg.add_alternative(
        f"<p>Ban yeu cau dat lai mat khau <b>{BRAND_NAME}</b>.</p>"
        f'<p><a href="{reset_url}">Dat lai mat khau</a></p>'
        f"<p>Link het han sau 60 phut. Neu khong phai ban, bo qua email nay.</p>",
        subtype="html",
    )
    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
        log.info("Da gui email reset toi %s", to_email)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Gui email reset that bai: %s", e)
        return False
