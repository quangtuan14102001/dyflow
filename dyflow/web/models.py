"""SQLite store cho web app: users, sessions, jobs, credit ledger, payments.

Dung sqlite3 chuan (khong ORM). Cac thao tac credit chay trong transaction
de so du + so cai (ledger) luon nhat quan.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DB_PATH = Path("work") / "dyflow.db"


def _resolve_db_path() -> Path:
    """Desktop: luon dung AppData/work/dyflow.db (khong phu thuoc cwd)."""
    try:
        from ..desktop_mode import data_dir, enabled

        if enabled():
            return data_dir() / "work" / "dyflow.db"
    except Exception:  # noqa: BLE001
        pass
    return Path("work") / "dyflow.db"


def _connect() -> sqlite3.Connection:
    global DB_PATH
    DB_PATH = _resolve_db_path()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# Trang thai job
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


def max_concurrent_per_user(default: int = 2) -> int:
    """Gioi han job running dong thoi moi user (env ghi de config)."""
    env = os.getenv("DYFLOW_MAX_CONCURRENT_PER_USER")
    if env and env.isdigit():
        return max(1, int(env))
    return max(1, int(default))

# Loai giao dich credit
TX_TOPUP = "topup"       # nap qua crypto
TX_CHARGE = "charge"     # tru khi tao job
TX_REFUND = "refund"     # hoan khi job that bai
TX_BONUS = "bonus"       # tang khi dang ky
TX_ADMIN = "admin"       # admin cong/tru tay

# Trang thai thanh toan (nap tay)
PAY_WAITING = "waiting"       # cho user chuyen tien
PAY_SUBMITTED = "submitted"   # user da gui TX hash, cho admin duyet
PAY_FINISHED = "finished"
PAY_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    credits       INTEGER NOT NULL DEFAULT 0,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS password_resets (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    user_id      TEXT,
    source_url   TEXT NOT NULL,
    voice        TEXT NOT NULL,
    target_lang  TEXT NOT NULL DEFAULT 'vi',
    status       TEXT NOT NULL DEFAULT 'queued',
    stage        TEXT NOT NULL DEFAULT 'queued',
    progress     INTEGER NOT NULL DEFAULT 0,
    message      TEXT NOT NULL DEFAULT '',
    credits_cost INTEGER NOT NULL DEFAULT 0,
    refunded     INTEGER NOT NULL DEFAULT 0,
    output_path  TEXT,
    downloaded   INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL,
    output_opts_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS credit_tx (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    amount        INTEGER NOT NULL,   -- +nap/hoan, -tru
    type          TEXT NOT NULL,
    balance_after INTEGER NOT NULL,
    ref           TEXT,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    package_id   TEXT NOT NULL,
    credits      INTEGER NOT NULL,
    amount_usd   REAL NOT NULL,
    provider     TEXT NOT NULL,
    invoice_id   TEXT,
    tx_hash      TEXT,
    note         TEXT,
    status       TEXT NOT NULL DEFAULT 'waiting',
    credited     INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    paid_at      REAL
);

CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tx_user ON credit_tx(user_id);
CREATE INDEX IF NOT EXISTS idx_pay_user ON payments(user_id);

CREATE TABLE IF NOT EXISTS telegram_link_tokens (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()}
    if "tx_hash" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN tx_hash TEXT")
    if "note" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN note TEXT")

    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "target_lang" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN target_lang TEXT NOT NULL DEFAULT 'vi'")
    if "tts_engine" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN tts_engine TEXT NOT NULL DEFAULT 'edge'")
    if "openai_model" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN openai_model TEXT NOT NULL DEFAULT 'tts-1'")
    if "qa_json" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN qa_json TEXT")
    if "output_opts_json" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN output_opts_json TEXT NOT NULL DEFAULT '{}'")
    if "source_title" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN source_title TEXT NOT NULL DEFAULT ''")
    if "downloaded" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN downloaded INTEGER NOT NULL DEFAULT 0")

    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "telegram_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN telegram_id TEXT")
    if "trial_started_at" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN trial_started_at REAL")
    if "trial_ends_at" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN trial_ends_at REAL")
    if "is_super_admin" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_super_admin INTEGER NOT NULL DEFAULT 0")
    if "blocked" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram "
        "ON users(telegram_id) WHERE telegram_id IS NOT NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs(user_id, status)")


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()


def recover_stale(*, include_queued: bool = False) -> int:
    """Job con 'running' (va queued neu desktop) tu process cu -> that bai + hoan credit."""
    n = 0
    statuses: tuple[str, ...] = (STATUS_RUNNING,)
    if include_queued:
        statuses = (STATUS_RUNNING, STATUS_QUEUED)
    with _connect() as conn:
        qmarks = ",".join("?" * len(statuses))
        rows = conn.execute(
            f"SELECT id, user_id, credits_cost, refunded FROM jobs WHERE status IN ({qmarks})",
            statuses,
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?",
                (STATUS_FAILED, "Bi gian doan (dong app / khoi dong lai)", time.time(), r["id"]),
            )
            if r["user_id"] and r["credits_cost"] and not r["refunded"]:
                _refund_locked(conn, r["user_id"], r["id"], r["credits_cost"])
            n += 1
        conn.commit()
    return n


# ----------------------------- users / auth -----------------------------

def create_user(email: str, password_hash: str, bonus: int = 0) -> dict:
    uid = f"u_{uuid.uuid4().hex[:16]}"
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, credits, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, email.lower().strip(), password_hash, bonus, now),
        )
        if bonus > 0:
            conn.execute(
                "INSERT INTO credit_tx (id, user_id, amount, type, balance_after, ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"tx_{uuid.uuid4().hex[:16]}", uid, bonus, TX_BONUS, bonus, "signup", now),
            )
        conn.commit()
    return {"id": uid, "email": email.lower().strip(), "credits": bonus}


DESKTOP_EMAIL = "desktop@local"


def ensure_desktop_owner() -> dict:
    """User local cho app desktop: khong tru credit. Role theo license key."""
    from . import auth as _auth

    existing = get_user_by_email(DESKTOP_EMAIL)
    if existing is None:
        existing = create_user(
            DESKTOP_EMAIL,
            _auth.hash_password(secrets.token_urlsafe(24)),
            bonus=99999,
        )
    apply_desktop_license_role(existing["id"])
    user = get_user(existing["id"])
    if user is None:
        raise RuntimeError("Khong tao duoc user desktop")
    return user


def apply_desktop_license_role(user_id: str | None = None) -> None:
    """Gan is_admin / is_super_admin theo key (user / admin / super)."""
    from ..desktop_mode import enabled as desktop_on
    from ..license_key import license_role_or_dev

    if not desktop_on():
        return
    uid = user_id
    if not uid:
        row = get_user_by_email(DESKTOP_EMAIL)
        if row is None:
            return
        uid = row["id"]
    role = license_role_or_dev()
    is_admin = 1 if role in ("admin", "super") else 0
    is_super = 1 if role == "super" else 0
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET is_admin=?, is_super_admin=?, credits=99999 WHERE id=?",
            (is_admin, is_super, uid),
        )
        conn.commit()


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email=?", (email.lower().strip(),)
        ).fetchone()
    return dict(row) if row else None


def get_user(user_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_session(user_id: str, ttl_days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now, now + ttl_days * 86400),
        )
        conn.commit()
    return token


def get_session_user(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token=? AND s.expires_at > ?",
            (token, time.time()),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()


def update_password(user_id: str, password_hash: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
        # Vo hieu hoa session cu
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        conn.commit()


def create_password_reset(user_id: str, ttl_minutes: int = 60) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _connect() as conn:
        # Xoa token cu cua user
        conn.execute("DELETE FROM password_resets WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO password_resets (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now, now + ttl_minutes * 60),
        )
        conn.commit()
    return token


def get_password_reset(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM password_resets WHERE token=? AND used_at IS NULL AND expires_at > ?",
            (token, time.time()),
        ).fetchone()
    return dict(row) if row else None


def consume_password_reset(token: str) -> Optional[str]:
    """Danh dau token da dung, tra ve user_id. None neu het han/khong hop le."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute(
            "SELECT * FROM password_resets WHERE token=? AND used_at IS NULL AND expires_at > ?",
            (token, time.time()),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE password_resets SET used_at=? WHERE token=?",
            (time.time(), token),
        )
        conn.commit()
        return row["user_id"]


