"""FastAPI app cho dyflow: auth + credit + nap crypto + job da luong.

Chay (worker chay kem trong tien trinh):
    uvicorn dyflow.web.app:app --port 8080

Tach worker rieng (VPS da luong): dat DYFLOW_WEB_NO_WORKER=1 cho web,
va chay `python -m dyflow.web.worker` o tien trinh khac.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    from ..preflight import hydrate_openai_key
    hydrate_openai_key()
except Exception:  # noqa: BLE001
    pass

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..pipeline import normalize_douyin_url
from ..utils import load_config, log, setup_logging
from ..voices import list_languages, list_tts_choices, resolve_preset, resolve_tts
from . import auth, billing, i18n, mail, models, notify, payments, quote, storage, telegram_api, verify_tron, worker
from ..brand import BRAND_DOMAIN, BRAND_NAME
from .progress import STAGES, stage_index, stage_label

_MAX_BATCH_URLS = 20
_MAX_BATCH_URLS_DESKTOP = 200
_MAX_CHANNEL_FULL = 40
_MAX_CHANNEL_FULL_DESKTOP = 200
_MAX_LOCAL_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
_CFG_CACHE: dict | None = None


def _batch_limit() -> int:
    try:
        from ..desktop_mode import enabled as desktop_on
        return _MAX_BATCH_URLS_DESKTOP if desktop_on() else _MAX_BATCH_URLS
    except Exception:  # noqa: BLE001
        return _MAX_BATCH_URLS


def _channel_full_cap() -> int:
    try:
        from ..desktop_mode import enabled as desktop_on
        return _MAX_CHANNEL_FULL_DESKTOP if desktop_on() else _MAX_CHANNEL_FULL
    except Exception:  # noqa: BLE001
        return _MAX_CHANNEL_FULL


def _use_chrome_for_channel(mode: str, count: str = "10") -> bool:
    """Chrome khi can full kenh hoac nhieu hon 1-2 trang API (~18 clip)."""
    if (mode or "").strip().lower() == "full":
        return True
    try:
        n = int(str(count or "10").strip())
    except ValueError:
        n = 10
    return n > 20


def _channel_fetch_limit(mode: str, count: str) -> int:
    """So video lay tu kenh: N moi nhat, hoac full (cap an toan)."""
    cap = _channel_full_cap()
    if (mode or "").strip().lower() == "full":
        return cap
    try:
        n = int(str(count or "10").strip())
    except ValueError:
        n = 10
    return max(1, min(n, cap))


def _uploads_dir() -> Path:
    try:
        from ..desktop_mode import data_dir, enabled as desktop_on
        return (data_dir() if desktop_on() else Path("work")) / "uploads"
    except Exception:  # noqa: BLE001
        return Path("work") / "uploads"


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

app = FastAPI(title=f"{BRAND_NAME} web")
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


def _items_from_quoted_json(raw: str, *, limit: int) -> list[dict]:
    """Tai su dung bang gia UI — tranh liet ke kenh (Chrome) lan 2 khi bam Start."""
    text = (raw or "").strip()
    if not text or limit <= 0:
        return []
    try:
        blob = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = blob if isinstance(blob, list) else []
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        href = str(row.get("url") or "").strip()
        if not href or href in seen:
            continue
        try:
            href = _validate_douyin(href)
        except ValueError:
            continue
        try:
            credits = max(1, int(row.get("credits") or 1))
        except (TypeError, ValueError):
            credits = 1
        seen.add(href)
        out.append({"url": href, "credits": credits})
        if len(out) >= limit:
            break
    return out


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
    models.sync_super_admin_telegram(os.getenv("TELEGRAM_SUPER_ADMIN_ID", ""))
    from ..desktop_mode import enabled as _desktop_on
    if _desktop_on():
        models.ensure_desktop_owner()
        log.info("Desktop mode: user local, khong tru credit")
    recovered = models.recover_stale()
    if recovered:
        log.info("Khoi phuc %d job running dang do -> that bai", recovered)
    if os.getenv("DYFLOW_WEB_NO_WORKER") != "1":
        worker.start_background(os.getenv("DYFLOW_CONFIG") or "config.yaml")
    log.info("dyflow web san sang tai http://localhost:8080")


def _license_free_path(path: str) -> bool:
    if path.startswith("/static") or path.startswith("/api/license"):
        return True
    return path in ("/license", "/favicon.ico", "/api/ui-lang", "/robots.txt")


@app.middleware("http")
async def _kick_worker(request: Request, call_next):
    """Windows: uvicorn trong thread co the bo qua startup -> worker khong chay."""
    try:
        worker.ensure_started()
    except Exception:  # noqa: BLE001
        pass
    from ..license_key import block_message, gate_ok

    if gate_ok() or _license_free_path(request.url.path):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        lang = i18n.get_ui_lang(request)
        return JSONResponse({"detail": block_message(lang)}, status_code=403)
    return RedirectResponse("/license", status_code=302)


def _require_admin(request: Request) -> dict:
    user = _require_user(request)
    if models.is_blocked_user(user):
        raise HTTPException(403, "Tai khoan bi khoa")
    if not user.get("is_admin") and not user.get("is_super_admin"):
        raise HTTPException(403, "Can quyen admin")
    return user


def _require_super_admin(request: Request) -> dict:
    user = _require_admin(request)
    if not models.is_super_admin_user(user):
        raise HTTPException(403, "Can quyen super admin")
    return user


@app.get("/license", response_class=HTMLResponse)
def license_page(request: Request):
    from ..desktop_mode import enabled as desktop_on
    from ..license_key import is_required, public_status

    if not desktop_on() and not is_required():
        return RedirectResponse("/", status_code=302)
    user = auth.current_user(request)
    return _page(
        request,
        "license.html",
        user=user,
        license=public_status(user=user),
    )


@app.get("/api/license")
def api_license_status(request: Request):
    from ..license_key import public_status

    return public_status(user=auth.current_user(request))


@app.post("/api/license")
def api_license_activate(request: Request, key: str = Form("")):
    from ..license_key import LicenseError, activate, public_status

    lang = i18n.get_ui_lang(request)
    try:
        activate(key)
    except LicenseError as e:
        msg_key = {
            "empty": "license_empty",
            "expired": "license_expired",
            "invalid": "license_invalid",
            "machine": "license_machine",
        }.get(e.code, "license_invalid")
        raise HTTPException(400, i18n.t(lang, msg_key)) from e
    models.apply_desktop_license_role()
    user = auth.current_user(request)
    return {"ok": True, **public_status(user=user)}


@app.post("/api/license/mint")
def api_license_mint(
    request: Request,
    days: str = Form("5d"),
    role: str = Form("user"),
    machine: str = Form(""),
):
    from ..license_key import (
        actor_role_for,
        allowed_mint_roles,
        mint,
        mint_max_days,
        normalize_role,
        parse_duration,
        parse_machine_code,
        public_status,
    )

    user = _require_user(request)
    lang = i18n.get_ui_lang(request)
    actor = actor_role_for(user)
    try:
        target = normalize_role(role)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if target not in allowed_mint_roles(actor):
        raise HTTPException(403, i18n.t(lang, "license_mint_forbidden"))
    try:
        n = parse_duration(days, max_days=mint_max_days(actor))
        mach = parse_machine_code(machine) if (machine or "").strip() else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    key, info = mint(n, role=target, machine=mach)
    return {
        "ok": True,
        "key": key,
        "role": info["role"],
        "days": info["days"],
        "kid": info.get("kid") or "",
        "machine_code": info.get("machine_code") or "",
        "expires_at": info.get("expires_at"),
        "until_label": (
            ""
            if info.get("perpetual")
            else time.strftime("%d/%m/%Y", time.localtime(float(info["expires_at"])))
        ),
        "perpetual": bool(info.get("perpetual")),
        **public_status(user=user),
    }


@app.post("/api/license/renew")
def api_license_renew(
    request: Request,
    key: str = Form(""),
    days: str = Form("1m"),
    machine: str = Form(""),
):
    from ..license_key import (
        LicenseError,
        actor_role_for,
        allowed_mint_roles,
        mint_max_days,
        parse_duration,
        public_status,
        renew,
    )

    user = _require_user(request)
    lang = i18n.get_ui_lang(request)
    actor = actor_role_for(user)
    if actor not in ("admin", "super"):
        raise HTTPException(403, i18n.t(lang, "license_mint_forbidden"))
    try:
        from ..license_key import decode as _decode

        info_probe = _decode(key, allow_expired=True, check_machine=False)
    except LicenseError as e:
        msg_key = "license_empty" if e.code == "empty" else "license_invalid"
        raise HTTPException(400, i18n.t(lang, msg_key)) from e
    target_role = str(info_probe.get("role") or "user")
    if target_role not in allowed_mint_roles(actor):
        raise HTTPException(403, i18n.t(lang, "license_mint_forbidden"))
    try:
        n = parse_duration(days, max_days=mint_max_days(actor))
        new_key, info = renew(key, n, machine=machine.strip() or None)
    except (ValueError, LicenseError) as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "key": new_key,
        "role": info["role"],
        "days": info["days"],
        "kid": info.get("kid") or "",
        "machine_code": info.get("machine_code") or "",
        "expires_at": info.get("expires_at"),
        "until_label": (
            ""
            if info.get("perpetual")
            else time.strftime("%d/%m/%Y", time.localtime(float(info["expires_at"])))
        ),
        "perpetual": bool(info.get("perpetual")),
        **public_status(user=user),
    }


@app.post("/api/openai-tts-key")
def api_openai_tts_key(request: Request, key: str = Form("")):
    from ..license_key import public_status
    from ..user_openai import save_tts_key

    _require_user(request)
    lang = i18n.get_ui_lang(request)
    try:
        save_tts_key(key)
    except ValueError:
        raise HTTPException(400, i18n.t(lang, "openai_tts_key_invalid"))
    return {"ok": True, **public_status(user=auth.current_user(request))}


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
        return _page(request, "landing.html", user=None)
    languages = list_languages()
    ui = i18n.get_ui_lang(request)
    lang_ids = {x["id"] for x in languages}
    default_dub = ui if ui in lang_ids else "en"
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
    for j in jobs:
        if j.get("status") == models.STATUS_QUEUED:
            pos, total_q = models.queue_position(j["id"])
            j["queue_pos"] = pos
            j["queue_total"] = total_q
    trial = _trial_for_user(user)

    return _page(
        request, "index.html",
        user=user,
        languages=languages,
        tts_choices=list_tts_choices(),
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


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    domain = BRAND_DOMAIN
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /login\n"
        "Allow: /register\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        "Disallow: /jobs/\n"
        "Disallow: /billing\n"
        "Disallow: /library\n"
        "Disallow: /queue\n"
        "Disallow: /batch\n"
        "Disallow: /forgot-password\n"
        "Disallow: /reset-password\n"
        f"Sitemap: https://{domain}/sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=Response)
def sitemap_xml():
    domain = BRAND_DOMAIN
    base = f"https://{domain}"
    today = time.strftime("%Y-%m-%d")
    urls = [
        ("/", "1.0", "daily"),
        ("/register", "0.9", "weekly"),
        ("/login", "0.6", "monthly"),
    ]
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, prio, freq in urls:
        body.append("  <url>")
        body.append(f"    <loc>{base}{path}</loc>")
        body.append(f"    <lastmod>{today}</lastmod>")
        body.append(f"    <changefreq>{freq}</changefreq>")
        body.append(f"    <priority>{prio}</priority>")
        body.append("  </url>")
    body.append("</urlset>")
    return Response("\n".join(body) + "\n", media_type="application/xml")


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


@app.get("/batch", response_class=HTMLResponse)
def batch_page(request: Request, page: int = 1):
    """Cung form tao video, nhung o URL lon — dan nhieu link / 1 link kenh."""
    return index(request, page=page)


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    jobs = models.list_jobs(
        user_id=user["id"],
        limit=50,
        statuses=(
            models.STATUS_RUNNING,
            models.STATUS_QUEUED,
            models.STATUS_FAILED,
            models.STATUS_CANCELLED,
        ),
    )
    for j in jobs:
        if j.get("status") == models.STATUS_QUEUED:
            pos, total_q = models.queue_position(j["id"])
            j["queue_pos"] = pos
            j["queue_total"] = total_q

    return _page(request, "queue.html", user=user, jobs=jobs)


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
    user = _require_admin(request)
    return _page(
        request, "admin_payments.html",
        user=user,
        pending=models.list_pending_payments(100),
        admin_tab="payments",
        stats=models.admin_stats(),
    )


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
def admin_home(request: Request):
    user = _require_admin(request)
    return _page(
        request, "admin_home.html",
        user=user,
        admin_tab="home",
        stats=models.admin_stats(),
    )


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request, q: str = "", page: int = 1):
    user = _require_admin(request)
    page = max(1, int(page or 1))
    per = 30
    total = models.count_users(q)
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages)
    users = models.list_users(limit=per, offset=(page - 1) * per, q=q)
    return _page(
        request, "admin_users.html",
        user=user,
        admin_tab="users",
        users=users,
        q=q,
        page=page,
        total_pages=pages,
        page_items=_page_items(page, pages) if total else [],
        total=total,
        is_super=models.is_super_admin_user(user),
        stats=models.admin_stats(),
    )


@app.get("/admin/jobs", response_class=HTMLResponse)
def admin_jobs_page(request: Request, status: str = "", page: int = 1):
    user = _require_admin(request)
    page = max(1, int(page or 1))
    per = 40
    st = (status or "").strip() or None
    if st and st not in {
        models.STATUS_QUEUED, models.STATUS_RUNNING,
        models.STATUS_COMPLETED, models.STATUS_FAILED,
    }:
        st = None
    total = models.count_jobs(status=st)
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages)
    jobs = models.list_jobs_admin(limit=per, offset=(page - 1) * per, status=st)
    return _page(
        request, "admin_jobs.html",
        user=user,
        admin_tab="jobs",
        jobs=jobs,
        status_filter=st or "",
        page=page,
        total_pages=pages,
        page_items=_page_items(page, pages) if total else [],
        total=total,
        stats=models.admin_stats(),
    )


@app.post("/api/admin/users/{user_id}/credits")
def api_admin_credits(
    request: Request,
    user_id: str,
    amount: int = Form(...),
    note: str = Form(""),
):
    admin = _require_admin(request)
    amount = int(amount)
    if amount == 0:
        raise HTTPException(400, "Amount phai != 0")
    ref = f"admin:{admin['email']}:{ (note or '').strip()[:80] }"
    updated = models.admin_adjust_credits(user_id, amount, ref=ref)
    if updated is None:
        raise HTTPException(400, "Khong dieu chinh duoc (user khong ton tai / so du am)")
    log.info("Admin %s adjust %s %+d -> %s", admin["email"], user_id, amount, updated["credits"])
    return {"ok": True, "credits": updated["credits"]}


@app.post("/api/admin/users/{user_id}/admin")
def api_admin_set_admin(
    request: Request,
    user_id: str,
    is_admin: int = Form(...),
):
    _require_super_admin(request)
    target = models.get_user(user_id)
    if not target:
        raise HTTPException(404, "Khong tim thay user")
    if target.get("is_super_admin") and not int(is_admin):
        raise HTTPException(400, "Khong the go quyen super admin")
    if not models.set_user_admin(user_id, bool(int(is_admin))):
        raise HTTPException(400, "Khong cap nhat duoc")
    return {"ok": True, "is_admin": bool(int(is_admin))}


@app.post("/api/admin/users/{user_id}/block")
def api_admin_block(
    request: Request,
    user_id: str,
    blocked: int = Form(...),
):
    admin = _require_admin(request)
    target = models.get_user(user_id)
    if not target:
        raise HTTPException(404, "Khong tim thay user")
    if target.get("is_super_admin"):
        raise HTTPException(400, "Khong khoa super admin")
    if target["id"] == admin["id"]:
        raise HTTPException(400, "Khong tu khoa chinh minh")
    if not models.set_user_blocked(user_id, bool(int(blocked))):
        raise HTTPException(400, "Khong cap nhat duoc")
    return {"ok": True, "blocked": bool(int(blocked))}


@app.post("/api/admin/jobs/{job_id}/retry")
def api_admin_job_retry(request: Request, job_id: str):
    _require_admin(request)
    if not models.requeue_job(job_id):
        raise HTTPException(400, "Chi retry duoc job failed")
    return {"ok": True}


@app.post("/api/admin/jobs/{job_id}/refund")
def api_admin_job_refund(request: Request, job_id: str):
    _require_admin(request)
    job = models.get_job(job_id)
    if not job:
        raise HTTPException(404, "Khong tim thay job")
    models.refund_job(job_id)
    return {"ok": True}


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


@app.get("/jobs/{job_id}/editor", response_class=HTMLResponse)
def editor_page(request: Request, job_id: str):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    return _page(
        request, "editor.html",
        user=user,
        job_id=job_id,
        job_status=job.get("status") or "",
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
    if models.is_blocked_user(user):
        raise HTTPException(403, "Tai khoan bi khoa")
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
    lang = i18n.get_ui_lang(request)
    pkg = billing.get_package(package)
    if not pkg:
        raise HTTPException(400, i18n.t(lang, "topup_bad_pkg"))
    if pkg.get("id") != "trial":
        raise HTTPException(403, i18n.t(lang, "topup_trial_only"))
    wallet = payments.wallet_config()
    if not wallet["ready"]:
        raise HTTPException(503, i18n.t(lang, "wallet_missing"))
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
    lang = i18n.get_ui_lang(request)
    pay = models.get_payment(payment_id)
    if not pay or pay["user_id"] != user["id"]:
        raise HTTPException(404, i18n.t(lang, "topup_not_found"))
    if pay["status"] == models.PAY_FINISHED:
        raise HTTPException(409, i18n.t(lang, "topup_already"))

    tx_hash = tx_hash.strip()
    used = models.find_credited_by_tx(tx_hash)
    if used and used["id"] != payment_id:
        raise HTTPException(409, i18n.t(lang, "topup_tx_used"))

    if not models.submit_payment_tx(payment_id, user["id"], tx_hash, note):
        raise HTTPException(400, i18n.t(lang, "topup_tx_bad"))
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
            "message": i18n.t(lang, "topup_auto_msg", n=pay["credits"]),
            "verify": result.reason,
        }
    return {
        "ok": True,
        "status": models.PAY_SUBMITTED,
        "auto_credited": False,
        "message": i18n.t(lang, "topup_manual_msg", reason=result.reason),
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
    return {"languages": list_languages(), "tts": list_tts_choices()}


@app.get("/api/voices/preview")
def api_voice_preview(
    request: Request,
    lang: str = "vi",
    voice: str = "male",
    tts: str = "edge",
):
    """Nghe thu giong TTS (MP3). Cache tren disk."""
    _require_user(request)
    try:
        resolve_preset(lang, voice)
        tts_cfg = resolve_tts(tts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if tts_cfg.get("needs_openai_key"):
        from ..user_openai import has_tts_key

        if not has_tts_key():
            raise HTTPException(400, i18n.t(i18n.get_ui_lang(request), "openai_tts_key_needed"))
    from ..voiceover import synthesize_preview

    preset = resolve_preset(lang, voice)
    vid = preset["id"]
    engine = tts_cfg["tts_engine"]
    model = tts_cfg["openai_model"]
    cache = Path("work") / "voice_previews" / f"{lang}_{vid}_{engine}_{model}.mp3"
    try:
        path = synthesize_preview(
            lang, vid, cache,
            tts_engine=engine,
            openai_model=model,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Voice preview fail lang=%s voice=%s tts=%s: %s", lang, voice, tts, e)
        raise HTTPException(502, f"Khong tao duoc audio mau: {e}") from e
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=f"preview_{lang}_{vid}_{model}.mp3",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.post("/api/quote")
def api_quote(
    request: Request,
    url: str = Form(...),
    channel_mode: str = Form("count"),
    channel_count: str = Form("10"),
):
    """Uoc tinh credit theo do dai tung link (khong tao job)."""
    _require_user(request)
    cfg = _web_cfg()
    dl_cfg = dict(cfg.get("download") or {})
    ch_lim = _channel_fetch_limit(channel_mode, channel_count)
    try:
        result = quote.quote_urls(
            url, dl_cfg, limit=max(_batch_limit(), ch_lim), channel_limit=ch_lim,
            allow_chrome=_use_chrome_for_channel(channel_mode, channel_count),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return result


@app.get("/api/caption-presets")
def api_caption_presets(request: Request):
    _require_user(request)
    from ..export_style import PRESETS, preset_names

    return {
        "default": "classic",
        "presets": [
            {
                "id": name,
                "primary_color": PRESETS[name]["primary_color"],
                "outline_color": PRESETS[name]["outline_color"],
                "border_style": PRESETS[name]["border_style"],
            }
            for name in preset_names()
        ],
    }


def _save_local_videos(uploads: list[UploadFile], *, limit: int) -> list[str]:
    """Luu video tu may vao uploads/, tra ve path local (source job)."""
    up = _uploads_dir()
    up.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for upload in uploads or []:
        if len(paths) >= limit:
            break
        name = (upload.filename or "").strip()
        if not name:
            continue
        ext = Path(name).suffix.lower()
        if ext not in _VIDEO_EXTS:
            raise HTTPException(400, f"Dinh dang khong ho tro: {name}")
        stem = Path(name).stem
        safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in stem)
        safe = (safe.strip("._") or "video")[:48]
        dest = up / f"local_{uuid.uuid4().hex[:8]}_{safe}{ext}"
        size = 0
        try:
            with dest.open("wb") as fh:
                while True:
                    chunk = upload.file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _MAX_LOCAL_VIDEO_BYTES:
                        dest.unlink(missing_ok=True)
                        raise HTTPException(400, "Video toi da 2GB / file")
                    fh.write(chunk)
        finally:
            try:
                upload.file.close()
            except Exception:  # noqa: BLE001
                pass
        if size < 1000:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"File rong: {name}")
        paths.append(str(dest.resolve()))
    return paths


def _credits_for_local(path: str) -> int:
    from ..utils import probe_duration
    from . import billing as _billing

    try:
        dur = probe_duration(path)
    except Exception:  # noqa: BLE001
        dur = None
    return int(_billing.credits_for_duration(dur))


@app.post("/api/jobs")
def api_create_job(
    request: Request,
    url: str = Form(""),
    voice: str = Form(""),
    lang: str = Form(""),
    tts: str = Form(""),
    preset_name: str = Form("classic"),
    video_speed: str = Form("1.0"),
    logo_path: str = Form(""),
    logo_position: str = Form("top_right"),
    logo_size_percent: str = Form("10"),
    sub_pos_x: str = Form(""),
    sub_pos_y: str = Form(""),
    sub_scale: str = Form(""),
    logo: UploadFile | None = File(None),
    videos: list[UploadFile] = File(default=[]),
    channel_mode: str = Form("count"),
    channel_count: str = Form("10"),
    quoted_json: str = Form(""),
):
    """Tao 1 hoac nhieu job: link Douyin (ke ca full kenh), hoac video local."""
    user = _require_user(request)
    if models.is_blocked_user(user):
        raise HTTPException(403, "Tai khoan bi khoa")
    lang = (lang or "").strip()
    voice = (voice or "").strip()
    tts = (tts or "").strip()
    if not lang:
        raise HTTPException(400, "Chon ngon ngu dich truoc khi bat dau.")
    if not voice:
        raise HTTPException(400, "Chon giong du phong truoc khi bat dau.")
    if not tts:
        raise HTTPException(400, "Chon engine TTS truoc khi bat dau.")
    try:
        resolve_preset(lang, voice)
        tts_cfg = resolve_tts(tts)
    except ValueError as e:
        raise HTTPException(400, str(e))

    cfg = _web_cfg()
    from ..preflight import assert_job_ready
    try:
        assert_job_ready(cfg, {"voiceover": {"tts_engine": tts_cfg["tts_engine"]}})
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e

    limit = _batch_limit()
    ch_lim = _channel_fetch_limit(channel_mode, channel_count)
    uploads = [v for v in (videos or []) if v is not None and (v.filename or "").strip()]
    local_paths = _save_local_videos(uploads, limit=max(limit, ch_lim)) if uploads else []

    # Probe do dai + tinh credit (cung logic /api/quote) — xu ly full clip theo so credit
    dl_cfg = dict(cfg.get("download") or {})
    quoted_items: list[dict] = []
    room = max(limit, ch_lim) - len(local_paths)
    full_channel = _use_chrome_for_channel(channel_mode, channel_count)
    # Full kênh: khong dung bang gia 32 clip (HTML/yt-dlp thieu trang).
    reused = [] if full_channel else _items_from_quoted_json(quoted_json, limit=room)
    if reused:
        quoted_items = reused
    elif (url or "").strip() and room > 0:
        try:
            quoted = quote.quote_urls(
                url, dl_cfg, limit=room, channel_limit=min(ch_lim, room),
                allow_chrome=full_channel,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        quoted_items = list(quoted.get("items") or [])

    sources = [str(i["url"]) for i in quoted_items]
    costs = [int(i["credits"]) for i in quoted_items]
    for s in sources:
        try:
            _validate_douyin(s)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    for path in local_paths:
        sources.append(path)
        costs.append(_credits_for_local(path))

    if not sources:
        raise HTTPException(
            400,
            "Dan link Douyin (video hoac kenh) hoac chon video tu may.",
        )

    from ..export_style import normalize_output_opts

    logo_saved = str(logo_path or "").strip()
    if logo is not None and logo.filename:
        raw = logo.file.read()
        if raw:
            if len(raw) > 8 * 1024 * 1024:
                raise HTTPException(400, "Logo toi da 8MB")
            up = _uploads_dir()
            up.mkdir(parents=True, exist_ok=True)
            ext = Path(logo.filename).suffix.lower() or ".png"
            if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
                ext = ".png"
            dest = up / f"logo_{uuid.uuid4().hex}{ext}"
            dest.write_bytes(raw)
            logo_saved = str(dest)

    opts = normalize_output_opts({
        "preset_name": preset_name,
        "video_speed": video_speed,
        "logo_path": logo_saved,
        "logo_position": logo_position,
        "logo_size_percent": logo_size_percent,
        "sub_pos_x": sub_pos_x or None,
        "sub_pos_y": sub_pos_y or None,
        "sub_scale": sub_scale or None,
    })
    opts_json = json.dumps(opts, ensure_ascii=False)

    ref = f"job-batch:{user['id']}:{int(time.time())}:{len(sources)}"
    trial = _trial_for_user(user)
    result = models.create_jobs_with_trial(
        user["id"],
        sources,
        voice,
        lang,
        costs,
        ref,
        trial_ok=bool(trial.get("active")),
        tts_engine=tts_cfg["tts_engine"],
        openai_model=tts_cfg["openai_model"],
        output_opts_json=opts_json,
    )
    if result is None:
        raise HTTPException(402, "Khong du credit. Vui long nap them.")
    job_ids, charged = result
    for job_id, source, c in zip(job_ids, sources, charged):
        log.info(
            "Tao job %s cho %s: %s (lang=%s giong=%s, -%d credit%s)",
            job_id, user["email"], source, lang, voice, c,
            " [trial]" if c == 0 else "",
        )
    return JSONResponse(
        {
            "id": job_ids[0],
            "ids": job_ids,
            "count": len(job_ids),
            "credits_charged": sum(charged),
            "costs": charged,
        },
        status_code=202,
    )


def _job_public(job: dict) -> dict:
    data = {
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
        "source_url": job.get("source_url") or "",
        "target_lang": job.get("target_lang") or "",
        "voice": job.get("voice") or "",
        "preset_name": "classic",
        "created_at": job.get("created_at"),
        "qa": None,
    }
    raw_opts = job.get("output_opts_json")
    if raw_opts:
        try:
            opts = json.loads(raw_opts)
            if isinstance(opts, dict):
                data["preset_name"] = opts.get("preset_name") or "classic"
                data["output_opts"] = opts
        except (TypeError, json.JSONDecodeError):
            pass
    raw_qa = job.get("qa_json")
    if raw_qa:
        try:
            data["qa"] = json.loads(raw_qa)
        except (TypeError, json.JSONDecodeError):
            data["qa"] = None
    if job["status"] == models.STATUS_QUEUED:
        pos, total = models.queue_position(job["id"])
        data["queue_pos"] = pos
        data["queue_total"] = total
    return data


@app.get("/api/jobs")
def api_list_jobs(request: Request):
    user = _require_user(request)
    return {"jobs": [_job_public(j) for j in models.list_jobs(user_id=user["id"], limit=50)]}


@app.post("/api/jobs/cancel-active")
def api_cancel_active(request: Request):
    user = _require_user(request)
    n = models.cancel_active_jobs(user["id"])
    return {"ok": True, "cancelled": n}


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(request: Request, job_id: str):
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    if not models.cancel_job(job_id, user["id"]):
        raise HTTPException(400, "Job khong the huy (da xong hoac da huy)")
    return {"ok": True, "id": job_id, "status": "cancelled"}


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
            if j["status"] in (
                models.STATUS_COMPLETED,
                models.STATUS_FAILED,
                models.STATUS_CANCELLED,
            ):
                break
            time.sleep(1.0)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _output_roots() -> list[Path]:
    roots = [Path("output").resolve(), Path("work").resolve()]
    try:
        from ..desktop_mode import data_dir

        roots.append((data_dir() / "output").resolve())
        roots.append((data_dir() / "work").resolve())
    except Exception:  # noqa: BLE001
        pass
    paths = (_web_cfg().get("paths") or {})
    for key in ("output_dir", "work_dir"):
        raw = str(paths.get(key) or "").strip()
        if raw:
            roots.append(Path(raw).expanduser().resolve())
    return roots


def _job_output_file(job: dict) -> Path:
    if not job.get("output_path"):
        raise HTTPException(409, "Chua co file xuat")
    out = Path(str(job["output_path"])).expanduser().resolve()
    if not out.exists():
        raise HTTPException(404, "File khong ton tai")
    out_s = out.resolve()
    if not any(_is_relative_to(out_s, r) for r in _output_roots()):
        raise HTTPException(404, "File khong ton tai")
    return out


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _user_downloads() -> Path:
    home = Path.home()
    for name in ("Downloads", "Tải xuống"):
        p = home / name
        if p.is_dir():
            return p
    p = home / "Downloads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _reveal_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:  # noqa: BLE001
        log.warning("Khong mo duoc thu muc: %s", path)


@app.get("/api/jobs/{job_id}/download")
def api_download(request: Request, job_id: str, dl: int = 0):
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    out = _job_output_file(job)
    # dl=1: bat buoc download ve may; khong co dl: cho phep xem inline (the video)
    return FileResponse(
        out,
        media_type="video/mp4",
        filename=f"videoscreator_{job_id[:12]}.mp4",
        content_disposition_type="attachment" if dl else "inline",
    )


@app.post("/api/jobs/{job_id}/export-local")
def api_export_local(request: Request, job_id: str):
    """App desktop (pywebview): copy MP4 vao Downloads + mo Explorer. Khong dung <a download>."""
    from ..desktop_mode import enabled as desktop_on

    if not desktop_on():
        raise HTTPException(400, "Chi dung tren app desktop")
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    src = _job_output_file(job)
    dest_dir = _user_downloads()
    dest = dest_dir / f"videoscreator_{job_id[:12]}.mp4"
    if not (dest.exists() and dest.stat().st_size == src.stat().st_size):
        n = 2
        while dest.exists():
            dest = dest_dir / f"videoscreator_{job_id[:12]}_{n}.mp4"
            n += 1
        shutil.copy2(src, dest)
    _reveal_file(dest)
    return {"ok": True, "path": str(dest)}


def _owned_job(request: Request, job_id: str) -> dict:
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    return job


def _editor_video(job: dict):
    from ..video_editor import find_apply_source, find_preview_video

    cfg = _web_cfg()
    preview = find_preview_video(job["id"], job, cfg)
    if not preview:
        raise HTTPException(409, "Chua co video de sua. Cho pipeline tai/xuat xong.")
    return find_apply_source(job["id"], job, cfg, preview)


@app.get("/api/jobs/{job_id}/editor")
def api_editor_get(request: Request, job_id: str):
    from ..utils import probe_duration, probe_video_size
    from ..video_editor import job_work_dir, load_layout

    job = _owned_job(request, job_id)
    cfg = _web_cfg()
    try:
        src = _editor_video(job)
    except HTTPException as exc:
        return JSONResponse(
            {"ok": False, "has_video": False, "detail": exc.detail},
            status_code=409,
        )
    w, h = probe_video_size(src)
    duration = probe_duration(src)
    work = job_work_dir(job_id, cfg)
    layout = load_layout(work, video_w=w, video_h=h, duration=duration)
    logo = work / "editor_logo.png"
    return {
        "ok": True,
        "has_video": True,
        "status": job.get("status"),
        "duration": duration,
        "video_w": w,
        "video_h": h,
        "has_logo": logo.is_file(),
        "layout": layout,
        "media_url": f"/api/jobs/{job_id}/editor/media",
    }


@app.put("/api/jobs/{job_id}/editor")
def api_editor_save(request: Request, job_id: str, payload: dict = Body(...)):
    from ..utils import probe_duration, probe_video_size
    from ..video_editor import job_work_dir, normalize_layout, save_layout

    job = _owned_job(request, job_id)
    cfg = _web_cfg()
    src = _editor_video(job)
    w, h = probe_video_size(src)
    duration = probe_duration(src)
    layout = normalize_layout(
        payload.get("layout") if isinstance(payload, dict) else payload,
        video_w=w, video_h=h, duration=duration,
    )
    save_layout(job_work_dir(job_id, cfg), layout)
    return {"ok": True, "layout": layout}


@app.get("/api/jobs/{job_id}/editor/media")
def api_editor_media(request: Request, job_id: str, dl: int = 0):
    job = _owned_job(request, job_id)
    src = _editor_video(job)
    return FileResponse(
        src,
        media_type="video/mp4",
        filename=f"videoscreator_{job_id[:12]}.mp4",
        content_disposition_type="attachment" if dl else "inline",
    )


@app.post("/api/jobs/{job_id}/editor/scan")
def api_editor_scan(request: Request, job_id: str, payload: dict = Body(default=None)):
    from ..video_editor import scan_text_regions

    job = _owned_job(request, job_id)
    src = _editor_video(job)
    body = payload or {}
    try:
        t = float(body.get("t") or 0)
    except (TypeError, ValueError):
        t = 0.0
    try:
        window = float(body.get("window") or 0)
    except (TypeError, ValueError):
        window = 0.0
    window = max(0.0, min(60.0, window))
    try:
        regions = scan_text_regions(src, t=t, window_sec=window)
    except ImportError:
        raise HTTPException(503, "Chua cai RapidOCR (pip install rapidocr-onnxruntime)") from None
    except Exception as e:  # noqa: BLE001
        log.warning("Editor OCR loi: %s", e)
        raise HTTPException(500, f"OCR that bai: {e}") from e
    return {"ok": True, "regions": regions, "count": len(regions)}


@app.post("/api/jobs/{job_id}/editor/logo")
async def api_editor_logo(request: Request, job_id: str, file: UploadFile = File(...)):
    from ..video_editor import job_work_dir, save_logo

    job = _owned_job(request, job_id)
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "Logo toi da 8MB")
    suffix = Path(file.filename or "logo.png").suffix
    dest = save_logo(job_work_dir(job_id, _web_cfg()), raw, suffix=suffix)
    return {"ok": True, "has_logo": dest.is_file()}


@app.get("/api/jobs/{job_id}/editor/logo")
def api_editor_logo_get(request: Request, job_id: str):
    from ..video_editor import job_work_dir

    job = _owned_job(request, job_id)
    path = job_work_dir(job_id, _web_cfg()) / "editor_logo.png"
    if not path.is_file():
        raise HTTPException(404, "Chua co logo")
    return FileResponse(path, media_type="image/png")


@app.post("/api/jobs/{job_id}/editor/apply")
def api_editor_apply(request: Request, job_id: str, payload: dict = Body(default=None)):
    from ..utils import probe_duration, probe_video_size
    from ..video_editor import (
        apply_layout,
        find_apply_source,
        job_output_dir,
        job_work_dir,
        normalize_layout,
        save_layout,
    )

    job = _owned_job(request, job_id)
    cfg = _web_cfg()
    preview = _editor_video(job)
    src = find_apply_source(job_id, job, cfg, preview)
    work = job_work_dir(job_id, cfg)
    pinned = work / "editor_source.mp4"
    if not pinned.exists() and src.resolve() != pinned.resolve():
        try:
            work.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, pinned)
            src = pinned
        except OSError:
            pass
    w, h = probe_video_size(src)
    duration = probe_duration(src)
    body = payload or {}
    layout = normalize_layout(
        body.get("layout") if isinstance(body, dict) else body,
        video_w=w, video_h=h, duration=duration,
    )
    save_layout(work, layout)
    dst = job_output_dir(cfg) / f"{job_id}_edit.mp4"
    logo = work / "editor_logo.png"
    font = str((cfg.get("paths") or {}).get("font_path") or "")
    try:
        apply_layout(
            src, dst, layout,
            logo_path=logo if logo.is_file() else None,
            font_path=font,
        )
    except RuntimeError as e:
        log.warning("Editor apply loi: %s", e)
        raise HTTPException(500, str(e)[:400]) from e
    models.set_output_path(job_id, str(dst))
    return {
        "ok": True,
        "output": str(dst),
        "download": f"/api/jobs/{job_id}/editor/media?dl=1",
    }
