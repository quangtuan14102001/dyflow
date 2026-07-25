"""Bot Telegram: CHI xac minh trial channel (khong render video).

Chay:
  export TELEGRAM_BOT_TOKEN="..."
  python -m dyflow.telegram_bot

Bot PHAI duoc them lam admin cua channel (TELEGRAM_CHANNEL / @dyflow_app)
de getChatMember hoat dong.

Tao video: dung web dyflow.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # noqa: BLE001
    pass

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .utils import log, setup_logging
from .web import billing, models, telegram_api


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    args = context.args or []
    if args:
        await _handle_verify(update, context, args[0].strip())
        return

    channel = telegram_api.channel_username()
    base = (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8080").rstrip("/")
    await update.message.reply_text(
        "dyflow bot — chi dung de xac minh trial.\n\n"
        f"1) Join channel {channel}\n"
        "2) Tren web bam «Xac minh Telegram»\n"
        f"3) Tao video tai: {base}\n\n"
        "Bot nay khong nhan link Douyin / khong render video."
    )


async def _handle_verify(
    update: Update, context: ContextTypes.DEFAULT_TYPE, token: str,
) -> None:
    assert update.message and update.effective_user
    tg_id = update.effective_user.id
    channel = telegram_api.channel_username()
    join_url = telegram_api.channel_link()

    member = await asyncio.to_thread(telegram_api.is_channel_member, tg_id)
    if not member:
        bot_ok = await asyncio.to_thread(
            telegram_api.is_channel_member, context.bot.id,
        )
        if not bot_ok:
            await update.message.reply_text(
                "Bot chua duoc them lam ADMIN cua channel "
                f"{channel}.\n\n"
                "Ban (chu channel) can:\n"
                f"1) Mo {join_url}\n"
                "2) Manage channel → Administrators → Add admin\n"
                f"3) Them @{context.bot.username} lam admin\n"
                "4) Quay lai web bam «Xac minh Telegram» lai."
            )
            return
        await update.message.reply_text(
            f"Ban chua join channel {channel}.\n"
            f"Hay join truoc: {join_url}\n"
            "Roi bam lai link xac minh tren web."
        )
        return

    await asyncio.to_thread(models.init_db)
    result = await asyncio.to_thread(models.activate_trial_from_token, token, tg_id)
    if not result.get("ok"):
        err = result.get("error")
        if err == "invalid_token":
            msg = "Link xac minh het han hoac da dung. Quay lai web de tao link moi."
        elif err == "telegram_taken":
            msg = "Tai khoan Telegram nay da lien ket email khac."
        else:
            msg = f"Xac minh that bai ({err})."
        await update.message.reply_text(msg)
        return

    days = result.get("days", billing.TRIAL_DAYS)
    per = result.get("videos_per_day", billing.TRIAL_VIDEOS_PER_DAY)
    base = (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8080").rstrip("/")
    await update.message.reply_text(
        f"OK! Da kich hoat trial cho {result.get('email')}.\n"
        f"• {days} ngay · {per} video mien phi / ngay\n"
        f"• Tao video tren web: {base}"
    )
    log.info("Trial OK tg=%s email=%s", tg_id, result.get("email"))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    base = (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8080").rstrip("/")
    await update.message.reply_text(
        "Bot nay khong render video.\n"
        f"Hay dung web: {base}"
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Thieu TELEGRAM_BOT_TOKEN.\n"
            "  export TELEGRAM_BOT_TOKEN='...'\n"
            "  python -m dyflow.telegram_bot"
        )

    setup_logging(verbose=False)
    models.init_db()
    log.info(
        "Bot verify-only | channel=%s trial=%sdays/%svideo",
        telegram_api.channel_username(),
        billing.TRIAL_DAYS,
        billing.TRIAL_VIDEOS_PER_DAY,
    )
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot Telegram (verify-only) dang chay...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