# ----------------------------- credit ledger -----------------------------

def _add_tx(conn: sqlite3.Connection, user_id: str, amount: int, tx_type: str,
            balance_after: int, ref: str) -> None:
    conn.execute(
        "INSERT INTO credit_tx (id, user_id, amount, type, balance_after, ref, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"tx_{uuid.uuid4().hex[:16]}", user_id, amount, tx_type, balance_after, ref, time.time()),
    )


def charge_credits(user_id: str, amount: int, ref: str) -> bool:
    """Tru credit neu du. Tra ve True/False (khong du)."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None or row["credits"] < amount:
            conn.commit()
            return False
        new_bal = row["credits"] - amount
        conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, user_id))
        _add_tx(conn, user_id, -amount, TX_CHARGE, new_bal, ref)
        conn.commit()
        return True


def _refund_locked(conn: sqlite3.Connection, user_id: str, job_id: str, amount: int) -> None:
    row = conn.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return
    new_bal = row["credits"] + amount
    conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, user_id))
    _add_tx(conn, user_id, amount, TX_REFUND, new_bal, f"job:{job_id}")
    conn.execute("UPDATE jobs SET refunded=1 WHERE id=?", (job_id,))


def refund_job(job_id: str) -> None:
    """Hoan credit cho job that bai (idempotent nho cot refunded)."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        job = conn.execute(
            "SELECT user_id, credits_cost, refunded FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job and job["user_id"] and job["credits_cost"] and not job["refunded"]:
            _refund_locked(conn, job["user_id"], job_id, job["credits_cost"])
        conn.commit()


