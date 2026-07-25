"""SQLite store cho web app: users, sessions, jobs, credit ledger, payments.

Dung sqlite3 chuan (khong ORM). Cac thao tac credit chay trong transaction
de so du + so cai (ledger) luon nhat quan.
"""
from __future__ import annotations

import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path("work") / "dyflow.db"

# Trang thai job
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Loai giao dich credit
TX_TOPUP = "topup"       # nap qua crypto
TX_CHARGE = "charge"     # tru khi tao job
TX_REFUND = "refund"     # hoan khi job that bai
TX_BONUS = "bonus"       # tang khi dang ky

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
    error        TEXT,
    created_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL
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


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()}
    if "tx_hash" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN tx_hash TEXT")
    if "note" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN note TEXT")

    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "target_lang" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN target_lang TEXT NOT NULL DEFAULT 'vi'")

    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "telegram_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN telegram_id TEXT")
    if "trial_started_at" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN trial_started_at REAL")
    if "trial_ends_at" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN trial_ends_at REAL")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram "
        "ON users(telegram_id) WHERE telegram_id IS NOT NULL"
    )


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()


def recover_stale() -> int:
    """Job con 'running' tu process cu -> that bai + hoan credit."""
    n = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, user_id, credits_cost, refunded FROM jobs WHERE status=?",
            (STATUS_RUNNING,),
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?",
                (STATUS_FAILED, "Bi gian doan (server khoi dong lai)", time.time(), r["id"]),
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
    """Dat is_admin=1 cho email (tu ADMIN_EMAIL trong .env)."""
    email = (email or "").strip().lower()
    if not email:
        return
    with _connect() as conn:
        conn.execute("UPDATE users SET is_admin=1 WHERE email=?", (email,))
        conn.commit()


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
    cost_each: int,
    ref: str,
    *,
    trial_ok: bool = True,
) -> tuple[list[str], list[int]] | None:
    """Tao nhieu job: dung free trial truoc, con lai tru credit.
    Tra ve (job_ids, costs) hoac None neu khong du.
    """
    from . import billing as _billing

    n = len(sources)
    if n <= 0:
        return None
    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if user is None:
            conn.commit()
            return None

        free_use = 0
        if trial_ok and _trial_active_row(user, now) and user["telegram_id"]:
            used = _free_used_today_locked(conn, user_id, now)
            free_left = max(0, _billing.TRIAL_VIDEOS_PER_DAY - used)
            free_use = min(free_left, n)

        paid_n = n - free_use
        paid_cost = paid_n * cost_each
        if user["credits"] < paid_cost:
            conn.commit()
            return None

        if paid_cost > 0:
            new_bal = user["credits"] - paid_cost
            conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, user_id))
            _add_tx(conn, user_id, -paid_cost, TX_CHARGE, new_bal, ref)

        costs = [0] * free_use + [cost_each] * paid_n
        job_ids: list[str] = []
        for source, cost in zip(sources, costs):
            job_id = f"web_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO jobs (id, user_id, source_url, voice, target_lang, status, stage, "
                "progress, message, credits_cost, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id, user_id, source, voice, target_lang or "vi",
                    STATUS_QUEUED, "queued", 0, "Dang cho xu ly...", cost, now,
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
) -> list[dict[str, Any]]:
    offset = max(0, int(offset))
    limit = max(1, min(100, int(limit)))
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
        args.extend([limit, offset])
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            args,
        ).fetchall()
    return [dict(r) for r in rows]


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


def claim_next_job() -> Optional[dict[str, Any]]:
    """Lay 1 job queued cu nhat, doi sang running trong 1 transaction.

    An toan cho nhieu worker nho BEGIN IMMEDIATE (SQLite khoa ghi)."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY created_at ASC LIMIT 1",
            (STATUS_QUEUED,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE jobs SET status=?, stage=?, started_at=? WHERE id=?",
            (STATUS_RUNNING, "starting", time.time(), row["id"]),
        )
        conn.commit()
        return dict(row)


def update_progress(job_id: str, stage: str, progress: int, message: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET stage=?, progress=?, message=? WHERE id=?",
            (stage, int(progress), message, job_id),
        )
        conn.commit()


def mark_completed(job_id: str, output_path: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, stage=?, progress=?, message=?, output_path=?, finished_at=? WHERE id=?",
            (STATUS_COMPLETED, "completed", 100, "Hoan tat!", output_path, time.time(), job_id),
        )
        conn.commit()


def mark_failed(job_id: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, stage=?, message=?, error=?, finished_at=? WHERE id=?",
            (STATUS_FAILED, "failed", "Xu ly that bai", error[:2000], time.time(), job_id),
        )
        conn.commit()
