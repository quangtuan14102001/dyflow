"""FastAPI app cho dyflow: auth + credit + nap crypto + job da luong.

Chay (worker chay kem trong tien trinh):
    uvicorn dyflow.web.app:app --port 8080

Tach worker rieng (VPS da luong): dat DYFLOW_WEB_NO_WORKER=1 cho web,
va chay `python -m dyflow.web.worker` o tien trinh khac.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
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
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..pipeline import normalize_douyin_url
from ..utils import load_config, log, setup_logging
from ..voices import list_languages, list_tts_choices, resolve_preset, resolve_tts
from . import auth, billing, i18n, mail, models, notify, payments, quote, storage, telegram_api, verify_tron, worker
from ..brand import BRAND_DOMAIN, BRAND_NAME
from .progress import STAGES, stage_index, stage_label

_MAX_BATCH_URLS = 20
_MAX_BATCH_URLS_DESKTOP = 100_000
_MAX_CHANNEL_FULL = 40
_MAX_CHANNEL_FULL_DESKTOP = 100_000
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


def _use_chrome_for_channel(_mode: str, _count: str = "10") -> bool:
    """Khong mo Chrome/Edge de liet ke kenh — HTTP + headless sau."""
    return False


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


def _resolve_asset_dir(sub: str) -> Path:
    try:
        from ..desktop_mode import data_dir

        patch_d = data_dir() / "patch" / "dyflow" / "web" / sub
        if patch_d.is_dir():
            return patch_d
    except Exception:
        pass
    return BASE_DIR / sub


templates = Jinja2Templates(directory=str(_resolve_asset_dir("templates")))

app = FastAPI(title=f"{BRAND_NAME} web")
app.mount("/static", StaticFiles(directory=str(_resolve_asset_dir("static"))), name="static")

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
    """Tai su dung bang gia UI — tranh liet ke kenh (Chrome) lan 2 khi bam Start.

    Chap nhan: JSON list object, JSON list URL, object {items: [...]},
    hoac text nhieu dong URL (WebView2 Windows hay lam vo quoted_json).
    """
    text = (raw or "").strip()
    if not text or limit <= 0:
        return []
    blob: object | None
    try:
        blob = json.loads(text)
    except json.JSONDecodeError:
        blob = None
    rows: list = []
    if isinstance(blob, list):
        rows = blob
    elif isinstance(blob, dict):
        inner = blob.get("items")
        rows = inner if isinstance(inner, list) else []
    if not rows:
        from ..pipeline import extract_douyin_urls

        urls = extract_douyin_urls(text, limit=limit)
        rows = urls
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        credits = 1
        if isinstance(row, str):
            href = row.strip()
        elif isinstance(row, dict):
            href = str(row.get("url") or "").strip()
            try:
                credits = max(1, int(row.get("credits") or 1))
            except (TypeError, ValueError):
                credits = 1
        else:
            continue
        if not href or href in seen:
            continue
        try:
            href = _validate_douyin(href)
        except ValueError:
            continue
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
    if path.startswith("/static") or path.startswith("/api/license") or path == "/api/healthz":
        return True
    return path in ("/license", "/favicon.ico", "/api/ui-lang", "/robots.txt")


@app.get("/api/healthz")
def api_healthz():
    return {"status": "ok", "app": BRAND_NAME}


@app.exception_handler(Exception)
async def _unhandled_api_error(request: Request, exc: Exception):
    """Windows WebView2: HTML/plain 500 -> UI chi hien 'Could not create job'."""
    if isinstance(exc, (HTTPException, StarletteHTTPException, RequestValidationError)):
        if isinstance(exc, RequestValidationError):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        return await http_exception_handler(request, exc)
    log.exception("Unhandled %s %s", request.method, request.url.path)
    msg = str(exc).strip() or exc.__class__.__name__
    return JSONResponse({"detail": msg[:500]}, status_code=500)


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
    # public_status cung co role/until_label/... cua ACTOR — dat truoc, roi
    # ghi de bang role/han cua KEY vua mint (tranh hien "super" khi tao VC1).
    return {
        "ok": True,
        **public_status(user=user),
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
        **public_status(user=user),
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
    }


@app.post("/api/openai-tts-key")
def api_openai_tts_key(request: Request, key: str = Form("")):
    from ..license_key import public_status
    from ..user_openai import save_tts_key

    _require_user(request)
    lang = i18n.get_ui_lang(request)
    try:
        # verify=True: ping OpenAI — key sai/mang loi khong luu.
        save_tts_key(key)
    except ValueError as e:
        code = str(e)
        msg_key = (
            "openai_key_unreachable"
            if code == "openai_key_unreachable"
            else "openai_translate_key_invalid"
        )
        raise HTTPException(400, i18n.t(lang, msg_key)) from e
    return {"ok": True, **public_status(user=auth.current_user(request))}


@app.post("/api/deepseek-key")
def api_deepseek_key(request: Request, key: str = Form("")):
    from ..license_key import public_status
    from ..user_deepseek import save_deepseek_key

    _require_user(request)
    lang = i18n.get_ui_lang(request)
    try:
        save_deepseek_key(key)
    except ValueError as e:
        code = str(e)
        msg_key = (
            "deepseek_key_unreachable"
            if code == "deepseek_key_unreachable"
            else "deepseek_key_invalid"
        )
        raise HTTPException(400, i18n.t(lang, msg_key)) from e
    return {"ok": True, **public_status(user=auth.current_user(request))}


@app.post("/api/qwen-key")
def api_qwen_key(request: Request, key: str = Form("")):
    from ..license_key import public_status
    from ..user_qwen import save_qwen_key

    _require_user(request)
    lang = i18n.get_ui_lang(request)
    try:
        save_qwen_key(key)
    except ValueError as e:
        code = str(e)
        msg_key = (
            "qwen_key_unreachable"
            if code == "qwen_key_unreachable"
            else "qwen_key_invalid"
        )
        raise HTTPException(400, i18n.t(lang, msg_key)) from e
    return {"ok": True, **public_status(user=auth.current_user(request))}


@app.post("/api/tvoice-key")
def api_tvoice_key(request: Request, key: str = Form("")):
    from ..license_key import public_status
    from ..user_tvoice import save_tvoice_key

    _require_user(request)
    try:
        save_tvoice_key(key)
    except ValueError as e:
        code = str(e)
        msg = "API key T-Voice không hợp lệ" if code == "tvoice_key_invalid" else "Không thể kết nối máy chủ T-Voice AI (tvoiceai.com)"
        raise HTTPException(400, msg) from e
    return {"ok": True, **public_status(user=auth.current_user(request))}


@app.get("/api/tvoice-voices")
def api_tvoice_voices(request: Request):
    _require_user(request)
    from ..tvoice import list_tvoice_voices

    return {"voices": list_tvoice_voices()}


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


def _export_dir_ctx() -> str:
    try:
        return str(_load_export_dir())
    except Exception:  # noqa: BLE001
        try:
            return str(_user_downloads())
        except Exception:  # noqa: BLE001
            return ""


@app.get("/", response_class=HTMLResponse)
def index(request: Request, page: int = 1):
    user = auth.current_user(request)
    if not user:
        return _page(request, "landing.html", user=None)
    languages = list_languages()
    ui = i18n.get_ui_lang(request)
    lang_ids = {x["id"] for x in languages}
    default_dub = "vi" if "vi" in lang_ids else (ui if ui in lang_ids else "en")
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

    from ..genre_profiles import list_genres, load_genre_template
    from ..tvoice import list_tvoice_voices

    genres = list_genres()
    genre_templates = {g["id"]: load_genre_template(g["id"]) for g in genres}

    return _page(
        request, "index.html",
        user=user,
        languages=languages,
        tts_choices=list_tts_choices(),
        tvoice_voices=list_tvoice_voices(),
        genres=genres,
        genre_templates=genre_templates,
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
        export_dir=_export_dir_ctx(),
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


@app.get("/merge", response_class=HTMLResponse)
def merge_page(request: Request):
    """Trang ghep nhieu video thanh 1 video."""
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    from .export_delivery import load_merge_export_dir
    merge_dir = str(load_merge_export_dir())
    return _page(request, "merge.html", user=user, merge_export_dir=merge_dir)


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    jobs = models.list_jobs(
        user_id=user["id"],
        limit=1000,
        statuses=(
            models.STATUS_RUNNING,
            models.STATUS_QUEUED,
            models.STATUS_FAILED,
            models.STATUS_CANCELLED,
        ),
    )
    queued_count = sum(1 for j in jobs if j.get("status") == models.STATUS_QUEUED)
    q_idx = 0
    for j in jobs:
        if j.get("status") == models.STATUS_QUEUED:
            q_idx += 1
            j["queue_pos"] = q_idx
            j["queue_total"] = queued_count

    return _page(request, "queue.html", user=user, jobs=jobs)


@app.get("/library", response_class=HTMLResponse)
def library_page(request: Request, page: int = 1, tab: str = "jobs"):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    cfg = _web_cfg()
    from ..video_editor import job_output_dir
    try:
        models.sync_existing_merged_files(job_output_dir(cfg))
    except Exception:
        pass

    merge_history = models.list_merge_history(limit=100)
    merge_total = len(merge_history)

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

    import datetime
    for m in merge_history:
        ts = m.get("created_at") or 0
        if ts > 0:
            m["created_at_fmt"] = datetime.datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
        else:
            m["created_at_fmt"] = ""
        sz = m.get("file_size") or 0
        if sz >= 1024 * 1024 * 1024:
            m["size_fmt"] = f"{sz / (1024*1024*1024):.1f} GB"
        elif sz >= 1024 * 1024:
            m["size_fmt"] = f"{sz / (1024*1024):.1f} MB"
        elif sz >= 1024:
            m["size_fmt"] = f"{sz / 1024:.0f} KB"
        else:
            m["size_fmt"] = f"{sz} B"
        m["has_file"] = Path(m.get("output_path") or "").is_file()

    return _page(
        request, "library.html",
        user=user,
        videos=videos,
        merge_videos=merge_history,
        merge_total=merge_total,
        tab=tab,
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
    export_dir = _export_dir_ctx()
    return _page(
        request, "job.html",
        user=user,
        job_id=job_id,
        stages=i18n.stages_for(i18n.get_ui_lang(request)),
        export_dir=export_dir,
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


@app.get("/api/genres")
def api_genres():
    from ..genre_profiles import list_genres, load_genre_template

    genres = list_genres()
    return {
        "genres": genres,
        "templates": {g["id"]: load_genre_template(g["id"]) for g in genres},
    }


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
    if tts_cfg.get("needs_tvoice_key"):
        from ..user_tvoice import has_tvoice_key

        if not has_tvoice_key():
            raise HTTPException(400, "Vui lòng nhập T-Voice API Key trước khi nghe thử.")
    from ..voiceover import synthesize_preview


    preset = resolve_preset(lang, voice)
    vid = preset["id"]
    safe_vid = re.sub(r"[^a-zA-Z0-9_-]", "_", vid)
    engine = tts_cfg["tts_engine"]
    model = tts_cfg["openai_model"]
    cache = Path("work") / "voice_previews" / f"{lang}_{safe_vid}_{engine}_{model}.mp3"
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
        filename=f"preview_{lang}_{safe_vid}_{model}.mp3",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.post("/api/quote")
def api_quote(
    request: Request,
    url: str = Form(...),
    channel_mode: str = Form("count"),
    channel_count: str = Form("40"),
):
    """Uoc tinh credit / liet ke kenh de chon (1 tham so url — tu nhan dien)."""
    _require_user(request)
    cfg = _web_cfg()
    dl_cfg = dict(cfg.get("download") or {})
    ch_lim = _channel_fetch_limit(channel_mode, channel_count)
    try:
        result = quote.quote_urls(
            url, dl_cfg, limit=max(_batch_limit(), ch_lim), channel_limit=ch_lim,
            allow_chrome=False,
        )
    except Exception as e:  # noqa: BLE001
        from ..dl_errors import DyDownloadError, classify_download_error

        err = e if isinstance(e, DyDownloadError) else classify_download_error(e)
        if err.code == DyDownloadError.NOT_FOUND:
            status = 404
        elif err.code == DyDownloadError.INVALID_URL:
            status = 400
        elif err.code == DyDownloadError.EMPTY_CHANNEL:
            status = 404
        else:
            status = 400
        raise HTTPException(status, err.to_dict()) from e
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
        # Giu ten goc (so tap Han/Viet) — chi thay ky tu cam Windows
        safe = "".join(
            ch if (ch.isalnum() or ch in "-._ " or "\u4e00" <= ch <= "\u9fff") else "_"
            for ch in stem
        )
        safe = re.sub(r"\s+", " ", safe).strip(" ._") or "video"
        safe = safe[:80]
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
    videos: list[UploadFile] | None = File(None),
    channel_mode: str = Form("count"),
    channel_count: str = Form("40"),
    quoted_json: str = Form(""),
    quoted_urls: str = Form(""),
    chrome_fallback: str = Form(""),
    translate_engine: str = Form(""),
    genre_id: str = Form(""),
    genre_prompt: str = Form(""),
    voice_mode: str = Form("single"),
    anti_copyright_mode: str = Form("standard"),
    ac_mirror: str = Form(""),
    ac_noise: str = Form(""),
    ac_color: str = Form(""),
    ac_vignette: str = Form(""),
    ac_blur_sub: str = Form(""),
    ac_blur_sub_mode: str = Form("bottom"),
    ac_pitch: str = Form(""),
    ac_zoom: str = Form(""),
    ac_speed: str = Form(""),
    export_dir: str = Form(""),
):
    """Tao 1 hoac nhieu job: link Douyin (ke ca full kenh), hoac video local."""
    user = _require_user(request)
    if models.is_blocked_user(user):
        raise HTTPException(403, "Tai khoan bi khoa")
    lang = (lang or "").strip()
    voice = (voice or "").strip()
    tts = (tts or "").strip()
    from ..translate_llm import resolve_translate_engine

    te = resolve_translate_engine(translate_engine)
    if te not in {"google", "openai", "deepseek", "qwen"}:
        te = "google"
    ui_lang = i18n.get_ui_lang(request)
    if not (genre_id or "").strip():
        genre_id = "do_thi"
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
    if te == "qwen":
        from ..user_qwen import has_qwen_key, verify_qwen_key
        from ..llm_provider import qwen_key

        key = (qwen_key() or "").strip()
        if not key and not has_qwen_key():
            raise HTTPException(400, i18n.t(ui_lang, "qwen_key_needed"))
        if key:
            try:
                verify_qwen_key(key)
            except ValueError as e:
                msg_key = (
                    "qwen_key_unreachable"
                    if str(e) == "qwen_key_unreachable"
                    else "qwen_key_invalid"
                )
                raise HTTPException(400, i18n.t(ui_lang, msg_key)) from e
    elif te == "deepseek":
        from ..user_deepseek import has_deepseek_key, verify_deepseek_key
        from ..llm_provider import deepseek_key

        key = (deepseek_key() or "").strip()
        if not key and not has_deepseek_key():
            raise HTTPException(400, i18n.t(ui_lang, "deepseek_key_needed"))
        if key:
            try:
                verify_deepseek_key(key)
            except ValueError as e:
                msg_key = (
                    "deepseek_key_unreachable"
                    if str(e) == "deepseek_key_unreachable"
                    else "deepseek_key_invalid"
                )
                raise HTTPException(400, i18n.t(ui_lang, msg_key)) from e
    elif te == "openai" or tts_cfg.get("needs_openai_key"):
        from ..user_openai import has_tts_key, verify_openai_key
        from ..preflight import openai_key

        key = (openai_key() or "").strip()
        if not key and not has_tts_key():
            key_msg = (
                "openai_translate_key_invalid"
                if te == "openai"
                else "openai_tts_key_needed"
            )
            raise HTTPException(400, i18n.t(ui_lang, key_msg))
        # Key cu / het han: chan Start, khong de job chay roi moi fail
        if key:
            try:
                verify_openai_key(key)
            except ValueError as e:
                msg_key = (
                    "openai_key_unreachable"
                    if str(e) == "openai_key_unreachable"
                    else "openai_translate_key_invalid"
                )
                raise HTTPException(400, i18n.t(ui_lang, msg_key)) from e

    if tts_cfg.get("needs_tvoice_key"):
        from ..user_tvoice import has_tvoice_key

        if not has_tvoice_key():
            raise HTTPException(400, "Vui lòng nhập API Key T-Voice AI trước khi bắt đầu tạo video.")



    cfg = _web_cfg()
    from ..preflight import assert_job_ready
    try:
        assert_job_ready(cfg, {"voiceover": {"tts_engine": tts_cfg["tts_engine"]}})
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except NameError as e:
        raise HTTPException(400, f"Pre-flight loi cau hinh: {e}") from e

    limit = _batch_limit()
    ch_lim = _channel_fetch_limit(channel_mode, channel_count)
    upload_list = videos if isinstance(videos, list) else ([videos] if videos is not None else [])
    uploads = [v for v in upload_list if v is not None and (v.filename or "").strip()]
    local_paths = _save_local_videos(uploads, limit=max(limit, ch_lim)) if uploads else []

    # Probe do dai + tinh credit (cung logic /api/quote) — xu ly full clip theo so credit
    dl_cfg = dict(cfg.get("download") or {})
    quoted_items: list[dict] = []
    room = max(limit, ch_lim) - len(local_paths)
    # Luon dung bang gia UI — Start khong liet ke kenh / mo Chrome lan 2.
    reused = _items_from_quoted_json(quoted_json, limit=room)
    if not reused:
        reused = _items_from_quoted_json(quoted_urls, limit=room)
    if reused:
        quoted_items = reused
        log.info("Tao job: tai su dung %d clip da bao gia", len(reused))
    elif (url or "").strip() and room > 0:
        from ..pipeline import is_channel_url

        if is_channel_url(url):
            raise HTTPException(
                400,
                "Hay chon video trong danh sach kenh truoc khi Start "
                "(Preview → tick video → Start).",
            )
        try:
            quoted = quote.quote_urls(
                url, dl_cfg, limit=room, channel_limit=min(ch_lim, room),
                allow_chrome=False,
            )
        except Exception as e:  # noqa: BLE001
            from ..dl_errors import DyDownloadError, classify_download_error

            err = e if isinstance(e, DyDownloadError) else classify_download_error(e)
            status = 404 if err.code == DyDownloadError.NOT_FOUND else 400
            raise HTTPException(status, err.to_dict()) from e
        if str(quoted.get("kind") or "") == "channel":
            raise HTTPException(
                400,
                "Hay chon video trong danh sach kenh truoc khi Start.",
            )
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
        "translate_engine": te,
        "genre_id": genre_id,
        "genre_prompt": genre_prompt,
        "voice_mode": voice_mode,
        "anti_copyright_mode": anti_copyright_mode,
        "ac_mirror": ac_mirror,
        "ac_noise": ac_noise,
        "ac_color": ac_color,
        "ac_vignette": ac_vignette,
        "ac_blur_sub": ac_blur_sub,
        "ac_blur_sub_mode": ac_blur_sub_mode,
        "ac_pitch": ac_pitch,
        "ac_zoom": ac_zoom,
        "ac_speed": ac_speed,
        "export_dir": str(export_dir or "").strip(),
    })
    # Chi bat Chrome khi user dong y (form chrome_fallback=1) — khong tu dong.
    if str(chrome_fallback or "").strip().lower() in {"1", "true", "yes", "on"}:
        opts["chrome_fallback"] = True
    if str(export_dir or "").strip():
        opts["export_dir"] = str(export_dir).strip()
    opts_json = json.dumps(opts, ensure_ascii=False)

    ref = f"job-batch:{user['id']}:{int(time.time())}:{len(sources)}"
    trial = _trial_for_user(user)
    try:
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
    except Exception as e:  # noqa: BLE001
        log.exception("create_jobs_with_trial")
        raise HTTPException(500, f"Khong ghi duoc job vao database: {e}") from e
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


def _job_public(job: dict, include_queue_pos: bool = True, include_qa: bool = True) -> dict:
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
        "has_output": _job_has_output_file(job) if job.get("status") == models.STATUS_COMPLETED else False,
        "source_url": job.get("source_url") or "",
        "source_title": (job.get("source_title") or "").strip(),
        "target_lang": job.get("target_lang") or "",
        "voice": job.get("voice") or "",
        "preset_name": "classic",
        "created_at": job.get("created_at"),
        "qa": None,
        "queue_pos": job.get("queue_pos") or 0,
        "queue_total": job.get("queue_total") or 0,
    }
    raw_opts = job.get("output_opts_json")
    if raw_opts:
        try:
            opts = json.loads(raw_opts)
            if isinstance(opts, dict):
                data["preset_name"] = opts.get("preset_name") or "classic"
                data["output_opts"] = opts
                data["export_dir"] = str(opts.get("export_dir") or "").strip()
        except (TypeError, json.JSONDecodeError):
            pass
    if include_qa:
        raw_qa = job.get("qa_json")
        if raw_qa:
            try:
                data["qa"] = json.loads(raw_qa)
            except (TypeError, json.JSONDecodeError):
                data["qa"] = None
    if include_queue_pos and job["status"] == models.STATUS_QUEUED and not data["queue_pos"]:
        pos, total = models.queue_position(job["id"])
        data["queue_pos"] = pos
        data["queue_total"] = total
    return data


@app.get("/api/jobs")
def api_list_jobs(
    request: Request,
    active_only: bool = False,
    include_completed: bool = False,
    limit: int = 500,
):
    user = _require_user(request)
    user_id = user["id"]
    running_count = models.count_user_jobs(user_id, models.STATUS_RUNNING)
    queued_count = models.count_user_jobs(user_id, models.STATUS_QUEUED)

    if active_only:
        raw_jobs = models.list_jobs(
            user_id=user_id,
            statuses=(models.STATUS_RUNNING, models.STATUS_QUEUED),
            limit=limit,
        )
    elif include_completed:
        raw_jobs = models.list_jobs(user_id=user_id, limit=limit)
    else:
        # Default for queue page: running, queued, failed, cancelled
        raw_jobs = models.list_jobs(
            user_id=user_id,
            statuses=(
                models.STATUS_RUNNING,
                models.STATUS_QUEUED,
                models.STATUS_FAILED,
                models.STATUS_CANCELLED,
            ),
            limit=limit,
        )

    q_idx = 0
    results = []
    for j in raw_jobs:
        if j.get("status") == models.STATUS_QUEUED:
            q_idx += 1
            j["queue_pos"] = q_idx
            j["queue_total"] = queued_count
        results.append(_job_public(j, include_queue_pos=False, include_qa=False))
    return {
        "jobs": results,
        "running_count": running_count,
        "queued_count": queued_count,
        "active_count": running_count + queued_count,
    }


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
    raw = str(job.get("output_path") or "").strip()
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw).expanduser())
    # Fallback: file theo job id trong output/ (truong hop DB mat path / path cu).
    jid = str(job.get("id") or "").strip()
    if jid:
        for root in _output_roots():
            candidates.append(root / f"{jid}.mp4")
            candidates.append(root / "output" / f"{jid}.mp4")
            # formats khac "source" → {jid}_9x16.mp4 ...
            try:
                for p in root.glob(f"{jid}_*.mp4"):
                    candidates.append(p)
                out_sub = root / "output"
                if out_sub.is_dir():
                    for p in out_sub.glob(f"{jid}_*.mp4"):
                        candidates.append(p)
            except OSError:
                pass
    seen: set[str] = set()
    for cand in candidates:
        try:
            out = cand.expanduser().resolve()
        except OSError:
            continue
        key = str(out).lower()
        if key in seen:
            continue
        seen.add(key)
        if not out.is_file() or out.stat().st_size <= 0:
            continue
        if any(_is_relative_to(out, r) for r in _output_roots()):
            # Heal DB neu path trong job rong / sai nhung file van con.
            if str(job.get("output_path") or "").strip() != str(out):
                try:
                    models.set_output_path(jid, str(out))
                    job["output_path"] = str(out)
                except Exception:  # noqa: BLE001
                    pass
            return out
    if not raw:
        raise HTTPException(409, "Chua co file xuat")
    raise HTTPException(404, "File khong ton tai")


def _job_has_output_file(job: dict) -> bool:
    if job.get("status") != models.STATUS_COMPLETED:
        return False
    try:
        _job_output_file(job)
        return True
    except HTTPException:
        return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_export_name(title: str, job_id: str) -> str:
    from .export_delivery import safe_export_name

    return safe_export_name(title, job_id)


def _user_downloads() -> Path:
    from .export_delivery import user_downloads

    return user_downloads()


def _load_export_dir() -> Path:
    from .export_delivery import load_export_dir

    return load_export_dir()


def _export_prefs_path() -> Path:
    from .export_delivery import export_prefs_path

    return export_prefs_path()


def _save_export_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        pref = _export_prefs_path()
        pref.write_text(
            json.dumps({"dir": str(path)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("save export prefs: %s", e)
    return path


def _pick_folder_win_native(initial: str) -> str | None:
    """Dialog chon thu muc kieu File Explorer (IFileOpenDialog + FOS_PICKFOLDERS).

    Khong mo Terminal, khong dung FolderBrowserDialog (cay thu muc cu).
    """
    import ctypes
    from ctypes import HRESULT, POINTER, byref, c_void_p, c_wchar_p, windll, wintypes

    ole32 = windll.ole32
    shell32 = windll.shell32

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

        @classmethod
        def from_str(cls, text: str) -> "GUID":
            import uuid

            u = uuid.UUID(text)
            g = cls()
            g.Data1 = int(u.time_low)
            g.Data2 = int(u.time_mid)
            g.Data3 = int(u.time_hi_version)
            for i, b in enumerate(u.bytes[8:]):
                g.Data4[i] = b
            return g

    CLSID_FileOpenDialog = GUID.from_str("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
    IID_IFileOpenDialog = GUID.from_str("D57C7288-D4AD-4768-BE02-9D969532D960")
    IID_IShellItem = GUID.from_str("43826D1E-E718-42EE-BC55-A1E261C37BFE")

    CLSCTX_INPROC_SERVER = 0x1
    COINIT_APARTMENTTHREADED = 0x2
    FOS_PICKFOLDERS = 0x20
    FOS_FORCEFILESYSTEM = 0x40
    SIGDN_FILESYSPATH = 0x80058000
    S_OK = 0

    ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoCreateInstance.argtypes = [
        POINTER(GUID), c_void_p, wintypes.DWORD, POINTER(GUID), POINTER(c_void_p),
    ]
    ole32.CoCreateInstance.restype = HRESULT
    shell32.SHCreateItemFromParsingName.argtypes = [
        c_wchar_p, c_void_p, POINTER(GUID), POINTER(c_void_p),
    ]
    shell32.SHCreateItemFromParsingName.restype = HRESULT

    hr_init = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    com_owned = hr_init in (0, 1)  # S_OK / S_FALSE
    dialog = c_void_p()
    try:
        hr = ole32.CoCreateInstance(
            byref(CLSID_FileOpenDialog),
            None,
            CLSCTX_INPROC_SERVER,
            byref(IID_IFileOpenDialog),
            byref(dialog),
        )
        if hr != S_OK or not dialog.value:
            log.warning("IFileOpenDialog CoCreateInstance hr=0x%08x", hr & 0xFFFFFFFF)
            return None

        vtbl = ctypes.cast(
            ctypes.cast(dialog, POINTER(c_void_p))[0],
            POINTER(c_void_p),
        )

        def _fn(idx, *argtypes, restype=HRESULT):
            proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
            return proto(vtbl[idx])

        # IUnknown: 0 QI, 1 AddRef, 2 Release
        # IModalWindow: 3 Show
        # IFileDialog: ... 9 SetOptions, 11 SetDefaultFolder, 12 SetFolder, 17 SetTitle, 20 GetResult
        Show = _fn(3, c_void_p)
        SetOptions = _fn(9, wintypes.DWORD)
        GetOptions = _fn(10, POINTER(wintypes.DWORD))
        SetFolder = _fn(12, c_void_p)
        SetTitle = _fn(17, c_wchar_p)
        GetResult = _fn(20, POINTER(c_void_p))
        Release = _fn(2, restype=wintypes.ULONG)

        opts = wintypes.DWORD()
        GetOptions(dialog, byref(opts))
        SetOptions(dialog, opts.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM)
        SetTitle(dialog, "Chon thu muc luu video MP4")

        init = (initial or "").strip()
        if init and Path(init).is_dir():
            item = c_void_p()
            hr_item = shell32.SHCreateItemFromParsingName(
                init, None, byref(IID_IShellItem), byref(item),
            )
            if hr_item == S_OK and item.value:
                SetFolder(dialog, item)
                # Release shell item
                item_vtbl = ctypes.cast(
                    ctypes.cast(item, POINTER(c_void_p))[0],
                    POINTER(c_void_p),
                )
                ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(item_vtbl[2])(item)

        # Owner HWND: dung GetForegroundWindow hoac 0
        owner = wintypes.HWND(0)
        try:
            hwnd = windll.user32.GetForegroundWindow()
            if hwnd:
                owner = wintypes.HWND(hwnd)
        except Exception:  # noqa: BLE001
            pass

        hr = Show(dialog, owner)
        if hr != S_OK:
            Release(dialog)
            dialog.value = None
            if (hr & 0xFFFFFFFF) == 0x800704C7:
                return ""  # Nguoi dung bam Cancel / Dong dialog
            return None  # COM error

        result = c_void_p()
        hr = GetResult(dialog, byref(result))
        if hr != S_OK or not result.value:
            Release(dialog)
            dialog.value = None
            return ""

        res_vtbl = ctypes.cast(
            ctypes.cast(result, POINTER(c_void_p))[0],
            POINTER(c_void_p),
        )
        GetDisplayName = ctypes.WINFUNCTYPE(
            HRESULT, c_void_p, wintypes.DWORD, POINTER(c_wchar_p),
        )(res_vtbl[5])  # IShellItem::GetDisplayName
        ReleaseItem = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(res_vtbl[2])

        name_ptr = c_wchar_p()
        hr = GetDisplayName(result, SIGDN_FILESYSPATH, byref(name_ptr))
        path = None
        if hr == S_OK and name_ptr.value:
            path = str(name_ptr.value)
            windll.ole32.CoTaskMemFree(name_ptr)
        ReleaseItem(result)
        Release(dialog)
        dialog.value = None
        if path and Path(path).is_dir():
            return path
        return ""
    except Exception as e:  # noqa: BLE001
        log.warning("native folder dialog: %s", e)
        return None
    finally:
        if dialog and dialog.value:
            try:
                vtbl = ctypes.cast(
                    ctypes.cast(dialog, POINTER(c_void_p))[0],
                    POINTER(c_void_p),
                )
                ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtbl[2])(dialog)
            except Exception:  # noqa: BLE001
                pass
        if com_owned:
            try:
                ole32.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass


def _run_in_sta_thread(func, *args, **kwargs):
    """Chay ham dialog Win32 COM tren 1 STA thread moi hoan toan de dam bao thanh cong 100%."""
    import threading

    res = [None]
    err = [None]

    def _worker():
        try:
            res[0] = func(*args, **kwargs)
        except Exception as e:
            err[0] = e

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if err[0]:
        raise err[0]
    return res[0]


def _pick_folder_win_powershell(initial: str) -> str | None:
    """Fallback cu (FolderBrowser) — chi dung khi native COM va Tkinter deu that bai."""
    init = initial or str(_user_downloads())
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$f = New-Object System.Windows.Forms.FolderBrowserDialog
$f.Description = 'Chon thu muc luu video MP4'
$f.ShowNewFolderButton = $true
$init = @'
{init}
'@
$init = $init.Trim()
if ($init -and (Test-Path -LiteralPath $init)) {{ $f.SelectedPath = $init }}
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'FixedToolWindow'
$owner.StartPosition = 'Manual'
$owner.Location = New-Object System.Drawing.Point(-32000, -32000)
$owner.Size = New-Object System.Drawing.Size(1, 1)
[void]$owner.Show()
try {{
  $r = $f.ShowDialog($owner)
  if ($r -eq [System.Windows.Forms.DialogResult]::OK -and $f.SelectedPath) {{
    [Console]::Out.WriteLine($f.SelectedPath)
  }}
}} finally {{
  $owner.Close(); $owner.Dispose()
}}
"""
    tmp = None
    try:
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix="vc_folder_", suffix=".ps1")
        os.close(fd)
        Path(tmp).write_text(script, encoding="utf-8-sig")
        no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        r = subprocess.run(
            [
                "powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass", "-File", tmp,
            ],
            capture_output=True,
            text=True,
            timeout=300,
            creationflags=no_win,
        )
        out = (r.stdout or "").strip().splitlines()
        if out and out[-1].strip():
            chosen = out[-1].strip()
            if Path(chosen).is_dir():
                return chosen
    except Exception as e:  # noqa: BLE001
        log.warning("powershell folder dialog: %s", e)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return None