def credit_topup(payment_id: str) -> bool:
    """Cong credit tu 1 payment da finished. Idempotent (cot credited)."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        pay = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if pay is None or pay["credited"]:
            conn.commit()
            return False
        row = conn.execute("SELECT credits FROM users WHERE id=?", (pay["user_id"],)).fetchone()
        if row is None:
            conn.commit()
            return False
        new_bal = row["credits"] + pay["credits"]
        conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, pay["user_id"]))
        _add_tx(conn, pay["user_id"], pay["credits"], TX_TOPUP, new_bal, f"pay:{payment_id}")
        conn.execute(
            "UPDATE payments SET status=?, credited=1, paid_at=? WHERE id=?",
            (PAY_FINISHED, time.time(), payment_id),
        )
        conn.commit()
        return True


def list_transactions(
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    offset = max(0, int(offset))
    limit = max(1, min(100, int(limit)))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM credit_tx WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_transactions(user_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM credit_tx WHERE user_id=?", (user_id,)
        ).fetchone()
    return int(row["n"] if row else 0)


# ----------------------------- payments -----------------------------

def create_payment(user_id: str, package_id: str, credits: int,
                   amount_usd: float, provider: str = "manual") -> str:
    pid = f"pay_{uuid.uuid4().hex[:16]}"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO payments (id, user_id, package_id, credits, amount_usd, provider, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, user_id, package_id, credits, amount_usd, provider, PAY_WAITING, time.time()),
        )
        conn.commit()
    return pid


def set_payment_status(payment_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE payments SET status=? WHERE id=?", (status, payment_id))
        conn.commit()


def submit_payment_tx(payment_id: str, user_id: str, tx_hash: str, note: str = "") -> bool:
    """User gui TX hash. Chi chap nhan payment waiting/submitted cua chinh user."""
    tx_hash = tx_hash.strip()
    if len(tx_hash) < 8:
        return False
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE payments SET tx_hash=?, note=?, status=? "
            "WHERE id=? AND user_id=? AND status IN (?, ?) AND credited=0",
            (tx_hash, note.strip()[:500], PAY_SUBMITTED, payment_id, user_id,
             PAY_WAITING, PAY_SUBMITTED),
        )
        conn.commit()
        return cur.rowcount > 0


def get_payment(payment_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    return dict(row) if row else None


def list_payments(
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    offset = max(0, int(offset))
    limit = max(1, min(100, int(limit)))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_payments(user_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM payments WHERE user_id=?", (user_id,)
        ).fetchone()
    return int(row["n"] if row else 0)


def list_pending_payments(limit: int = 100) -> list[dict[str, Any]]:
    """Admin: payment cho duyet (submitted) + moi tao (waiting)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT p.*, u.email AS user_email FROM payments p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.status IN (?, ?) AND p.credited=0 "
            "ORDER BY p.created_at DESC LIMIT ?",
            (PAY_SUBMITTED, PAY_WAITING, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_payments_admin(limit: int = 30, offset: int = 0) -> list[dict[str, Any]]:
    """Admin: payment gan day (moi status)."""
    offset = max(0, int(offset))
    limit = max(1, min(100, int(limit)))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT p.*, u.email AS user_email FROM payments p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def find_credited_by_tx(tx_hash: str) -> Optional[dict[str, Any]]:
    """Tim payment da cong credit bang cung TX (chong dung lai hash)."""
    tx_hash = (tx_hash or "").strip()
    if not tx_hash:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE tx_hash=? AND credited=1 LIMIT 1",
            (tx_hash,),
        ).fetchone()
    return dict(row) if row else None


def reject_payment(payment_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE payments SET status=? WHERE id=? AND credited=0",
            (PAY_FAILED, payment_id),
        )
        conn.commit()


def ensure_admin_email(email: str) -> None:
    """Dat is_admin + is_super_admin cho ADMIN_EMAIL (owner web)."""
    email = (email or "").strip().lower()
    if not email:
        return
    with _connect() as conn:
        row = conn.execute("SELECT id, credits FROM users WHERE email=?", (email,)).fetchone()
        if row is None:
            conn.commit()
            return
        conn.execute(
            "UPDATE users SET is_admin=1, is_super_admin=1 WHERE email=?",
            (email,),
        )
        if int(row["credits"] or 0) < 99999:
            new_bal = 99999
            conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, row["id"]))
            _add_tx(
                conn, row["id"], new_bal - int(row["credits"] or 0),
                TX_BONUS, new_bal, "owner:unlimited",
            )
        conn.commit()


def get_user_by_telegram_id(telegram_id: str | int | None) -> Optional[dict[str, Any]]:
    tid = str(telegram_id or "").strip()
    if not tid:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id=?", (tid,)
        ).fetchone()
    return dict(row) if row else None


def sync_super_admin_telegram(telegram_id: str | int | None) -> None:
    """Neu TELEGRAM_SUPER_ADMIN_ID khop user.telegram_id -> super admin."""
    tid = str(telegram_id or "").strip()
    if not tid:
        return
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE telegram_id=?", (tid,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET is_admin=1, is_super_admin=1 WHERE id=?",
                (row["id"],),
            )
            conn.commit()


