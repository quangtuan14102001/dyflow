"""FastAPI app cho dyflow: auth + credit + nap crypto + job da luong.

Chay (worker chay kem trong tien trinh):
    uvicorn dyflow.web.app:app --port 8080

Tach worker rieng (VPS da luong): dat DYFLOW_WEB_NO_WORKER=1 cho web,
va chay `python -m dyflow.web.worker` o tien trinh khac.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:  # noqa: BLE001
    pass

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..pipeline import extract_douyin_urls, normalize_douyin_url
from ..utils import load_config, log, setup_logging
from ..voices import list_languages, resolve_preset
from . import auth, billing, i18n, mail, models, notify, payments, storage, telegram_api, verify_tron, worker
from .progress import STAGES, stage_index, stage_label

_MAX_BATCH_URLS = 20
_CFG_CACHE: dict | None = None


def _public_base() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8080").rstrip("/")


def _web_cfg() -> dict:
    global _CFG_CACHE
    if _CFG_CACHE is None:
        try:
            _CFG_CACHE = load_config(os.getenv("DYFLOW_CONFIG", "config.yaml"))
        except Exception:  # noqa: BLE001
            _CFG_CACHE = {}
    return _CFG_CACHE


BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="dyflow web")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_ALLOWED_HOSTS = {"douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com"}


def _page(request: Request, name: str, **ctx):
    """TemplateResponse kem i18n + user (neu chua truyen)."""
    data = i18n.template_globals(request)
    if "user" not in ctx:
        data["user"] = auth.current_user(request)
    data.update(ctx)
    return templates.TemplateResponse(request, name, data)


def _validate_douyin(url: str) -> str:
    normalized = normalize_douyin_url(url)
    host = (urlparse(normalized).hostname or "").lower()
    if host not in _ALLOWED_HOSTS and not host.endswith(".douyin.com"):
        raise ValueError(f"Hostname khong hop le: {host}")
    return normalized


def _require_user(request: Request) -> dict:
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Chua dang nhap")
    return user


@app.on_event("startup")
def _startup() -> None:
    setup_logging(False)
    models.init_db()
    models.ensure_admin_email(os.getenv("ADMIN_EMAIL", ""))
    if os.getenv("DYFLOW_WEB_NO_WORKER") != "1":
        worker.start_background()
    log.info("dyflow web san sang tai http://localhost:8080")


def _require_admin(request: Request) -> dict:
    user = _require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(403, "Can quyen admin")
    return user


# ----------------------------- pages -----------------------------

def _trial_for_user(user: dict) -> dict:
    """Trial status; re-check channel membership neu dang trong cua so trial."""
    base = models.trial_status(user["id"], still_member=True)
    ends = base.get("trial_ends_at")
    tg = base.get("telegram_id") or user.get("telegram_id")
    still = True
    if tg and ends and float(ends) > time.time():
        still = telegram_api.is_channel_member(tg)
        if not still:
            base = models.trial_status(user["id"], still_member=False)
            ends = base.get("trial_ends_at")
    if ends:
        base["ends_label"] = time.strftime("%d/%m/%Y", time.localtime(float(ends)))
    else:
        base["ends_label"] = ""
    base["days"] = billing.TRIAL_DAYS
    base["videos_per_day"] = base.get("videos_per_day") or billing.TRIAL_VIDEOS_PER_DAY
    return base


@app.get("/", response_class=HTMLResponse)
def index(request: Request, page: int = 1):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    languages = list_languages()
    ui = i18n.get_ui_lang(request)
    lang_ids = {x["id"] for x in languages}
    default_dub = ui if ui in lang_ids else "vi"
    languages = sorted(
        languages,
        key=lambda x: (0 if x["id"] == default_dub else 1, x["label"]),
    )

    per_page = 8
    total = models.count_jobs(user_id=user["id"])
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), total_pages))
    offset = (page - 1) * per_page
    jobs = models.list_jobs(user_id=user["id"], limit=per_page, offset=offset)
    trial = _trial_for_user(user)

    return _page(
        request, "index.html",
        user=user,
        languages=languages,
        default_dub_lang=default_dub,
        jobs=jobs,
        credits_per_video=billing.CREDITS_PER_VIDEO,
        page=page,
        total_pages=total_pages,
        total_jobs=total,
        per_page=per_page,
        page_items=_page_items(page, total_pages),
        trial=trial,
        channel_link=telegram_api.channel_link(),
        channel_name=telegram_api.channel_username(),
    )


def _page_items(page: int, total: int, siblings: int = 1) -> list[int | None]:
    """Danh sach so trang + None = dau '...'. VD: [1, 2, 3, None, 12]."""
    if total <= 1:
        return [1] if total == 1 else []
    if total <= 7:
        return list(range(1, total + 1))

    pages: set[int] = {1, total, page}
    for i in range(page - siblings, page + siblings + 1):
        if 1 <= i <= total:
            pages.add(i)
    # neu gan dau/cuoi thi them them so de khong le loi
    if page <= 3:
        pages.update({2, 3, 4})
    if page >= total - 2:
        pages.update({total - 1, total - 2, total - 3})

    ordered = sorted(p for p in pages if 1 <= p <= total)
    out: list[int | None] = []
    prev = 0
    for n in ordered:
        if prev and n - prev > 1:
            out.append(None)
        out.append(n)
        prev = n
    return out


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=302)
    return _page(request, "login.html", mode="login", user=None)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=302)
    return _page(
        request, "login.html",
        mode="register", bonus=billing.SIGNUP_BONUS_CREDITS, user=None,
    )


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_page(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=302)
    return _page(request, "forgot.html", user=None)


@app.get("/reset-password", response_class=HTMLResponse)
def reset_page(request: Request, token: str = ""):
    token_ok = bool(token and models.get_password_reset(token))
    return _page(
        request, "reset.html",
        token=token, token_ok=token_ok, user=None,
    )


@app.get("/library", response_class=HTMLResponse)
def library_page(request: Request, page: int = 1):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    per_page = 12
    total = models.count_jobs(user_id=user["id"], status=models.STATUS_COMPLETED)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    page = max(1, min(int(page or 1), total_pages))
    offset = (page - 1) * per_page
    jobs = models.list_jobs(
        user_id=user["id"],
        limit=per_page,
        offset=offset,
        status=models.STATUS_COMPLETED,
    )

    from . import media as media_mod

    ui_lang = i18n.get_ui_lang(request)
    videos = []
    for j in jobs:
        item = media_mod.enrich_library_item(j)
        rk = item.get("rel_key") or ""
        if rk == "just_now":
            item["rel_label"] = i18n.t(ui_lang, "rel_just_now")
        elif rk.startswith("m:"):
            item["rel_label"] = i18n.t(ui_lang, "rel_minutes", n=rk.split(":")[1])
        elif rk.startswith("h:"):
            item["rel_label"] = i18n.t(ui_lang, "rel_hours", n=rk.split(":")[1])
        elif rk.startswith("d:"):
            item["rel_label"] = i18n.t(ui_lang, "rel_days", n=rk.split(":")[1])
        elif rk.startswith("mo:"):
            item["rel_label"] = i18n.t(ui_lang, "rel_months", n=rk.split(":")[1])
        else:
            item["rel_label"] = ""
        videos.append(item)

    return _page(
        request, "library.html",
        user=user,
        videos=videos,
        page=page,
        total_pages=total_pages,
        total_jobs=total,
        page_items=_page_items(page, total_pages) if total else [],
        retention_days=storage.retention_days(_web_cfg()),
    )


@app.get("/api/jobs/{job_id}/thumb")
def api_job_thumb(request: Request, job_id: str):
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    if job["status"] != models.STATUS_COMPLETED or not job.get("output_path"):
        raise HTTPException(404, "Chua co video")
    from . import media as media_mod

    thumb = media_mod.ensure_thumbnail(job_id, job["output_path"])
    if not thumb:
        raise HTTPException(404, "Khong tao duoc thumbnail")
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request, pay_page: int = 1, tx_page: int = 1):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    wallet = payments.wallet_config()

    pay_per = 8
    tx_per = 10
    pay_total = models.count_payments(user["id"])
    tx_total = models.count_transactions(user["id"])
    pay_pages = max(1, (pay_total + pay_per - 1) // pay_per) if pay_total else 1
    tx_pages = max(1, (tx_total + tx_per - 1) // tx_per) if tx_total else 1
    pay_page = max(1, min(int(pay_page or 1), pay_pages))
    tx_page = max(1, min(int(tx_page or 1), tx_pages))

    return _page(
        request, "billing.html",
        user=user,
        packages=billing.list_packages(),
        payments=models.list_payments(
            user["id"], limit=pay_per, offset=(pay_page - 1) * pay_per,
        ),
        transactions=models.list_transactions(
            user["id"], limit=tx_per, offset=(tx_page - 1) * tx_per,
        ),
        wallet=wallet,
        gateway_ready=wallet["ready"],
        pay_page=pay_page,
        pay_total_pages=pay_pages,
        pay_page_items=_page_items(pay_page, pay_pages) if pay_total else [],
        tx_page=tx_page,
        tx_total_pages=tx_pages,
        tx_page_items=_page_items(tx_page, tx_pages) if tx_total else [],
        trial=_trial_for_user(user),
        channel_link=telegram_api.channel_link(),
        channel_name=telegram_api.channel_username(),
    )


@app.get("/admin/payments", response_class=HTMLResponse)
def admin_payments_page(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not user.get("is_admin"):
        raise HTTPException(403, "Can quyen admin")
    return _page(
        request, "admin_payments.html",
        user=user,
        pending=models.list_pending_payments(100),
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    return _page(
        request, "job.html",
        user=user,
        job_id=job_id,
        stages=i18n.stages_for(i18n.get_ui_lang(request)),
    )


@app.post("/api/ui-lang")
def api_set_ui_lang(request: Request, lang: str = Form(...)):
    lang = i18n.normalize_ui_lang(lang)
    resp = JSONResponse({"ok": True, "lang": lang})
    resp.set_cookie(
        i18n.COOKIE,
        lang,
        max_age=365 * 24 * 3600,
        httponly=False,
        samesite="lax",
        path="/",
    )
    return resp


# ----------------------------- auth API -----------------------------

@app.post("/api/register")
def api_register(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if "@" not in email or len(password) < 6:
        raise HTTPException(400, "Email khong hop le hoac mat khau < 6 ky tu")
    if models.get_user_by_email(email):
        raise HTTPException(400, "Email da ton tai")
    user = models.create_user(email, auth.hash_password(password), bonus=billing.SIGNUP_BONUS_CREDITS)
    token = models.create_session(user["id"])
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="lax", max_age=30 * 86400)
    return resp


@app.post("/api/login")
def api_login(email: str = Form(...), password: str = Form(...)):
    user = models.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Sai email hoac mat khau")
    token = models.create_session(user["id"])
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="lax", max_age=30 * 86400)
    return resp


@app.post("/api/logout")
def api_logout(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        models.delete_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.post("/api/forgot-password")
def api_forgot_password(email: str = Form(...)):
    """Tao token reset va gui email xac nhan. Khong hien link tren web."""
    email = email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Email khong hop le")

    if not mail.smtp_configured():
        raise HTTPException(
            503,
            "He thong chua cau hinh SMTP de gui email. "
            "Them SMTP_HOST, SMTP_FROM (va SMTP_USER/SMTP_PASSWORD) vao .env.",
        )

    user = models.get_user_by_email(email)
    # Tra loi giong nhau de tranh do email ton tai
    msg = "Neu email ton tai, chung toi da gui link dat lai mat khau. Hay kiem tra hop thu (va spam)."
    if user:
        token = models.create_password_reset(user["id"])
        reset_url = f"{_public_base()}/reset-password?token={token}"
        mailed = mail.send_reset_email(email, reset_url)
        if not mailed:
            log.error("Gui email reset that bai toi %s", email)
            raise HTTPException(
                502,
                "Khong gui duoc email. Kiem tra SMTP_HOST / SMTP_USER / SMTP_PASSWORD trong .env.",
            )
        notify.notify_password_reset(email, reset_url)  # optional admin ping
        log.info("Password reset email da gui toi %s", email)
    return {"ok": True, "message": msg}


@app.post("/api/reset-password")
def api_reset_password(
    token: str = Form(...),
    password: str = Form(...),
    password2: str = Form(""),
):
    if len(password) < 6:
        raise HTTPException(400, "Mat khau toi thieu 6 ky tu")
    if password2 and password != password2:
        raise HTTPException(400, "Mat khau nhap lai khong khop")
    user_id = models.consume_password_reset(token.strip())
    if not user_id:
        raise HTTPException(400, "Link het han hoac khong hop le")
    models.update_password(user_id, auth.hash_password(password))
    log.info("Da reset mat khau user %s", user_id)
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    user = _require_user(request)
    trial = _trial_for_user(user)
    return {
        "id": user["id"],
        "email": user["email"],
        "credits": user["credits"],
        "trial": trial,
    }


@app.post("/api/telegram/link")
def api_telegram_link(request: Request):
    """Tao deep-link de user mo bot + xac minh da join channel."""
    user = _require_user(request)
    if not telegram_api.bot_token():
        raise HTTPException(503, "Chua cau hinh TELEGRAM_BOT_TOKEN")
    bot_user = telegram_api.get_bot_username()
    if not bot_user:
        raise HTTPException(503, "Khong lay duoc username bot Telegram")
    token = models.create_telegram_link_token(user["id"])
    deep = f"https://t.me/{bot_user}?start={token}"
    return {
        "ok": True,
        "deep_link": deep,
        "channel_link": telegram_api.channel_link(),
        "channel": telegram_api.channel_username(),
        "trial_days": billing.TRIAL_DAYS,
        "videos_per_day": billing.TRIAL_VIDEOS_PER_DAY,
    }


# ----------------------------- billing API (nap tay) -----------------------------

@app.post("/api/topup")
def api_topup(request: Request, package: str = Form(...)):
    """Tao don nap tay: tra ve payment_id + thong tin vi."""
    user = _require_user(request)
    pkg = billing.get_package(package)
    if not pkg:
        raise HTTPException(400, "Goi khong hop le")
    wallet = payments.wallet_config()
    if not wallet["ready"]:
        raise HTTPException(503, "Chua cau hinh CRYPTO_WALLET_ADDRESS trong .env")
    pay_id = models.create_payment(
        user["id"], pkg["id"], pkg["credits"], pkg["price_usd"], provider="manual",
    )
    return {
        "payment_id": pay_id,
        "credits": pkg["credits"],
        "amount_usd": pkg["price_usd"],
        "package": pkg["label"],
        "wallet": wallet,
    }


@app.post("/api/topup/submit")
def api_topup_submit(
    request: Request,
    payment_id: str = Form(...),
    tx_hash: str = Form(...),
    note: str = Form(""),
):
    """User gui TX hash -> verify on-chain -> tu dong cong credit neu OK."""
    user = _require_user(request)
    pay = models.get_payment(payment_id)
    if not pay or pay["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay don nap")
    if pay["status"] == models.PAY_FINISHED:
        raise HTTPException(409, "Don da duoc cong credit")

    tx_hash = tx_hash.strip()
    used = models.find_credited_by_tx(tx_hash)
    if used and used["id"] != payment_id:
        raise HTTPException(409, "TX hash nay da duoc dung cho don khac")

    if not models.submit_payment_tx(payment_id, user["id"], tx_hash, note):
        raise HTTPException(400, "TX hash khong hop le hoac don khong the cap nhat")
    pay = models.get_payment(payment_id) or pay

    wallet = payments.wallet_config()
    result = verify_tron.verify_usdt_trc20_tx(
        tx_hash,
        expected_to=wallet["address"],
        expected_usd=float(pay["amount_usd"]),
    )

    auto = False
    if result.ok:
        auto = models.credit_topup(payment_id)
        if auto:
            log.info(
                "Auto credit OK payment=%s user=%s +%s (%s)",
                payment_id, user.get("email"), pay["credits"], result.reason,
            )
        else:
            log.warning("Verify OK nhung credit_topup that bai: %s", payment_id)
    else:
        log.info("Auto credit CHO: payment=%s — %s", payment_id, result.reason)

    pay = models.get_payment(payment_id) or pay
    notify.notify_topup_result(
        pay,
        user_email=user.get("email", ""),
        auto_credited=auto,
        verify_msg=result.reason,
    )

    if auto:
        return {
            "ok": True,
            "status": models.PAY_FINISHED,
            "auto_credited": True,
            "message": f"Da xac nhan on-chain. +{pay['credits']} credit.",
            "verify": result.reason,
        }
    return {
        "ok": True,
        "status": models.PAY_SUBMITTED,
        "auto_credited": False,
        "message": (
            "Chua tu dong cong duoc. Don dang cho admin duyet. "
            f"Ly do: {result.reason}"
        ),
        "verify": result.reason,
    }


@app.post("/api/admin/payments/{payment_id}/approve")
def api_admin_approve(request: Request, payment_id: str):
    _require_admin(request)
    pay = models.get_payment(payment_id)
    if not pay:
        raise HTTPException(404, "Khong tim thay don")
    if not models.credit_topup(payment_id):
        raise HTTPException(409, "Khong cong duoc (da cong / khong hop le)")
    log.info("Admin duyet nap %s -> +%s credit cho %s", payment_id, pay["credits"], pay["user_id"])
    return {"ok": True}


@app.post("/api/admin/payments/{payment_id}/reject")
def api_admin_reject(request: Request, payment_id: str):
    _require_admin(request)
    pay = models.get_payment(payment_id)
    if not pay:
        raise HTTPException(404, "Khong tim thay don")
    if pay.get("credited"):
        raise HTTPException(409, "Don da cong credit, khong tu choi duoc")
    models.reject_payment(payment_id)
    return {"ok": True}


# ----------------------------- jobs API -----------------------------

@app.get("/api/voices")
def api_voices():
    return {"languages": list_languages()}


@app.get("/api/voices/preview")
def api_voice_preview(request: Request, lang: str = "vi", voice: str = "male"):
    """Nghe thu giong TTS (MP3). Cache tren disk."""
    _require_user(request)
    try:
        resolve_preset(lang, voice)
    except ValueError as e:
        raise HTTPException(400, str(e))
    from ..voiceover import synthesize_preview

    # voice id co the la alias male/female
    preset = resolve_preset(lang, voice)
    vid = preset["id"]
    cache = Path("work") / "voice_previews" / f"{lang}_{vid}.mp3"
    try:
        path = synthesize_preview(lang, vid, cache)
    except Exception as e:  # noqa: BLE001
        log.warning("Voice preview fail lang=%s voice=%s: %s", lang, voice, e)
        raise HTTPException(502, f"Khong tao duoc audio mau: {e}") from e
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=f"preview_{lang}_{vid}.mp3",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.post("/api/jobs")
def api_create_job(
    request: Request,
    url: str = Form(...),
    voice: str = Form("male"),
    lang: str = Form("vi"),
):
    """Tao 1 hoac nhieu job tu o dán (nhieu link / nhieu dong)."""
    user = _require_user(request)
    try:
        resolve_preset(lang, voice)
    except ValueError as e:
        raise HTTPException(400, str(e))

    sources = extract_douyin_urls(url, limit=_MAX_BATCH_URLS)
    if not sources:
        raise HTTPException(
            400,
            "Khong nhan dien link Douyin. Dan 1 hoac nhieu link (moi link 1 dong).",
        )
    # validate host tung link (extract da chuan hoa)
    for s in sources:
        try:
            _validate_douyin(s)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    cost = billing.CREDITS_PER_VIDEO
    ref = f"job-batch:{user['id']}:{int(time.time())}:{len(sources)}"
    trial = _trial_for_user(user)
    result = models.create_jobs_with_trial(
        user["id"],
        sources,
        voice,
        lang,
        cost,
        ref,
        trial_ok=bool(trial.get("active")),
    )
    if result is None:
        raise HTTPException(402, "Khong du credit. Vui long nap them.")
    job_ids, costs = result
    for job_id, source, c in zip(job_ids, sources, costs):
        log.info(
            "Tao job %s cho %s: %s (lang=%s giong=%s, -%d credit%s)",
            job_id, user["email"], source, lang, voice, c,
            " [trial]" if c == 0 else "",
        )
    return JSONResponse(
        {"id": job_ids[0], "ids": job_ids, "count": len(job_ids)},
        status_code=202,
    )


def _job_public(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "stage": job["stage"],
        "stage_label": stage_label(job["stage"]),
        "stage_index": stage_index(job["stage"]),
        "stage_total": len(STAGES) - 1,
        "progress": job["progress"],
        "message": job["message"],
        "error": job["error"],
        "has_output": bool(job["output_path"]) and job["status"] == models.STATUS_COMPLETED,
    }


@app.get("/api/jobs")
def api_list_jobs(request: Request):
    user = _require_user(request)
    return {"jobs": [_job_public(j) for j in models.list_jobs(user_id=user["id"], limit=50)]}


@app.get("/api/jobs/{job_id}")
def api_job(request: Request, job_id: str):
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    return _job_public(job)


@app.get("/api/jobs/{job_id}/events")
def api_job_events(request: Request, job_id: str):
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")

    def _stream():
        last = None
        for _ in range(6000):
            j = models.get_job(job_id)
            if not j:
                break
            snapshot = json.dumps(_job_public(j), ensure_ascii=False)
            if snapshot != last:
                yield f"data: {snapshot}\n\n"
                last = snapshot
            if j["status"] in (models.STATUS_COMPLETED, models.STATUS_FAILED):
                break
            time.sleep(1.0)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/download")
def api_download(request: Request, job_id: str, dl: int = 0):
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    if job["status"] != models.STATUS_COMPLETED or not job["output_path"]:
        raise HTTPException(409, "Job chua hoan tat")
    out = Path(job["output_path"]).resolve()
    out_root = (Path("output").resolve(), Path("work").resolve())
    if not any(str(out).startswith(str(r)) for r in out_root) or not out.exists():
        raise HTTPException(404, "File khong ton tai")
    # dl=1: bat buoc download ve may; khong co dl: cho phep xem inline (the video)
    return FileResponse(
        out,
        media_type="video/mp4",
        filename=f"dyflow_{job_id[:12]}.mp4",
        content_disposition_type="attachment" if dl else "inline",
    )