def _pick_folder_dialog(initial: str = "") -> str | None:
    """Mo dialog chon thu muc (desktop)."""
    init = initial or str(_user_downloads())
    if sys.platform == "win32":
        chosen = _run_in_sta_thread(_pick_folder_win_native, init)
        if chosen == "":
            return None  # Nguoi dung bam Cancel / Dong dialog -> khong fallback PowerShell!
        if chosen:
            return chosen
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        chosen = filedialog.askdirectory(initialdir=init, mustexist=True)
        root.destroy()
        if chosen:
            return str(chosen)
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("tk folder dialog: %s", e)
        if sys.platform == "win32":
            return _pick_folder_win_powershell(init)
        return None


def _pick_files_win_native(initial: str = "") -> list[str] | None:
    """IFileOpenDialog (FOS_ALLOWMULTISELECT) — hien thi dialog chon nhieu file Explorer chuan Windows 10/11."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import byref, c_void_p, POINTER, c_wchar_p, HRESULT, wintypes, windll, c_uint

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

        @classmethod
        def from_str(cls, text: str) -> "GUID":
            import uuid

            u = uuid.UUID(text)
            g = cls()
            g.Data1 = int(u.time_low)
            g.Data2 = int(u.time_mid)
            g.Data3 = int(u.time_hi_version)
            for i, b in enumerate(u.bytes[8:]):
                g.Data4[i] = b
            return g

    class COMDLG_FILTERSPEC(ctypes.Structure):
        _fields_ = [
            ("pszName", c_wchar_p),
            ("pszSpec", c_wchar_p),
        ]

    CLSID_FileOpenDialog = GUID.from_str("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
    IID_IFileOpenDialog = GUID.from_str("D57C7288-D4AD-4768-BE02-9D969532D960")
    IID_IShellItem = GUID.from_str("43826D1E-E718-42EE-BC55-A1E261C37BFE")

    CLSCTX_INPROC_SERVER = 0x1
    COINIT_APARTMENTTHREADED = 0x2
    FOS_ALLOWMULTISELECT = 0x00000200
    FOS_FILEMUSTEXIST = 0x00001000
    FOS_FORCEFILESYSTEM = 0x40
    SIGDN_FILESYSPATH = 0x80058000
    S_OK = 0

    ole32 = windll.ole32
    shell32 = windll.shell32

    ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoCreateInstance.argtypes = [
        POINTER(GUID), c_void_p, wintypes.DWORD, POINTER(GUID), POINTER(c_void_p),
    ]
    ole32.CoCreateInstance.restype = HRESULT
    shell32.SHCreateItemFromParsingName.argtypes = [
        c_wchar_p, c_void_p, POINTER(GUID), POINTER(c_void_p),
    ]
    shell32.SHCreateItemFromParsingName.restype = HRESULT

    hr_init = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    com_owned = hr_init in (0, 1)  # S_OK / S_FALSE
    dialog = c_void_p()
    paths: list[str] = []
    try:
        hr = ole32.CoCreateInstance(
            byref(CLSID_FileOpenDialog),
            None,
            CLSCTX_INPROC_SERVER,
            byref(IID_IFileOpenDialog),
            byref(dialog),
        )
        if hr != S_OK or not dialog.value:
            log.warning("IFileOpenDialog CoCreateInstance hr=0x%08x", hr & 0xFFFFFFFF)
            return None

        vtbl = ctypes.cast(
            ctypes.cast(dialog, POINTER(c_void_p))[0],
            POINTER(c_void_p),
        )

        def _fn(idx, *argtypes, restype=HRESULT):
            proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
            return proto(vtbl[idx])

        Show = _fn(3, c_void_p)
        SetFileTypes = _fn(4, c_uint, POINTER(COMDLG_FILTERSPEC))
        SetOptions = _fn(9, wintypes.DWORD)
        GetOptions = _fn(10, POINTER(wintypes.DWORD))
        SetFolder = _fn(12, c_void_p)
        SetTitle = _fn(17, c_wchar_p)
        GetResults = _fn(27, POINTER(c_void_p))
        Release = _fn(2, restype=wintypes.ULONG)

        opts = wintypes.DWORD()
        GetOptions(dialog, byref(opts))
        SetOptions(dialog, opts.value | FOS_ALLOWMULTISELECT | FOS_FILEMUSTEXIST | FOS_FORCEFILESYSTEM)
        SetTitle(dialog, "Chọn các file video để ghép")

        filters = (COMDLG_FILTERSPEC * 2)(
            COMDLG_FILTERSPEC("Video files (*.mp4;*.mov;*.mkv;*.webm;*.avi;*.m4v)", "*.mp4;*.mov;*.mkv;*.webm;*.avi;*.m4v"),
            COMDLG_FILTERSPEC("All files (*.*)", "*.*"),
        )
        SetFileTypes(dialog, 2, filters)

        init = (initial or "").strip()
        if init and Path(init).is_dir():
            folder_item = c_void_p()
            hr_item = shell32.SHCreateItemFromParsingName(
                init, None, byref(IID_IShellItem), byref(folder_item),
            )
            if hr_item == S_OK and folder_item.value:
                SetFolder(dialog, folder_item)
                item_vtbl = ctypes.cast(
                    ctypes.cast(folder_item, POINTER(c_void_p))[0],
                    POINTER(c_void_p),
                )
                ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(item_vtbl[2])(folder_item)

        owner = wintypes.HWND(0)
        try:
            hwnd = windll.user32.FindWindowW(None, "VideosCreator")
            if hwnd:
                owner = wintypes.HWND(hwnd)
        except Exception:  # noqa: BLE001
            pass

        hr = Show(dialog, owner)
        if hr != S_OK:
            Release(dialog)
            dialog.value = None
            return []  # user cancelled

        item_array = c_void_p()
        hr = GetResults(dialog, byref(item_array))
        if hr != S_OK or not item_array.value:
            Release(dialog)
            dialog.value = None
            return []

        arr_vtbl = ctypes.cast(
            ctypes.cast(item_array, POINTER(c_void_p))[0],
            POINTER(c_void_p),
        )
        GetCount = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.DWORD))(arr_vtbl[7])
        GetItemAt = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, POINTER(c_void_p))(arr_vtbl[8])
        ReleaseArray = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(arr_vtbl[2])

        count = wintypes.DWORD(0)
        if GetCount(item_array, byref(count)) == S_OK:
            for idx in range(count.value):
                item = c_void_p()
                if GetItemAt(item_array, idx, byref(item)) == S_OK and item.value:
                    it_vtbl = ctypes.cast(
                        ctypes.cast(item, POINTER(c_void_p))[0],
                        POINTER(c_void_p),
                    )
                    GetDisplayName = ctypes.WINFUNCTYPE(
                        HRESULT, c_void_p, wintypes.DWORD, POINTER(c_wchar_p)
                    )(it_vtbl[5])
                    ReleaseItem = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(it_vtbl[2])
                    name_ptr = c_wchar_p()
                    if GetDisplayName(item, SIGDN_FILESYSPATH, byref(name_ptr)) == S_OK and name_ptr.value:
                        p_str = str(name_ptr.value)
                        windll.ole32.CoTaskMemFree(name_ptr)
                        if Path(p_str).is_file():
                            paths.append(p_str)
                    ReleaseItem(item)

        ReleaseArray(item_array)
        Release(dialog)
        dialog.value = None
        return paths
    except Exception as e:  # noqa: BLE001
        log.warning("native files dialog: %s", e)
        return None
    finally:
        if dialog and dialog.value:
            try:
                vtbl = ctypes.cast(
                    ctypes.cast(dialog, POINTER(c_void_p))[0],
                    POINTER(c_void_p),
                )
                ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtbl[2])(dialog)
            except Exception:  # noqa: BLE001
                pass
        if com_owned:
            try:
                ole32.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass


def _pick_files_win_powershell(initial: str = "") -> list[str]:
    """Fallback chon nhieu file qua PowerShell khi native COM / Tkinter khong kha dung."""
    init = initial or str(_user_downloads())
    script = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Multiselect = $true
$d.Filter = 'Video files (*.mp4;*.mov;*.mkv;*.webm;*.avi;*.m4v)|*.mp4;*.mov;*.mkv;*.webm;*.avi;*.m4v|All files (*.*)|*.*'
$d.Title = 'Chọn các file video để ghép'
$init = @'
{init}
'@
$init = $init.Trim()
if ($init -and (Test-Path -LiteralPath $init)) {{ $d.InitialDirectory = $init }}
try {{
  $r = $d.ShowDialog()
  if ($r -eq [System.Windows.Forms.DialogResult]::OK) {{
    $d.FileNames | ForEach-Object {{ [Console]::Out.WriteLine($_) }}
  }}
}} finally {{
  $d.Dispose()
}}
"""
    tmp = None
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="vc_files_", suffix=".ps1")
        os.close(fd)
        Path(tmp).write_text(script, encoding="utf-8-sig")
        no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        r = subprocess.run(
            [
                "powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass", "-File", tmp,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            creationflags=no_win,
        )
        lines = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
        return [p for p in lines if Path(p).is_file()]
    except Exception as e:
        log.warning("powershell files dialog: %s", e)
        return []
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _pick_video_files_dialog(initial: str = "") -> list[str]:
    """Mo dialog chon nhieu file video tren Windows (native COM -> Tkinter -> PowerShell)."""
    init = initial or str(_user_downloads())
    if sys.platform == "win32":
        chosen = _run_in_sta_thread(_pick_files_win_native, init)
        if chosen is not None:
            return chosen
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        chosen = filedialog.askopenfilenames(
            initialdir=init,
            title="Chọn các file video để ghép",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        if chosen:
            return [str(p) for p in chosen if Path(p).is_file()]
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("tk files dialog: %s", e)
    return _pick_files_win_powershell(init)


def _mostly_cjk(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    letters = sum(1 for c in s if c.isalpha() or ("\u4e00" <= c <= "\u9fff"))
    if letters <= 0:
        return False
    return cjk >= max(3, letters // 3)


def _job_export_title(job: dict) -> str:
    """Ten file xuat: uu tien ten file goc (local), roi source_title khong CJK."""
    try:
        from .export_delivery import title_from_source_path

        from_src = title_from_source_path(str(job.get("source_url") or ""))
        if from_src:
            return from_src
    except Exception:  # noqa: BLE001
        pass
    title = str(job.get("source_title") or "").strip()
    if title and not _mostly_cjk(title):
        return title
    # Job cu: thu doc script da dich neu work chua bi xoa
    try:
        from ..video_editor import job_work_dir

        work = job_work_dir(job["id"], _web_cfg())
        for name in ("script_auto_vi.txt", "script_auto.txt"):
            p = work / name
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8").strip()
            if text:
                from ..main import _filename_title_from_text

                got = _filename_title_from_text(text)
                if got and not _mostly_cjk(got):
                    return got
    except Exception:  # noqa: BLE001
        pass
    if title:
        return title
    return ""


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


def _is_desktop_or_win() -> bool:
    from ..desktop_mode import enabled as desktop_on
    return desktop_on() or sys.platform == "win32"


@app.get("/api/desktop/save-prefs")
def api_save_prefs_get(request: Request):
    _require_user(request)
    default = _user_downloads()
    d = _load_export_dir() if _is_desktop_or_win() else default
    # Seed prefs lan dau de UI luon co duong dan cu the (khong de o trong).
    try:
        if _is_desktop_or_win() and not _export_prefs_path().is_file():
            _save_export_dir(d)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "dir": str(d), "default_dir": str(default)}


@app.post("/api/desktop/save-prefs")
async def api_save_prefs_set(request: Request):
    if not _is_desktop_or_win():
        raise HTTPException(400, "Chi dung tren app desktop")
    user = _require_user(request)
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    raw = str((body or {}).get("dir") or "").strip()
    if not raw:
        raise HTTPException(400, "Thieu duong dan thu muc")
    p = Path(raw)
    if not p.is_dir():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"Khong tao duoc thu muc: {e}") from e
    d = _save_export_dir(p)
    try:
        models.update_queued_jobs_export_dir(user["id"], str(d))
    except Exception as e:
        log.warning("Khong cap nhat duoc hang cho voi thu muc moi: %s", e)
    return {"ok": True, "dir": str(d)}


@app.post("/api/desktop/pick-folder")
async def api_pick_folder(request: Request):
    import asyncio

    if not _is_desktop_or_win():
        raise HTTPException(400, "Chi dung tren app desktop")
    _require_user(request)
    initial = str(_load_export_dir())
    # Chay dialog o thread rieng — khong khoa event loop / worker khac.
    chosen = await asyncio.to_thread(_pick_folder_dialog, initial)
    if not chosen:
        return {"ok": False, "cancelled": True, "dir": initial}
    d = _save_export_dir(Path(chosen))
    return {"ok": True, "dir": str(d)}


@app.get("/api/desktop/merge-save-prefs")
def api_merge_save_prefs_get(request: Request):
    from .export_delivery import load_merge_export_dir, user_downloads

    _require_user(request)
    default = user_downloads() / "Merged_Videos"
    d = load_merge_export_dir() if _is_desktop_or_win() else default
    return {"ok": True, "dir": str(d), "default_dir": str(default)}


@app.post("/api/desktop/merge-save-prefs")
async def api_merge_save_prefs_set(request: Request):
    from .export_delivery import save_merge_export_dir

    if not _is_desktop_or_win():
        raise HTTPException(400, "Chi dung tren app desktop")
    _require_user(request)
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    raw = str((body or {}).get("dir") or "").strip()
    if not raw:
        raise HTTPException(400, "Thieu duong dan thu muc")
    p = Path(raw)
    if not p.is_dir():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"Khong tao duoc thu muc: {e}") from e
    d = save_merge_export_dir(p)
    return {"ok": True, "dir": str(d)}


@app.post("/api/desktop/merge-pick-folder")
async def api_merge_pick_folder(request: Request):
    import asyncio
    from .export_delivery import load_merge_export_dir, save_merge_export_dir

    if not _is_desktop_or_win():
        raise HTTPException(400, "Chi dung tren app desktop")
    _require_user(request)
    initial = str(load_merge_export_dir())
    chosen = await asyncio.to_thread(_pick_folder_dialog, initial)
    if not chosen:
        return {"ok": False, "cancelled": True, "dir": initial}
    d = save_merge_export_dir(Path(chosen))
    return {"ok": True, "dir": str(d)}


@app.post("/api/desktop/pick-video-files")
async def api_pick_video_files(request: Request):
    """Mo cua so Windows chon nhieu file video truc tiep tren may."""
    import asyncio

    if not _is_desktop_or_win():
        raise HTTPException(400, "Chi dung tren app desktop")
    _require_user(request)
    from .export_delivery import load_merge_export_dir
    initial = str(load_merge_export_dir())
    chosen_files = await asyncio.to_thread(_pick_video_files_dialog, initial)
    if not chosen_files:
        return {"ok": False, "cancelled": True, "files": []}
    files = [
        {"path": str(p), "name": Path(p).name, "size": Path(p).stat().st_size if Path(p).is_file() else 0}
        for p in chosen_files
    ]
    return {"ok": True, "files": files}


@app.post("/api/desktop/pick-video-folder")
async def api_pick_video_folder(request: Request):
    """Mo cua so Windows chon thu muc va tu dong lay tat ca file video ben trong."""
    import asyncio

    if not _is_desktop_or_win():
        raise HTTPException(400, "Chi dung tren app desktop")
    _require_user(request)
    from .export_delivery import load_merge_export_dir
    initial = str(load_merge_export_dir())
    chosen = await asyncio.to_thread(_pick_folder_dialog, initial)
    if not chosen:
        return {"ok": False, "cancelled": True, "files": []}
    p_chosen = Path(chosen)
    valid_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
    found_files = []
    try:
        for sub in sorted(p_chosen.iterdir()):
            if sub.is_file() and sub.suffix.lower() in valid_exts:
                found_files.append({
                    "path": str(sub),
                    "name": sub.name,
                    "size": sub.stat().st_size,
                })
    except Exception as e:
        log.warning("Loi quet folder video: %s", e)
    return {"ok": True, "dir": str(p_chosen), "files": found_files}


@app.get("/api/desktop/update/status")
def api_update_status(request: Request):
    """Lay phien ban hien tai va trang thai ban va."""
    _require_user(request)
    from ..version import get_version_info

    return {"ok": True, **get_version_info()}


@app.post("/api/desktop/update/check")
async def api_update_check(request: Request):
    """Kiem tra xem co ban cap nhat moi tren may chu hay khong."""
    _require_user(request)
    from ..updater import check_update

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    url = (body or {}).get("url") or ""
    return check_update(url)


@app.post("/api/desktop/update/apply")
async def api_update_apply(request: Request):
    """Tai va ap dung ban va tu patch_url."""
    _require_user(request)
    from ..updater import apply_patch_url

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    url = (body or {}).get("patch_url") or ""
    if not url:
        raise HTTPException(400, "Thieu duong dan patch_url")
    res = apply_patch_url(url)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "Cap nhat that bai")
    return res