def is_owner_user(user: dict | None) -> bool:
    """Admin/owner/desktop: tao video khong tru credit."""
    if not user:
        return False
    email = (user.get("email") or "").strip().lower()
    if email == DESKTOP_EMAIL:
        return True
    return bool(int(user.get("is_admin") or 0) == 1 or int(user.get("is_super_admin") or 0) == 1)


def is_super_admin_user(user: dict | None) -> bool:
    return bool(user and int(user.get("is_super_admin") or 0) == 1)


def is_blocked_user(user: dict | None) -> bool:
    return bool(user and int(user.get("blocked") or 0) == 1)


def list_users(limit: int = 100, offset: int = 0, q: str = "") -> list[dict[str, Any]]:
    offset = max(0, int(offset))
    limit = max(1, min(200, int(limit)))
    q = (q or "").strip().lower()
    with _connect() as conn:
        if q:
            rows = conn.execute(
                "SELECT id, email, credits, is_admin, is_super_admin, blocked, "
                "telegram_id, created_at, trial_ends_at FROM users "
                "WHERE lower(email) LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (f"%{q}%", limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, email, credits, is_admin, is_super_admin, blocked, "
                "telegram_id, created_at, trial_ends_at FROM users "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def count_users(q: str = "") -> int:
    q = (q or "").strip().lower()
    with _connect() as conn:
        if q:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE lower(email) LIKE ?",
                (f"%{q}%",),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"] if row else 0)


def get_user(user_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def admin_adjust_credits(user_id: str, delta: int, ref: str = "admin") -> dict[str, Any] | None:
    """Cong/tru credit (delta co the am). Tra ve user moi hoac None."""
    delta = int(delta)
    if delta == 0:
        return get_user(user_id)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            conn.commit()
            return None
        new_bal = int(row["credits"]) + delta
        if new_bal < 0:
            conn.commit()
            return None
        conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, user_id))
        _add_tx(conn, user_id, delta, TX_ADMIN, new_bal, ref)
        conn.commit()
    return get_user(user_id)


def set_user_admin(user_id: str, is_admin: bool, *, by_super: bool = False) -> bool:
    """Set is_admin. Khong the ha is_super_admin bang lenh nay."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return False
        if int(row["is_super_admin"] or 0) == 1 and not is_admin:
            # khong cho go super admin
            return False
        conn.execute(
            "UPDATE users SET is_admin=? WHERE id=?",
            (1 if is_admin else 0, user_id),
        )
        conn.commit()
    return True


def set_user_blocked(user_id: str, blocked: bool) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT id, is_super_admin FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return False
        if int(row["is_super_admin"] or 0) == 1:
            return False
        conn.execute(
            "UPDATE users SET blocked=? WHERE id=?",
            (1 if blocked else 0, user_id),
        )
        conn.commit()
    return True


def set_admin_by_email(email: str, is_admin: bool) -> dict[str, Any] | None:
    user = get_user_by_email(email)
    if not user:
        return None
    if not set_user_admin(user["id"], is_admin):
        return None
    return get_user(user["id"])


def list_jobs_admin(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Job + email user (cho admin)."""
    offset = max(0, int(offset))
    limit = max(1, min(100, int(limit)))
    with _connect() as conn:
        args: list[Any] = []
        where = ""
        if status:
            where = "WHERE j.status=?"
            args.append(status)
        args.extend([limit, offset])
        rows = conn.execute(
            f"SELECT j.*, u.email AS user_email FROM jobs j "
            f"LEFT JOIN users u ON u.id = j.user_id "
            f"{where} ORDER BY j.created_at DESC LIMIT ? OFFSET ?",
            args,
        ).fetchall()
    return [dict(r) for r in rows]


def requeue_job(job_id: str) -> bool:
    """Cho job failed quay lai queued (khong doi credit)."""
    with _connect() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["status"] != STATUS_FAILED:
            return False
        conn.execute(
            "UPDATE jobs SET status=?, stage=?, progress=?, message=?, error=?, "
            "started_at=NULL, finished_at=NULL WHERE id=?",
            (STATUS_QUEUED, "queued", 0, "Admin retry — dang cho xu ly...", None, job_id),
        )
        conn.commit()
    return True


def admin_stats() -> dict[str, int]:
    with _connect() as conn:
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        jobs_q = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status=?", (STATUS_QUEUED,)
        ).fetchone()["n"]
        jobs_r = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status=?", (STATUS_RUNNING,)
        ).fetchone()["n"]
        jobs_f = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status=?", (STATUS_FAILED,)
        ).fetchone()["n"]
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM payments WHERE status IN (?, ?) AND credited=0",
            (PAY_WAITING, PAY_SUBMITTED),
        ).fetchone()["n"]
    return {
        "users": int(users),
        "jobs_queued": int(jobs_q),
        "jobs_running": int(jobs_r),
        "jobs_failed": int(jobs_f),
        "payments_pending": int(pending),
    }


# ----------------------------- jobs -----------------------------

