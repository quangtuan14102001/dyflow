"""Bot Telegram:
- User: xac minh trial channel (khong render video)
- Super admin (TELEGRAM_SUPER_ADMIN_ID): menu + lenh quan ly
- Admin web (user.is_admin lien ket telegram): mo web admin
- Thong bao nap tien: TELEGRAM_ADMIN_CHAT_ID (notify.py)

Chay:
  export TELEGRAM_BOT_TOKEN="..."
  python -m dyflow.telegram_bot
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # noqa: BLE001
    pass

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from .brand import BRAND_NAME
from .utils import log, setup_logging
from .web import billing, models, telegram_api

# Nut ReplyKeyboard (hien o duoi chat)
BTN_DASH = "📊 Dashboard"
BTN_ADMINS = "📋 Admins"
BTN_CREDIT = "💳 Cộng / trừ credit"
BTN_ADD_ADMIN = "👤 Thêm admin"
BTN_REMOVE_ADMIN = "🚫 Bỏ admin"
BTN_WEB_ADMIN = "🌐 Link web"
BTN_PAYMENTS = "💰 Payments"
BTN_JOBS = "🎬 Jobs"
BTN_USERS = "👥 Users"
BTN_MYID = "🆔 My ID"
BTN_HELP = "❓ Help admin"
BTN_HIDE = "❌ Ẩn menu"

_ADMIN_BUTTONS = {
    BTN_DASH, BTN_ADMINS, BTN_CREDIT, BTN_ADD_ADMIN, BTN_REMOVE_ADMIN,
    BTN_WEB_ADMIN, BTN_PAYMENTS, BTN_JOBS, BTN_USERS, BTN_MYID, BTN_HELP, BTN_HIDE,
}

# user_data keys cho conversation ngan
STATE_CREDIT_EMAIL = "await_credit_email"
STATE_CREDIT_DELTA = "await_credit_delta"
STATE_ADD_ADMIN = "await_add_admin"
STATE_REMOVE_ADMIN = "await_remove_admin"


def _public_base() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8080").rstrip("/")


def _super_admin_id() -> int | None:
    raw = (os.getenv("TELEGRAM_SUPER_ADMIN_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_super_tg(user_id: int | None) -> bool:
    sid = _super_admin_id()
    return sid is not None and user_id is not None and int(user_id) == sid


def _staff_user(user_id: int | None) -> dict | None:
    """User web co quyen admin/super va da lien ket Telegram."""
    if user_id is None:
        return None
    if _is_super_tg(user_id):
        # Dam bao co record neu da sync
        u = models.get_user_by_telegram_id(user_id)
        return u or {"is_admin": 1, "is_super_admin": 1, "email": "(super)"}
    u = models.get_user_by_telegram_id(user_id)
    if u and models.is_owner_user(u):
        return u
    return None


def _is_staff_tg(user_id: int | None) -> bool:
    return _staff_user(user_id) is not None


def _admin_keyboard(*, super_only: bool) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(BTN_DASH), KeyboardButton(BTN_USERS)],
        [KeyboardButton(BTN_JOBS), KeyboardButton(BTN_PAYMENTS)],
        [KeyboardButton(BTN_WEB_ADMIN), KeyboardButton(BTN_HELP)],
        [KeyboardButton(BTN_MYID), KeyboardButton(BTN_HIDE)],
    ]
    if super_only:
        rows = [
            [KeyboardButton(BTN_ADMINS), KeyboardButton(BTN_CREDIT)],
            [KeyboardButton(BTN_ADD_ADMIN), KeyboardButton(BTN_REMOVE_ADMIN)],
            *rows,
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def _fmt_ts(ts: Any) -> str:
    try:
        import time as _time
        return _time.strftime("%d/%m %H:%M", _time.localtime(float(ts)))
    except Exception:  # noqa: BLE001
        return "?"


def _clip(s: str, n: int = 40) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def _reply_chunks(update: Update, text: str, **kwargs: Any) -> None:
    """Telegram gioi han ~4096 ky tu / tin."""
    assert update.message
    limit = 3900
    if len(text) <= limit:
        await update.message.reply_text(text, **kwargs)
        return
    parts: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    for i, part in enumerate(parts):
        await update.message.reply_text(part, **(kwargs if i == 0 else {}))


async def show_dashboard(update: Update) -> None:
    stats = await asyncio.to_thread(models.admin_stats)
    await update.message.reply_text(
        "📊 Dashboard\n"
        f"• Users: {stats['users']}\n"
        f"• Jobs queued: {stats['jobs_queued']}\n"
        f"• Jobs running: {stats['jobs_running']}\n"
        f"• Jobs failed: {stats['jobs_failed']}\n"
        f"• Payments pending: {stats['payments_pending']}"
    )


async def show_users(update: Update) -> None:
    users = await asyncio.to_thread(models.list_users, 25, 0, "")
    total = await asyncio.to_thread(models.count_users, "")
    lines = [f"👥 Users (hien {len(users)}/{total})"]
    for u in users:
        flags = []
        if u.get("is_super_admin"):
            flags.append("super")
        elif u.get("is_admin"):
            flags.append("admin")
        if u.get("blocked"):
            flags.append("BLOCK")
        tag = f" [{','.join(flags)}]" if flags else ""
        lines.append(
            f"• {u['email']}{tag}\n"
            f"  {int(u.get('credits') or 0)} cr · {_fmt_ts(u.get('created_at'))}"
        )
    if not users:
        lines.append("(trong)")
    await _reply_chunks(update, "\n".join(lines))


async def show_jobs(update: Update) -> None:
    stats = await asyncio.to_thread(models.admin_stats)
    jobs = await asyncio.to_thread(models.list_jobs_admin, 20, 0, None)
    lines = [
        "🎬 Jobs gan day\n"
        f"queued={stats['jobs_queued']} · running={stats['jobs_running']} · "
        f"failed={stats['jobs_failed']}\n"
    ]
    for j in jobs:
        st = j.get("status") or "?"
        email = j.get("user_email") or "?"
        prog = int(j.get("progress") or 0)
        stage = j.get("stage") or ""
        url = _clip(j.get("source_url") or "", 36)
        msg = _clip(j.get("message") or j.get("error") or "", 50)
        lines.append(
            f"• [{st}] {prog}% {stage}\n"
            f"  {email}\n"
            f"  {url}\n"
            f"  {msg}"
        )
    if not jobs:
        lines.append("(chua co job)")
    await _reply_chunks(update, "\n".join(lines))


async def show_payments(update: Update) -> None:
    pending = await asyncio.to_thread(models.list_pending_payments, 15)
    recent = await asyncio.to_thread(models.list_payments_admin, 15, 0)
    lines = [f"💰 Payments pending: {len(pending)}"]
    if pending:
        for p in pending:
            email = p.get("user_email") or "?"
            lines.append(
                f"• [{p.get('status')}] ${p.get('amount_usd')} → {p.get('credits')} cr\n"
                f"  {email} · {_fmt_ts(p.get('created_at'))}\n"
                f"  id={p.get('id')}"
            )
    else:
        lines.append("(khong co payment cho duyet)")

    lines.append("\n— Gan day —")
    for p in recent[:10]:
        email = p.get("user_email") or "?"
        lines.append(
            f"• [{p.get('status')}] ${p.get('amount_usd')} "
            f"{'✓' if p.get('credited') else ''} · {email}"
        )

    # Inline approve/reject cho pending submitted
    rows = []
    for p in pending:
        if p.get("status") != models.PAY_SUBMITTED:
            continue
        pid = p["id"]
        short = pid[-8:]
        rows.append([
            InlineKeyboardButton(
                f"✅ Duyet …{short}", callback_data=f"pay_ok:{pid}",
            ),
            InlineKeyboardButton(
                f"❌ Tu choi …{short}", callback_data=f"pay_no:{pid}",
            ),
        ])
    kb = InlineKeyboardMarkup(rows[:8]) if rows else None
    await _reply_chunks(update, "\n".join(lines), reply_markup=kb)

def _clear_admin_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in (
        STATE_CREDIT_EMAIL, STATE_CREDIT_DELTA, STATE_ADD_ADMIN, STATE_REMOVE_ADMIN,
        "credit_email",
    ):
        context.user_data.pop(k, None)


async def _post_init(app: Application) -> None:
    """Menu 3 gach (Bot Menu) — user thuong vs super admin."""
    base_cmds = [
        BotCommand("start", "Bat dau / hien menu"),
        BotCommand("menu", "Mo lai menu admin"),
        BotCommand("myid", "Xem Telegram ID"),
    ]
    admin_cmds = base_cmds + [
        BotCommand("admins", "Danh sach admin"),
        BotCommand("admin_add", "Them admin (email)"),
        BotCommand("admin_remove", "Bo admin (email)"),
        BotCommand("credit", "Cong/tru credit"),
        BotCommand("license", "Mint license desktop"),
        BotCommand("webadmin", "Link web admin"),
    ]
    try:
        await app.bot.set_my_commands(base_cmds, scope=BotCommandScopeDefault())
        sid = _super_admin_id()
        if sid:
            await app.bot.set_my_commands(
                admin_cmds, scope=BotCommandScopeChat(chat_id=sid),
            )
        log.info("Da set Bot Menu commands (Menu 3 gach)")
    except Exception as e:  # noqa: BLE001
        log.warning("set_my_commands fail: %s", e)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    args = context.args or []
    if args:
        await _handle_verify(update, context, args[0].strip())
        return

    channel = telegram_api.channel_username()
    base = _public_base()
    uid = update.effective_user.id
    msg = (
        f"{BRAND_NAME} bot\n\n"
        f"1) Join channel {channel}\n"
        "2) Tren web bam «Xac minh Telegram»\n"
        f"3) Tao video: {base}\n\n"
        "Bot khong nhan link Douyin / khong render.\n"
        f"Telegram ID cua ban: {uid}"
    )

    staff = await asyncio.to_thread(_staff_user, uid)
    if staff:
        if _is_super_tg(uid):
            await asyncio.to_thread(models.sync_super_admin_telegram, uid)
            msg += (
                "\n\n🛠 Super admin — dung nut ben duoi hoac Menu (☰).\n"
                "Web admin: " + f"{base}/admin"
            )
            kb = _admin_keyboard(super_only=True)
        else:
            msg += (
                "\n\n🛠 Admin — dung nut ben duoi hoac Menu (☰).\n"
                "Web admin: " + f"{base}/admin"
            )
            kb = _admin_keyboard(super_only=False)
        await update.message.reply_text(msg, reply_markup=kb)
        return

    await update.message.reply_text(msg)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    staff = await asyncio.to_thread(_staff_user, uid)
    if not staff:
        await update.message.reply_text("Chi admin moi co menu quan ly.")
        return
    _clear_admin_state(context)
    kb = _admin_keyboard(super_only=_is_super_tg(uid))
    await update.message.reply_text(
        "Menu admin da mo. Bam nut ben duoi:",
        reply_markup=kb,
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    await update.message.reply_text(
        f"Telegram ID cua ban: {update.effective_user.id}\n"
        "Dien vao TELEGRAM_SUPER_ADMIN_ID trong .env neu ban la chu."
    )


async def cmd_webadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await asyncio.to_thread(_is_staff_tg, update.effective_user.id):
        await update.message.reply_text("Chi admin duoc dung lenh nay.")
        return
    base = _public_base()
    await update.message.reply_text(
        f"Web admin:\n"
        f"• Dashboard: {base}/admin\n"
        f"• Users: {base}/admin/users\n"
        f"• Jobs: {base}/admin/jobs\n"
        f"• Payments: {base}/admin/payments"
    )


async def _require_super(update: Update) -> bool:
    if not update.message or not update.effective_user:
        return False
    if _is_super_tg(update.effective_user.id):
        return True
    await update.message.reply_text("Chi super admin duoc dung lenh nay.")
    return False


async def cmd_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_super(update):
        return
    users = await asyncio.to_thread(models.list_users, 200, 0, "")
    lines = []
    for u in users:
        if u.get("is_admin") or u.get("is_super_admin"):
            role = "super" if u.get("is_super_admin") else "admin"
            lines.append(f"• {u['email']} ({role}) · {u['credits']} cr")
    await update.message.reply_text(
        "Admins:\n" + ("\n".join(lines) if lines else "(trong)")
    )


async def cmd_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_super(update):
        return
    email = " ".join(context.args or []).strip().lower()
    if "@" not in email:
        _clear_admin_state(context)
        context.user_data[STATE_ADD_ADMIN] = True
        await update.message.reply_text(
            "Gui email can set admin:\nvd: user@gmail.com\n"
            "Hoac /admin_add email@domain.com"
        )
        return
    user = await asyncio.to_thread(models.set_admin_by_email, email, True)
    if not user:
        await update.message.reply_text("Khong tim thay email / khong set duoc.")
        return
    await update.message.reply_text(f"OK: {user['email']} -> admin")


async def cmd_admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_super(update):
        return
    email = " ".join(context.args or []).strip().lower()
    if "@" not in email:
        _clear_admin_state(context)
        context.user_data[STATE_REMOVE_ADMIN] = True
        await update.message.reply_text(
            "Gui email can bo admin:\nvd: user@gmail.com\n"
            "Hoac /admin_remove email@domain.com"
        )
        return
    await _do_remove_admin(update, email)


async def _do_remove_admin(update: Update, email: str) -> None:
    target = await asyncio.to_thread(models.get_user_by_email, email)
    if not target:
        await update.message.reply_text("Khong tim thay email.")
        return
    if target.get("is_super_admin"):
        await update.message.reply_text("Khong the go super admin.")
        return
    ok = await asyncio.to_thread(models.set_user_admin, target["id"], False)
    await update.message.reply_text(
        f"OK: {email} bo admin" if ok else "That bai."
    )


async def cmd_credit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_super(update):
        return
    args = context.args or []
    if len(args) < 2:
        _clear_admin_state(context)
        context.user_data[STATE_CREDIT_EMAIL] = True
        await update.message.reply_text(
            "Gui email user:\nvd: user@gmail.com\n"
            "Sau do gui so credit: +50 hoac -10\n"
            "Hoac /credit email@x.com +50"
        )
        return
    email = args[0].strip().lower()
    raw = args[1].strip().replace(",", "")
    await _do_credit(update, email, raw)


async def cmd_license(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_super(update):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Uu tien tao key trong app (Admin / Keys). Telegram chi du phong:\n"
            "/license 5d user MA-MAY\n"
            "/license 1m user MA-MAY\n"
            "/license 3m admin MA-MAY\n"
            "/license renew KEY 1m\n"
            "Goi: 5d (lan 1) | 1m | 3m | 6m | 1y"
        )
        return
    try:
        from .license_key import mint, normalize_role, parse_duration, renew

        if args[0].lower() == "renew":
            if len(args) < 3:
                await update.message.reply_text("Dung: /license renew KEY 1m [MA-MAY]")
                return
            days = parse_duration(args[2])
            machine = args[3] if len(args) > 3 else None
            key, info = renew(args[1], days, machine=machine)
        else:
            days = parse_duration(args[0])
            role = "user"
            machine = None
            rest = args[1:]
            if rest and rest[0].lower() in ("user", "admin", "super"):
                role = normalize_role(rest[0])
                rest = rest[1:]
            if rest:
                machine = rest[0]
            if not machine:
                await update.message.reply_text(
                    "Thieu ma may. Khach copy ma may trong app roi:\n"
                    "/license 5d user MA-MAY"
                )
                return
            key, info = mint(days, role=role, machine=machine)
    except ValueError as e:
        await update.message.reply_text(f"Loi: {e}")
        return
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"Mint fail: {e}")
        return
    exp = time.strftime("%d/%m/%Y", time.localtime(float(info["expires_at"])))
    await update.message.reply_text(
        f"Key {info['role']} — {info['days']} ngay — han {exp}\n"
        f"May: {info.get('machine_code') or '-'}\n\n{key}\n\n"
        "Gui dung dong nay cho khach. Het han thi /license renew KEY 1m"
    )


async def _do_credit(update: Update, email: str, raw: str) -> None:
    m = re.fullmatch(r"[+-]?\d+", raw)
    if not m:
        await update.message.reply_text("So credit khong hop le (vd +50 hoac -10).")
        return
    delta = int(raw)
    user = await asyncio.to_thread(models.get_user_by_email, email)
    if not user:
        await update.message.reply_text("Khong tim thay email.")
        return
    updated = await asyncio.to_thread(
        models.admin_adjust_credits,
        user["id"], delta, f"tg-super:{update.effective_user.id}",
    )
    if not updated:
        await update.message.reply_text("Khong dieu chinh duoc (so du am?).")
        return
    await update.message.reply_text(
        f"OK: {email} {delta:+d} -> {updated['credits']} credit"
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

    if _is_super_tg(tg_id):
        await asyncio.to_thread(models.sync_super_admin_telegram, tg_id)

    days = result.get("days", billing.TRIAL_DAYS)
    per = result.get("videos_per_day", billing.TRIAL_VIDEOS_PER_DAY)
    base = _public_base()
    text = (
        f"OK! Da kich hoat trial cho {result.get('email')}.\n"
        f"• {days} ngay · {per} video mien phi / ngay\n"
        f"• Tao video tren web: {base}"
    )
    staff = await asyncio.to_thread(_staff_user, tg_id)
    if staff:
        kb = _admin_keyboard(super_only=_is_super_tg(tg_id))
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text)
    log.info("Trial OK tg=%s email=%s", tg_id, result.get("email"))


async def _handle_admin_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
) -> bool:
    """Xu ly nut ReplyKeyboard. Tra True neu da xu ly."""
    if text not in _ADMIN_BUTTONS:
        return False
    assert update.message and update.effective_user
    uid = update.effective_user.id
    staff = await asyncio.to_thread(_staff_user, uid)
    if not staff:
        await update.message.reply_text("Chi admin duoc dung menu nay.")
        return True

    base = _public_base()
    _clear_admin_state(context)

    if text == BTN_HIDE:
        await update.message.reply_text(
            "Da an menu. Go /menu de mo lai.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return True
    if text == BTN_MYID:
        await cmd_myid(update, context)
        return True
    if text == BTN_HELP:
        help_msg = (
            "Menu admin (hien ngay trong chat):\n"
            f"• {BTN_DASH} — thong ke\n"
            f"• {BTN_USERS} / {BTN_JOBS} / {BTN_PAYMENTS} — danh sach\n"
            f"• {BTN_WEB_ADMIN} — link web (tuy chon)\n"
            f"• {BTN_MYID} — Telegram ID\n"
        )
        if _is_super_tg(uid):
            help_msg += (
                f"• {BTN_ADMINS} — danh sach admin\n"
                f"• {BTN_CREDIT} — cong/tru credit\n"
                f"• {BTN_ADD_ADMIN} / {BTN_REMOVE_ADMIN}\n"
            )
        help_msg += "Menu ☰ tren o chat cung co cac lenh."
        await update.message.reply_text(help_msg)
        return True
    if text == BTN_DASH:
        await show_dashboard(update)
        return True
    if text == BTN_WEB_ADMIN:
        await update.message.reply_text(
            f"Link web (tuy chon):\n"
            f"• {base}/admin\n"
            f"• {base}/admin/users\n"
            f"• {base}/admin/jobs\n"
            f"• {base}/admin/payments"
        )
        return True
    if text == BTN_PAYMENTS:
        await show_payments(update)
        return True
    if text == BTN_JOBS:
        await show_jobs(update)
        return True
    if text == BTN_USERS:
        await show_users(update)
        return True

    # Super-only buttons
    if not _is_super_tg(uid):
        await update.message.reply_text("Chi super admin dung nut nay.")
        return True
    if text == BTN_ADMINS:
        await cmd_admins(update, context)
        return True
    if text == BTN_CREDIT:
        context.user_data[STATE_CREDIT_EMAIL] = True
        await update.message.reply_text("Gui email user can chinh credit:")
        return True
    if text == BTN_ADD_ADMIN:
        context.user_data[STATE_ADD_ADMIN] = True
        await update.message.reply_text("Gui email can set admin:")
        return True
    if text == BTN_REMOVE_ADMIN:
        context.user_data[STATE_REMOVE_ADMIN] = True
        await update.message.reply_text("Gui email can bo admin:")
        return True
    return True


async def on_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    await query.answer()
    uid = update.effective_user.id
    if not await asyncio.to_thread(_is_staff_tg, uid):
        await query.edit_message_text("Chi admin duoc duyet payment.")
        return
    data = query.data
    if data.startswith("pay_ok:"):
        pid = data.split(":", 1)[1]
        ok = await asyncio.to_thread(models.credit_topup, pid)
        if ok:
            pay = await asyncio.to_thread(models.get_payment, pid)
            email = "?"
            if pay:
                u = await asyncio.to_thread(models.get_user, pay["user_id"])
                email = (u or {}).get("email") or "?"
            await query.edit_message_text(
                f"✅ Da duyet payment {pid[-8:]}\n"
                f"User: {email}\n"
                f"+{pay.get('credits') if pay else '?'} credit"
            )
        else:
            await query.edit_message_text(
                f"Khong duyet duoc {pid[-8:]} (da xu ly / khong hop le)."
            )
        return
    if data.startswith("pay_no:"):
        pid = data.split(":", 1)[1]
        await asyncio.to_thread(models.reject_payment, pid)
        await query.edit_message_text(f"❌ Da tu choi payment {pid[-8:]}")
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    uid = update.effective_user.id if update.effective_user else None

    # Nut menu
    if await _handle_admin_button(update, context, text):
        return

    # Conversation states (super admin)
    if uid and _is_super_tg(uid):
        if context.user_data.get(STATE_CREDIT_EMAIL):
            email = text.lower()
            if "@" not in email:
                await update.message.reply_text("Email khong hop le. Gui lai:")
                return
            context.user_data.pop(STATE_CREDIT_EMAIL, None)
            context.user_data["credit_email"] = email
            context.user_data[STATE_CREDIT_DELTA] = True
            await update.message.reply_text(
                f"Email: {email}\nGui so credit (vd +50 hoac -10):"
            )
            return
        if context.user_data.get(STATE_CREDIT_DELTA):
            email = context.user_data.pop("credit_email", "")
            context.user_data.pop(STATE_CREDIT_DELTA, None)
            if not email:
                await update.message.reply_text("Het phien. Bam lai «Cong / tru credit».")
                return
            await _do_credit(update, email, text.replace(",", "").strip())
            return
        if context.user_data.get(STATE_ADD_ADMIN):
            context.user_data.pop(STATE_ADD_ADMIN, None)
            email = text.lower()
            if "@" not in email:
                await update.message.reply_text("Email khong hop le.")
                return
            user = await asyncio.to_thread(models.set_admin_by_email, email, True)
            if not user:
                await update.message.reply_text("Khong tim thay email / khong set duoc.")
            else:
                await update.message.reply_text(f"OK: {user['email']} -> admin")
            return
        if context.user_data.get(STATE_REMOVE_ADMIN):
            context.user_data.pop(STATE_REMOVE_ADMIN, None)
            email = text.lower()
            if "@" not in email:
                await update.message.reply_text("Email khong hop le.")
                return
            await _do_remove_admin(update, email)
            return

        await update.message.reply_text(
            "Super admin: bam nut menu ben duoi, hoac /menu /admins /credit …"
        )
        return

    if uid and await asyncio.to_thread(_is_staff_tg, uid):
        await update.message.reply_text(
            "Admin: bam nut menu ben duoi, hoac /menu /webadmin"
        )
        return

    base = _public_base()
    await update.message.reply_text(
        f"Bot chi xac minh trial + thong bao admin.\nWeb: {base}"
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
    models.ensure_admin_email(os.getenv("ADMIN_EMAIL", ""))
    models.sync_super_admin_telegram(os.getenv("TELEGRAM_SUPER_ADMIN_ID", ""))
    sid = _super_admin_id()
    log.info(
        "Bot verify + admin menu | channel=%s super_tg=%s notify_chat=%s",
        telegram_api.channel_username(),
        sid or "(chua set TELEGRAM_SUPER_ADMIN_ID)",
        (os.getenv("TELEGRAM_ADMIN_CHAT_ID") or "").strip() or "(none)",
    )
    app = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("webadmin", cmd_webadmin))
    app.add_handler(CommandHandler("admins", cmd_admins))
    app.add_handler(CommandHandler("admin_add", cmd_admin_add))
    app.add_handler(CommandHandler("admin_remove", cmd_admin_remove))
    app.add_handler(CommandHandler("credit", cmd_credit))
    app.add_handler(CommandHandler("license", cmd_license))
    app.add_handler(CallbackQueryHandler(on_payment_callback, pattern=r"^pay_(ok|no):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot Telegram dang chay...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