@app.post("/api/desktop/update/upload-patch")
async def api_update_upload_patch(request: Request, patch_file: UploadFile = File(...)):
    """Ap dung ban va tu file .zip duoc upload truc tiep."""
    _require_user(request)
    import tempfile
    from ..updater import apply_patch_file

    if not patch_file.filename or not patch_file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "File ban va phai co dinh dang .zip")

    fd, tmp_path = tempfile.mkstemp(prefix="user_patch_", suffix=".zip")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(patch_file.file, f)
        res = apply_patch_file(Path(tmp_path))
        if not res.get("ok"):
            raise HTTPException(400, res.get("error") or "Loi ap dung ban va")
        return res
    finally:
        if Path(tmp_path).is_file():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@app.post("/api/desktop/update/rollback")
def api_update_rollback(request: Request):
    """Khoi phuc ve phien ban goc ban dau."""
    _require_user(request)
    from ..updater import rollback_patch

    return rollback_patch()


@app.post("/api/desktop/update/restart")
def api_update_restart(request: Request):
    """Khoi dong lai ung dung ngay lap tuc."""
    _require_user(request)
    import threading
    from ..updater import restart_application

    threading.Timer(0.5, restart_application).start()
    return {"ok": True, "message": "Dang khoi dong lai..."}


@app.get("/api/jobs/{job_id}/export-meta")
def api_export_meta(request: Request, job_id: str):
    """Ten file goi y + thu muc mac dinh (cho Save picker / UI)."""
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    # Cho phep meta khi da co output (completed) — khong bat buoc file con ton tai.
    name = _safe_export_name(_job_export_title(job), job_id)
    try:
        out = _job_output_file(job)
        size = out.stat().st_size if out.is_file() else 0
        ready = out.is_file()
    except HTTPException:
        out = None
        size = 0
        ready = False
    return {
        "ok": True,
        "filename": name,
        "ready": ready,
        "size": size,
        "dir": str(_load_export_dir()),
        "path": str(out) if out else "",
    }