def _utc_day_bounds(now: Optional[float] = None) -> tuple[float, float]:
    """[start, end) cua ngay UTC hien tai."""
    import datetime as _dt

    ts = now if now is not None else time.time()
    day = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).date()
    start = _dt.datetime(day.year, day.month, day.day, tzinfo=_dt.timezone.utc)
    end = start + _dt.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _trial_active_row(row: sqlite3.Row | dict, now: Optional[float] = None) -> bool:
    if row is None:
        return False
    ends = row["trial_ends_at"] if "trial_ends_at" in row.keys() else None
    if not ends:
        return False
    return float(ends) > (now if now is not None else time.time())


def _free_used_today_locked(conn: sqlite3.Connection, user_id: str, now: Optional[float] = None) -> int:
    start, end = _utc_day_bounds(now)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND credits_cost=0 "
        "AND status!=? AND created_at>=? AND created_at<?",
        (user_id, STATUS_FAILED, start, end),
    ).fetchone()
    return int(row["n"] if row else 0)


def trial_status(user_id: str, *, still_member: bool = True) -> dict[str, Any]:
    """Thong tin trial cho UI. still_member=False -> coi nhu khong con free."""
    from . import billing as _billing

    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return {"active": False, "linked": False}
        linked = bool(row["telegram_id"])
        active = _trial_active_row(row) and still_member and linked
        used = _free_used_today_locked(conn, user_id) if active else 0
        per_day = _billing.TRIAL_VIDEOS_PER_DAY
        remaining = max(0, per_day - used) if active else 0
        return {
            "active": active,
            "linked": linked,
            "telegram_id": row["telegram_id"],
            "trial_started_at": row["trial_started_at"],
            "trial_ends_at": row["trial_ends_at"],
            "videos_per_day": per_day,
            "used_today": used if active else 0,
            "remaining_today": remaining,
            "days": _billing.TRIAL_DAYS,
        }


def create_telegram_link_token(user_id: str, ttl_minutes: int = 30) -> str:
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO telegram_link_tokens (token, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token, user_id, now, now + ttl_minutes * 60),
        )
        conn.commit()
    return token


def activate_trial_from_token(token: str, telegram_id: str | int) -> dict[str, Any]:
    """Bot goi sau khi getChatMember OK. Tra ve {ok, error?, email?, ends_at?}."""
    from . import billing as _billing

    tg = str(telegram_id).strip()
    if not tg:
        return {"ok": False, "error": "missing_telegram_id"}
    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        tok = conn.execute(
            "SELECT * FROM telegram_link_tokens WHERE token=? AND used_at IS NULL AND expires_at>?",
            (token, now),
        ).fetchone()
        if tok is None:
            conn.commit()
            return {"ok": False, "error": "invalid_token"}

        user_id = tok["user_id"]
        other = conn.execute(
            "SELECT id, email FROM users WHERE telegram_id=? AND id!=?",
            (tg, user_id),
        ).fetchone()
        if other is not None:
            conn.commit()
            return {"ok": False, "error": "telegram_taken"}

        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if user is None:
            conn.commit()
            return {"ok": False, "error": "user_missing"}

        ends = user["trial_ends_at"]
        started = user["trial_started_at"]
        if not ends or float(ends) <= now:
            started = now
            ends = now + _billing.TRIAL_DAYS * 86400

        conn.execute(
            "UPDATE users SET telegram_id=?, trial_started_at=?, trial_ends_at=? WHERE id=?",
            (tg, started, ends, user_id),
        )
        conn.execute(
            "UPDATE telegram_link_tokens SET used_at=? WHERE token=?",
            (now, token),
        )
        email = user["email"]
        conn.commit()
    return {
        "ok": True,
        "email": email,
        "trial_ends_at": ends,
        "days": _billing.TRIAL_DAYS,
        "videos_per_day": _billing.TRIAL_VIDEOS_PER_DAY,
    }


def create_jobs_with_trial(
    user_id: str,
    sources: list[str],
    voice: str,
    target_lang: str,
    cost_each: int | list[int],
    ref: str,
    *,
    trial_ok: bool = True,
    tts_engine: str = "edge",
    openai_model: str = "tts-1",
    output_opts_json: str = "{}",
) -> tuple[list[str], list[int]] | None:
    """Tao nhieu job: dung free trial truoc, con lai tru credit.
    Tra ve (job_ids, costs) hoac None neu khong du.

    cost_each: 1 so (moi video bang nhau) hoac list theo tung link.
    Trial free slot chi ap dung khi video can <= 1 credit (clip ngan).
    """
    from . import billing as _billing

    n = len(sources)
    if n <= 0:
        return None
    if isinstance(cost_each, int):
        base_costs = [max(0, int(cost_each))] * n
    else:
        if len(cost_each) != n:
            return None
        base_costs = [max(0, int(c)) for c in cost_each]

    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if user is None:
            conn.commit()
            return None
        if int(user["blocked"] or 0) == 1:
            conn.commit()
            return None

        free_left = 0
        if trial_ok and _trial_active_row(user, now) and user["telegram_id"]:
            used = _free_used_today_locked(conn, user_id, now)
            free_left = max(0, _billing.TRIAL_VIDEOS_PER_DAY - used)

        # Owner/admin: khong tru credit
        owner_free = is_owner_user(dict(user))

        costs: list[int] = []
        free_use = 0
        for c in base_costs:
            if owner_free:
                costs.append(0)
                continue
            # Trial: chi free clip 1 credit (≤2 phut) — tranh abuse clip 30 phut
            if free_use < free_left and c <= _billing.CREDITS_PER_BLOCK:
                costs.append(0)
                free_use += 1
            else:
                costs.append(c)

        paid_cost = sum(costs)
        if not owner_free and user["credits"] < paid_cost:
            conn.commit()
            return None

        if paid_cost > 0:
            new_bal = user["credits"] - paid_cost
            conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, user_id))
            _add_tx(conn, user_id, -paid_cost, TX_CHARGE, new_bal, ref)

        job_ids: list[str] = []
        engine = (tts_engine or "edge").strip().lower() or "edge"
        model = (openai_model or "tts-1").strip() or "tts-1"
        running_now = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND status=?",
            (user_id, STATUS_RUNNING),
        ).fetchone()["n"]
        lim = max_concurrent_per_user(2)
        for idx, (source, cost) in enumerate(zip(sources, costs)):
            job_id = f"web_{uuid.uuid4().hex}"
            # Job moi luon queued; worker chi start khi user con slot (< lim running)
            msg = "Dang cho xu ly..."
            if int(running_now) >= lim:
                msg = f"Cho slot (toi da {lim} job song song / user)..."
            # Tang nhe created_at theo thu tu danh sach de dam bao FIFO tuyet doi
            job_created_at = now + (idx * 0.001)
            conn.execute(
                "INSERT INTO jobs (id, user_id, source_url, voice, target_lang, tts_engine, openai_model, status, stage, "
                "progress, message, credits_cost, created_at, output_opts_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id, user_id, source, voice, target_lang or "vi", engine, model,
                    STATUS_QUEUED, "queued", 0, msg, cost, job_created_at, output_opts_json or "{}",
                ),
            )
            job_ids.append(job_id)
        conn.commit()
    return job_ids, costs


def create_job(
    source_url: str,
    voice: str,
    user_id: str,
    credits_cost: int,
    target_lang: str = "vi",
) -> str:
    job_id = f"web_{uuid.uuid4().hex}"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, user_id, source_url, voice, target_lang, status, stage, progress, message, credits_cost, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, user_id, source_url, voice, target_lang or "vi",
             STATUS_QUEUED, "queued", 0, "Dang cho xu ly...", credits_cost, time.time()),
        )
        conn.commit()
    return job_id


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(
    user_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    statuses: Optional[tuple[str, ...]] = None,
) -> list[dict[str, Any]]:
    offset = max(0, int(offset))
    limit = max(1, min(100_000, int(limit)))
    with _connect() as conn:
        clauses: list[str] = []
        args: list[Any] = []
        if user_id:
            clauses.append("user_id=?")
            args.append(user_id)
        if statuses:
            clean = [s for s in statuses if s]
            if clean:
                clauses.append(f"status IN ({','.join('?' * len(clean))})")
                args.extend(clean)
        elif status:
            clauses.append("status=?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.extend([limit, offset])
        rows = conn.execute(
            f"SELECT * FROM jobs {where} "
            "ORDER BY CASE status "
            "WHEN 'running' THEN 0 "
            "WHEN 'queued' THEN 1 "
            "WHEN 'completed' THEN 2 "
            "ELSE 3 END, "
            "CASE WHEN status = 'queued' THEN created_at END ASC, "
            "CASE WHEN status = 'queued' THEN rowid END ASC, "
            "created_at DESC LIMIT ? OFFSET ?",
            args,
        ).fetchall()
    return [dict(r) for r in rows]


def queue_position(job_id: str) -> tuple[int, int]:
    """Vi tri hang doi (1-based) trong cac job co the start (user con slot).

    Chi dem queued cua cac user dang duoi gioi han running — dung voi
    max_concurrent_per_user. (0,0) neu job khong con queued.
    """
    lim = max_concurrent_per_user(2)
    with _connect() as conn:
        job = conn.execute(
            "SELECT status, created_at, user_id FROM jobs WHERE id=?", (job_id,),
        ).fetchone()
        if not job or job["status"] != STATUS_QUEUED:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs j WHERE j.status=? AND ("
                "  SELECT COUNT(*) FROM jobs r WHERE r.user_id=j.user_id AND r.status=?"
                ") < ?",
                (STATUS_QUEUED, STATUS_RUNNING, lim),
            ).fetchone()["n"]
            return 0, int(total)
        # Neu chinh user nay da full slot: hang doi trong user
        user_running = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND status=?",
            (job["user_id"], STATUS_RUNNING),
        ).fetchone()["n"]
        if int(user_running) >= lim:
            user_q = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND status=?",
                (job["user_id"], STATUS_QUEUED),
            ).fetchone()["n"]
            ahead = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND status=? AND ("
                "created_at < ? OR (created_at = ? AND id <= ?))",
                (
                    job["user_id"], STATUS_QUEUED,
                    job["created_at"], job["created_at"], job_id,
                ),
            ).fetchone()["n"]
            return int(ahead), int(user_q)
        ahead = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs j WHERE j.status=? AND ("
            "  SELECT COUNT(*) FROM jobs r WHERE r.user_id=j.user_id AND r.status=?"
            ") < ? AND ("
            "j.created_at < ? OR (j.created_at = ? AND j.id <= ?))",
            (
                STATUS_QUEUED, STATUS_RUNNING, lim,
                job["created_at"], job["created_at"], job_id,
            ),
        ).fetchone()["n"]
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs j WHERE j.status=? AND ("
            "  SELECT COUNT(*) FROM jobs r WHERE r.user_id=j.user_id AND r.status=?"
            ") < ?",
            (STATUS_QUEUED, STATUS_RUNNING, lim),
        ).fetchone()["n"]
    return int(ahead), int(total)