@app.get("/api/jobs/{job_id}/download")
def api_download(request: Request, job_id: str, dl: int = 0):
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    out = _job_output_file(job)
    name = _safe_export_name(_job_export_title(job), job_id)
    # dl=1: bat buoc download ve may; khong co dl: xem inline (the video) — KHONG tu luu.
    return FileResponse(
        out,
        media_type="video/mp4",
        filename=name,
        content_disposition_type="attachment" if dl else "inline",
    )


@app.post("/api/jobs/{job_id}/export-local")
async def api_export_local(request: Request, job_id: str):
    """App desktop: copy MP4 vao thu muc user chon (mac dinh Downloads). Chi khi bam Download."""
    from ..desktop_mode import enabled as desktop_on

    if not desktop_on():
        raise HTTPException(400, "Chi dung tren app desktop")
    user = _require_user(request)
    job = models.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "Khong tim thay job")
    src = _job_output_file(job)
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    dest_raw = str((body or {}).get("dir") or "").strip()
    if dest_raw:
        dest_dir = _save_export_dir(Path(dest_raw))
    else:
        dest_dir = None
        raw_opts = job.get("output_opts_json") or "{}"
        try:
            job_opts = json.loads(raw_opts)
            job_dir = str(job_opts.get("export_dir") or "").strip()
            if job_dir and (Path(job_dir).is_dir() or Path(job_dir).parent.is_dir()):
                dest_dir = Path(job_dir)
        except Exception:
            pass
        if not dest_dir:
            dest_dir = _load_export_dir()
    name = _safe_export_name(_job_export_title(job), job_id)
    dest = dest_dir / name
    if not (dest.exists() and dest.stat().st_size == src.stat().st_size):
        stem = dest.stem
        n = 2
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}.mp4"
            n += 1
        try:
            shutil.copy2(src, dest)
        except OSError as e:
            raise HTTPException(500, f"Khong copy duoc file: {e}") from e
        if not dest.is_file() or dest.stat().st_size <= 0:
            raise HTTPException(500, "Copy xong nhung file rong / khong ton tai")
    try:
        models.mark_downloaded(job_id)
    except Exception:  # noqa: BLE001
        pass
    # Mo Explorer o thread rieng — khong chan response.
    try:
        threading.Thread(target=_reveal_file, args=(dest,), daemon=True).start()
    except Exception:  # noqa: BLE001
        _reveal_file(dest)
    return {"ok": True, "path": str(dest), "dir": str(dest_dir), "filename": dest.name}


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