def count_jobs(user_id: Optional[str] = None, status: Optional[str] = None) -> int:
    with _connect() as conn:
        clauses: list[str] = []
        args: list[Any] = []
        if user_id:
            clauses.append("user_id=?")
            args.append(user_id)
        if status:
            clauses.append("status=?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs {where}", args,
        ).fetchone()
    return int(row["n"] if row else 0)


def count_user_jobs(user_id: str, status: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND status=?",
            (user_id, status),
        ).fetchone()
    return int(row["n"] if row else 0)


def claim_next_job(max_concurrent_per_user: int = 2) -> Optional[dict[str, Any]]:
    """Lay job queued cu nhat ma user con slot chay song song.

    Khong gioi han global theo 'chi 2 job ca he thong'. Gioi han duy nhat:
    moi user_id toi da `max_concurrent_per_user` job status=running.
    Nhieu user (vd 100) co the running dong thoi neu co du worker/may.

    An toan multi-worker nho BEGIN IMMEDIATE (SQLite khoa ghi).
    """
    lim = max(1, int(max_concurrent_per_user or 2))
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute(
            "SELECT j.* FROM jobs j "
            "WHERE j.status=? AND ("
            "  SELECT COUNT(*) FROM jobs r "
            "  WHERE r.user_id = j.user_id AND r.status=?"
            ") < ? "
            "ORDER BY j.created_at ASC, j.rowid ASC LIMIT 1",
            (STATUS_QUEUED, STATUS_RUNNING, lim),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE jobs SET status=?, stage=?, progress=?, message=?, started_at=? WHERE id=?",
            (
                STATUS_RUNNING,
                "starting",
                1,
                "Bat dau xu ly...",
                time.time(),
                row["id"],
            ),
        )
        conn.commit()
        return dict(row)


def update_progress(job_id: str, stage: str, progress: int, message: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET stage=?, progress=?, message=? WHERE id=? AND status=?",
            (stage, int(progress), message, job_id, STATUS_RUNNING),
        )
        conn.commit()


def mark_completed(
    job_id: str,
    output_path: str,
    qa_json: str | None = None,
    *,
    message: str | None = None,
) -> None:
    out = (output_path or "").strip()
    if not out:
        raise ValueError(f"mark_completed({job_id}): output_path rong — khong duoc danh DONE")
    msg = (message or "").strip() or "Hoan tat!"
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, stage=?, progress=?, message=?, output_path=?, qa_json=?, finished_at=? "
            "WHERE id=? AND status=?",
            (
                STATUS_COMPLETED, "completed", 100, msg[:2000], out, qa_json,
                time.time(), job_id, STATUS_RUNNING,
            ),
        )
        conn.commit()


def set_source_title(job_id: str, title: str) -> None:
    title = (title or "").strip()[:300]
    if not title:
        return
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET source_title=? WHERE id=?",
            (title, job_id),
        )
        conn.commit()


def mark_downloaded(job_id: str) -> None:
    """Danh dau user da co ban copy (Save folder / Download) — purge moi duoc xoa som."""
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET downloaded=1 WHERE id=? AND COALESCE(downloaded,0)=0",
            (job_id,),
        )
        conn.commit()


def set_output_path(job_id: str, output_path: str) -> None:
    """Cap nhat file xuat (sau khi Editor apply), giu status/QA."""
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET output_path=? WHERE id=?",
            (output_path, job_id),
        )
        conn.commit()


def is_cancelled(job_id: str) -> bool:
    row = get_job(job_id)
    return bool(row and row.get("status") == STATUS_CANCELLED)


def cancel_job(job_id: str, user_id: str) -> bool:
    """Huy queued/running. Hoan credit. Tra ve False neu khong huy duoc."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if (
            row is None
            or row["user_id"] != user_id
            or row["status"] not in (STATUS_QUEUED, STATUS_RUNNING)
        ):
            conn.commit()
            return False
        conn.execute(
            "UPDATE jobs SET status=?, stage=?, message=?, error=?, finished_at=? WHERE id=?",
            (
                STATUS_CANCELLED,
                "cancelled",
                "Da huy",
                "Da huy boi nguoi dung",
                time.time(),
                job_id,
            ),
        )
        if row["user_id"] and row["credits_cost"] and not row["refunded"]:
            _refund_locked(conn, row["user_id"], job_id, row["credits_cost"])
        conn.commit()
        return True


def cancel_active_jobs(user_id: str) -> int:
    """Huy moi job queued/running cua user. Tra ve so job da huy."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE user_id=? AND status IN (?, ?)",
            (user_id, STATUS_QUEUED, STATUS_RUNNING),
        ).fetchall()
    n = 0
    for r in rows:
        if cancel_job(r["id"], user_id):
            n += 1
    return n