@app.post("/api/merge")
async def api_merge_videos(
    request: Request,
    videos: list[UploadFile] = File(...),
    aspect_ratio: str = Form("first"),
    fit: str = Form("contain"),
    fast_copy: str = Form("1"),
    save_folder: str = Form(""),
    async_mode: str = Form("1"),
):
    """Ghep nhieu video thanh 1 file MP4 duy nhat (ho tro chay ngam background)."""
    user = _require_user(request)
    if not videos or len(videos) < 2:
        raise HTTPException(400, "Vui lòng chọn ít nhất 2 video để ghép.")

    from .merge_manager import merge_mgr
    if merge_mgr.is_busy():
        raise HTTPException(
            400,
            "Hiện đang có một tiến trình ghép video đang chạy ngầm. Vui lòng chờ hoàn thành hoặc bấm 'Hủy' trước khi bắt đầu ghép bộ mới.",
        )

    import tempfile
    import uuid
    from ..utils import ensure_media_bins
    from ..video_editor import job_output_dir
    from ..video_merger import merge_videos

    ensure_media_bins()

    task_id = f"merge_{uuid.uuid4().hex[:8]}"
    cfg = _web_cfg()
    out_folder = None
    if (save_folder or "").strip():
        try:
            out_folder = Path(save_folder.strip()).resolve()
            out_folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            out_folder = None
    if not out_folder:
        from .export_delivery import load_merge_export_dir
        out_folder = load_merge_export_dir()
        out_folder.mkdir(parents=True, exist_ok=True)

    # Đặt thư mục temp cùng ổ đĩa với out_folder để tránh tràn ổ C: khi người dùng chọn ổ D, E
    try:
        work_parent = out_folder / ".dyflow_tmp"
        work_parent.mkdir(parents=True, exist_ok=True)
        work_dir = work_parent / task_id
        work_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        work_dir = Path(tempfile.gettempdir()) / "dyflow_merge" / task_id
        work_dir.mkdir(parents=True, exist_ok=True)

    # Tự động dọn dẹp các thư mục temp cũ quá 1 giờ
    try:
        now = time.time()
        for p in (out_folder / ".dyflow_tmp", Path(tempfile.gettempdir()) / "dyflow_merge"):
            if p.is_dir():
                for sub in p.iterdir():
                    if sub.is_dir() and (now - sub.stat().st_mtime) > 3600:
                        shutil.rmtree(sub, ignore_errors=True)
    except Exception:
        pass

    # Kiểm tra dung lượng trống của ổ đĩa trước khi ghi
    try:
        disk_info = shutil.disk_usage(out_folder)
        free_mb = disk_info.free / (1024 * 1024)
        est_upload_mb = sum(getattr(f, "size", 0) or 0 for f in videos) / (1024 * 1024)
        needed_mb = max(300.0, est_upload_mb * 1.5)
        if disk_info.free < (needed_mb * 1024 * 1024):
            drive_name = out_folder.drive or str(out_folder)
            raise HTTPException(
                400,
                f"Ổ đĩa {drive_name} không đủ dung lượng trống (chỉ còn {free_mb:.0f} MB, cần khoảng {needed_mb:.0f} MB để xử lý {len(videos)} video). "
                f"Vui lòng dọn dẹp ổ đĩa hoặc bấm 'Đổi thư mục...' để chọn ổ đĩa khác (D:, E:...).",
            )
    except HTTPException:
        raise
    except Exception:
        pass

    saved_inputs: list[Path] = []
    is_async = str(async_mode).lower() in ("1", "true", "yes")
    try:
        import re
        for idx, file in enumerate(videos):
            orig_name = Path(file.filename or f"clip_{idx}.mp4").name
            clean_stem = re.sub(r'[\'"\\/<>:*?|]+', "_", Path(orig_name).stem) or f"clip_{idx}"
            suffix = Path(orig_name).suffix or ".mp4"
            safe_name = f"{idx:04d}_{clean_stem}{suffix}"
            dest = work_dir / safe_name
            with dest.open("wb") as buf:
                shutil.copyfileobj(file.file, buf)
            saved_inputs.append(dest)

        out_name = f"merged_{time.strftime('%Y%m%d_%H%M%S')}_{task_id[-4:]}.mp4"
        out_path = out_folder / out_name

        if is_async:
            video_meta = [
                {
                    "name": getattr(f, "filename", None) or Path(dest).name,
                    "size": getattr(f, "size", 0) or (dest.stat().st_size if dest.is_file() else 0),
                }
                for f, dest in zip(videos, saved_inputs)
            ]
            merge_mgr.start_task(
                task_id=task_id,
                saved_inputs=saved_inputs,
                out_path=out_path,
                out_folder=out_folder,
                work_dir=work_dir,
                aspect_ratio=aspect_ratio,
                fit=fit,
                fast_copy=str(fast_copy).lower() in ("1", "true", "yes"),
                total_videos=len(saved_inputs),
                video_items=video_meta,
            )
            return {
                "ok": True,
                "async": True,
                "task_id": task_id,
                "status": "running",
                "message": f"Đã đưa {len(saved_inputs)} video vào tiến trình ghép ngầm",
            }

        merged = merge_videos(
            saved_inputs,
            out_path,
            aspect_ratio=aspect_ratio,
            fit=fit,
            fast_copy=str(fast_copy).lower() in ("1", "true", "yes"),
        )

        import urllib.parse
        folder_param = f"?folder={urllib.parse.quote_plus(str(out_folder))}"
        return {
            "ok": True,
            "filename": out_name,
            "path": str(merged),
            "size": merged.stat().st_size,
            "download_url": f"/api/merge/download/{out_name}{folder_param}",
        }
    except HTTPException:
        raise
    except OSError as e:
        is_nospace = (
            getattr(e, "errno", None) == 28
            or getattr(e, "winerror", None) == 112
            or "No space left" in str(e)
            or "disk full" in str(e).lower()
        )
        if is_nospace:
            drive_name = out_folder.drive or str(out_folder)
            raise HTTPException(
                400,
                f"Ổ đĩa {drive_name} đã bị đầy (hết dung lượng lưu trữ [Errno 28]). "
                f"Vui lòng dọn dẹp file rác hoặc bấm 'Đổi thư mục...' để chọn ổ đĩa khác (D:, E:...) còn trống bộ nhớ.",
            ) from e
        if getattr(e, "winerror", None) == 206 or "WinError 206" in str(e) or "filename or extension is too long" in str(e).lower():
            raise HTTPException(
                400,
                "Đường dẫn thư mục hoặc danh sách video quá dài so với giới hạn Windows ([WinError 206]). "
                "Vui lòng bấm 'Đổi thư mục...' để chọn một thư mục có đường dẫn ngắn gọn hơn (ví dụ D:\\Videos).",
            ) from e
        log.exception("Loi ghep video: %s", e)
        raise HTTPException(500, f"Lỗi ghép video: {e}") from e
    except Exception as e:
        if "No space left" in str(e) or "disk full" in str(e).lower():
            drive_name = out_folder.drive or str(out_folder)
            raise HTTPException(
                400,
                f"Ổ đĩa {drive_name} đã bị đầy (hết dung lượng lưu trữ [Errno 28]). "
                f"Vui lòng dọn dẹp file rác hoặc bấm 'Đổi thư mục...' để chọn ổ đĩa khác (D:, E:...) còn trống bộ nhớ.",
            ) from e
        log.exception("Loi ghep video: %s", e)
        raise HTTPException(500, f"Lỗi ghép video: {e}") from e
    finally:
        if not is_async:
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass


@app.post("/api/merge/start-local")
async def api_merge_start_local(request: Request):
    """Bat dau ghep video ngay lap tuc tu file tren may desktop ma khong can upload qua HTTP."""
    _require_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    paths_raw = body.get("paths") or []
    if not paths_raw or len(paths_raw) < 2:
        raise HTTPException(400, "Vui lòng chọn ít nhất 2 video để ghép.")

    save_folder = (body.get("save_folder") or "").strip()
    valid_paths: list[Path] = []
    seen = set()

    candidate_dirs: list[Path] = []
    if save_folder:
        try:
            cand = Path(save_folder).resolve()
            if cand.is_dir():
                candidate_dirs.append(cand)
                if cand.parent and cand.parent.is_dir() and cand.parent != cand:
                    candidate_dirs.append(cand.parent)
            elif cand.parent and cand.parent.is_dir():
                candidate_dirs.append(cand.parent)
        except Exception:
            pass

    try:
        exp_dir = _load_export_dir()
        if exp_dir and exp_dir.is_dir() and exp_dir not in candidate_dirs:
            candidate_dirs.append(exp_dir)
            if exp_dir.parent and exp_dir.parent.is_dir() and exp_dir.parent != exp_dir:
                candidate_dirs.append(exp_dir.parent)
    except Exception:
        pass

    for p_str in paths_raw:
        try:
            p = Path(p_str)
            if p.is_file():
                candidate_dirs.append(p.parent.resolve())
                break
        except Exception:
            pass

    for p_str in paths_raw:
        if not p_str:
            continue
        try:
            p = Path(p_str)
            resolved = None
            if p.is_file():
                resolved = p.resolve()
            else:
                for c_dir in candidate_dirs:
                    cand1 = c_dir / p_str
                    if cand1.is_file():
                        resolved = cand1.resolve()
                        break
                    cand2 = c_dir / p.name
                    if cand2.is_file():
                        resolved = cand2.resolve()
                        break
            if resolved and str(resolved) not in seen:
                seen.add(str(resolved))
                valid_paths.append(resolved)
        except Exception:
            continue

    if len(valid_paths) < 2:
        raise HTTPException(
            400,
            f"Không tìm thấy đủ file video hợp lệ trên máy tính (chỉ tìm thấy {len(valid_paths)}/{len(paths_raw)} file). "
            f"Vui lòng bấm 'Chọn cả thư mục' hoặc bấm 'Đổi thư mục...' để chọn đúng thư mục chứa video.",
        )

    from .merge_manager import merge_mgr
    if merge_mgr.is_busy():
        st = merge_mgr.get_status()
        if st.get("elapsed_seconds", 0) > 7200:
            merge_mgr.clear_task()
        else:
            raise HTTPException(
                400,
                "Hiện đang có một tiến trình ghép video đang chạy ngầm. Vui lòng chờ hoàn thành hoặc bấm 'Hủy' trước khi bắt đầu ghép bộ mới.",
            )

    import tempfile, uuid
    from ..utils import ensure_media_bins
    from ..video_editor import job_output_dir
    ensure_media_bins()

    task_id = f"merge_{uuid.uuid4().hex[:8]}"
    cfg = _web_cfg()
    save_folder = (body.get("save_folder") or "").strip()
    out_folder = None
    if save_folder:
        try:
            out_folder = Path(save_folder).resolve()
            out_folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            out_folder = None
    if not out_folder:
        from .export_delivery import load_merge_export_dir
        out_folder = load_merge_export_dir()
        out_folder.mkdir(parents=True, exist_ok=True)

    try:
        work_parent = out_folder / ".dyflow_tmp"
        work_parent.mkdir(parents=True, exist_ok=True)
        work_dir = work_parent / task_id
        work_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        work_dir = Path(tempfile.gettempdir()) / "dyflow_merge" / task_id
        work_dir.mkdir(parents=True, exist_ok=True)

    out_name = f"merged_{time.strftime('%Y%m%d_%H%M%S')}_{task_id[-4:]}.mp4"
    out_path = out_folder / out_name

    video_meta = [
        {"name": p.name, "size": p.stat().st_size}
        for p in valid_paths
    ]
    merge_mgr.start_task(
        task_id=task_id,
        saved_inputs=valid_paths,
        out_path=out_path,
        out_folder=out_folder,
        work_dir=work_dir,
        aspect_ratio=body.get("aspect_ratio") or "first",
        fit=body.get("fit") or "contain",
        fast_copy=str(body.get("fast_copy", "1")).lower() in ("1", "true", "yes"),
        total_videos=len(valid_paths),
        video_items=video_meta,
    )
    return {
        "ok": True,
        "async": True,
        "task_id": task_id,
        "status": "running",
        "message": f"Đã đưa {len(valid_paths)} video vào tiến trình ghép ngầm",
    }