def mark_failed(job_id: str, error: str, qa_json: str | None = None) -> None:
    with _connect() as conn:
        if qa_json is not None:
            conn.execute(
                "UPDATE jobs SET status=?, stage=?, message=?, error=?, qa_json=?, finished_at=? "
                "WHERE id=? AND status=?",
                (
                    STATUS_FAILED,
                    "failed",
                    "Xu ly that bai",
                    error[:2000],
                    qa_json,
                    time.time(),
                    job_id,
                    STATUS_RUNNING,
                ),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status=?, stage=?, message=?, error=?, finished_at=? "
                "WHERE id=? AND status=?",
                (
                    STATUS_FAILED, "failed", "Xu ly that bai", error[:2000],
                    time.time(), job_id, STATUS_RUNNING,
                ),
            )
        conn.commit()


def init_merge_history(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS merge_history (
        id            TEXT PRIMARY KEY,
        user_id       TEXT,
        filename      TEXT NOT NULL,
        output_path   TEXT NOT NULL,
        output_folder TEXT,
        file_size     INTEGER NOT NULL DEFAULT 0,
        total_videos  INTEGER NOT NULL DEFAULT 0,
        duration_sec  REAL NOT NULL DEFAULT 0.0,
        status        TEXT NOT NULL DEFAULT 'completed',
        created_at    REAL NOT NULL
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_merge_created ON merge_history(created_at DESC)")


def add_merge_history(
    filename: str,
    output_path: str,
    output_folder: str = "",
    file_size: int = 0,
    total_videos: int = 0,
    duration_sec: float = 0.0,
    user_id: str | None = None,
    status: str = "completed",
    task_id: str | None = None,
) -> dict[str, Any]:
    mid = task_id or f"m_{uuid.uuid4().hex[:12]}"
    now = time.time()
    with _connect() as conn:
        init_merge_history(conn)
        conn.execute(
            "INSERT OR REPLACE INTO merge_history (id, user_id, filename, output_path, output_folder, file_size, total_videos, duration_sec, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, user_id, filename, output_path, output_folder, file_size, total_videos, duration_sec, status, now),
        )
        conn.commit()
    return {
        "id": mid,
        "filename": filename,
        "output_path": output_path,
        "output_folder": output_folder,
        "file_size": file_size,
        "total_videos": total_videos,
        "duration_sec": duration_sec,
        "status": status,
        "created_at": now,
    }


def list_merge_history(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        init_merge_history(conn)
        rows = conn.execute(
            "SELECT * FROM merge_history ORDER BY created_at DESC LIMIT ?",
            (max(1, min(500, int(limit))),),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_merge_history(merge_id: str) -> bool:
    with _connect() as conn:
        init_merge_history(conn)
        cur = conn.execute("DELETE FROM merge_history WHERE id=?", (merge_id,))
        conn.commit()
    return cur.rowcount > 0


def update_queued_jobs_export_dir(user_id: str, new_export_dir: str) -> int:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, output_opts_json FROM jobs WHERE user_id=? AND status=?",
            (user_id, STATUS_QUEUED),
        ).fetchall()
        count = 0
        for r in rows:
            try:
                opts = json.loads(r["output_opts_json"] or "{}")
                # Neu job da duoc chi dinh thu muc rieng khi tao: GIU NGUYEN, khong ghi de
                if str(opts.get("export_dir") or "").strip():
                    continue
                opts["export_dir"] = new_export_dir
                conn.execute(
                    "UPDATE jobs SET output_opts_json=? WHERE id=?",
                    (json.dumps(opts, ensure_ascii=False), r["id"]),
                )
                count += 1
            except Exception as e:
                log.warning("Khong cap nhat duoc export_dir cho job %s: %s", r["id"], e)
        conn.commit()
        return count


def sync_existing_merged_files(output_dir: Path | str) -> int:
    """Dong bo cac file merged_*.mp4 da co trong thu muc output vao bang merge_history."""
    try:
        p = Path(output_dir)
        if not p.is_dir():
            return 0
        added = 0
        with _connect() as conn:
            init_merge_history(conn)
            existing_paths = {r["output_path"] for r in conn.execute("SELECT output_path FROM merge_history").fetchall()}
            for f in sorted(p.glob("merged_*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
                if str(f) not in existing_paths and f.is_file():
                    stat = f.stat()
                    mid = f"m_{f.stem}"
                    conn.execute(
                        "INSERT OR IGNORE INTO merge_history (id, user_id, filename, output_path, output_folder, file_size, total_videos, duration_sec, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (mid, None, f.name, str(f), str(p), stat.st_size, 0, 0.0, "completed", stat.st_mtime),
                    )
                    added += 1
            if added:
                conn.commit()
        return added
    except Exception:
        return 0