@app.get("/api/merge/status")
def api_merge_status(request: Request, task_id: str = ""):
    """Kiem tra tien do va ket qua cua tien trinh ghep video chay ngam."""
    _require_user(request)
    from .merge_manager import merge_mgr
    return merge_mgr.get_status(task_id or None)


@app.post("/api/merge/cancel")
def api_merge_cancel(request: Request):
    """Huy tien trinh ghep video dang chay ngam."""
    _require_user(request)
    from .merge_manager import merge_mgr
    ok = merge_mgr.cancel_active()
    return {"ok": ok}


@app.post("/api/merge/clear")
def api_merge_clear(request: Request, task_id: str = ""):
    """Xoa trang thai tien trinh da hoan thanh/that bai de reset man hinh."""
    _require_user(request)
    from .merge_manager import merge_mgr
    merge_mgr.clear_task(task_id or None)
    return {"ok": True}


@app.post("/api/merge/reveal")
async def api_merge_reveal(request: Request):
    """Mo thu muc chua video da ghep trong File Explorer."""
    _require_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    path_str = body.get("path") or ""
    filename = body.get("filename") or ""

    target: Path | None = None
    if path_str and Path(path_str).is_file():
        target = Path(path_str)
    elif filename:
        cfg = _web_cfg()
        from ..video_editor import job_output_dir
        from .export_delivery import load_merge_export_dir
        candidates = [
            load_merge_export_dir() / Path(filename).name,
            job_output_dir(cfg) / Path(filename).name,
            _load_export_dir() / Path(filename).name,
            Path("output") / Path(filename).name,
        ]
        for c in candidates:
            if c.is_file():
                target = c
                break

    if target and target.is_file():
        try:
            threading.Thread(target=_reveal_file, args=(target,), daemon=True).start()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "Không tìm thấy file video"}


@app.get("/api/merge/download/{filename}")
def api_merge_download(request: Request, filename: str, folder: str = "", dl: int = 0):
    """Tai ve hoac stream video da ghep."""
    from ..video_editor import job_output_dir
    from .export_delivery import load_merge_export_dir

    safe_name = Path(filename).name
    cfg = _web_cfg()
    candidates: list[Path] = []
    if folder and Path(folder).is_dir():
        candidates.append(Path(folder) / safe_name)
    candidates.append(load_merge_export_dir() / safe_name)
    candidates.append(job_output_dir(cfg) / safe_name)
    try:
        candidates.append(_load_export_dir() / safe_name)
        candidates.append(_user_downloads() / safe_name)
    except Exception:
        pass
    candidates.append(Path("output") / safe_name)

    target: Path | None = None
    for cand in candidates:
        if cand.is_file():
            target = cand
            break

    if not target or not target.is_file():
        raise HTTPException(404, "Không tìm thấy video đã ghép.")
    if dl:
        return FileResponse(
            target,
            media_type="video/mp4",
            filename=safe_name,
            content_disposition_type="attachment",
        )
    return FileResponse(
        target,
        media_type="video/mp4",
        content_disposition_type="inline",
    )


@app.get("/api/merge/history")
def api_merge_history(request: Request, limit: int = 50):
    """Lay danh sach lich su cac video da ghep thanh cong."""
    _require_user(request)
    cfg = _web_cfg()
    from ..video_editor import job_output_dir
    try:
        models.sync_existing_merged_files(job_output_dir(cfg))
    except Exception:
        pass
    items = models.list_merge_history(limit=limit)
    import datetime
    for m in items:
        ts = m.get("created_at") or 0
        if ts > 0:
            m["created_at_fmt"] = datetime.datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
        else:
            m["created_at_fmt"] = ""
        sz = m.get("file_size") or 0
        if sz >= 1024 * 1024 * 1024:
            m["size_fmt"] = f"{sz / (1024*1024*1024):.1f} GB"
        elif sz >= 1024 * 1024:
            m["size_fmt"] = f"{sz / (1024*1024):.1f} MB"
        elif sz >= 1024:
            m["size_fmt"] = f"{sz / 1024:.0f} KB"
        else:
            m["size_fmt"] = f"{sz} B"
        m["has_file"] = Path(m.get("output_path") or "").is_file()
    return {"ok": True, "items": items}


@app.delete("/api/merge/history/{mid}")
def api_merge_history_delete(request: Request, mid: str):
    """Xoa mot muc khoi lich su ghep video."""
    _require_user(request)
    ok = models.delete_merge_history(mid)
    return {"ok": ok}
