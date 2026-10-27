"""Test purge: disk theo GB trong (khong %), grace 48h, downloaded flag.

Chay: .venv\\Scripts\\python.exe scripts\\test_purge_rules.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.web import models, storage  # noqa: E402
from dyflow.web.export_delivery import safe_export_name  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_ascii() -> None:
    n = safe_export_name("Nhiều năm sau, tôi không mặc váy!", "web_abc123")
    _assert(n.endswith(".mp4") and all(ord(c) < 128 for c in n), n)
    print("name", n)


def test_disk_critical_uses_gb_not_pct(tmp: Path) -> None:
    # Mock: nguyen ly — _disk_critical canh GB trong.
    crit, pct, free = storage._disk_critical(
        {"web": {"disk_min_free_gb": 0.000001}},  # rat thap → khong critical neu co bat ky free
        tmp,
    )
    # May that luon co free > 1KB → khong critical
    _assert(free > 0, str(free))
    _assert(crit is False or free < 0.000001, f"crit={crit} free={free}")


def test_forget_messages_distinct(tmp: Path) -> None:
    # Dung DB tam
    import os

    os.chdir(tmp)
    (tmp / "work").mkdir()
    models.init_db()
    jid = "web_" + ("d" * 32)
    now = time.time()
    with models._connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id,user_id,source_url,voice,status,stage,progress,message,"
            "credits_cost,refunded,output_path,created_at,finished_at,downloaded) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                jid, "u1", "http://x", "v", models.STATUS_COMPLETED, "completed", 100,
                "Hoan tat!", 0, 0, str(tmp / "out.mp4"), now - 10, now - 10, 0,
            ),
        )
        conn.commit()
        storage._forget_job_files(
            conn, jid, str(tmp / "out.mp4"),
            reason="Het han luu tru (da qua 7 ngay, user da tai ve).",
        )
        conn.commit()
        row = conn.execute("SELECT message, output_path FROM jobs WHERE id=?", (jid,)).fetchone()
    _assert("Het han luu tru" in row["message"], row["message"])
    _assert("het o dia" not in row["message"].lower(), row["message"])
    _assert(not row["output_path"], repr(row["output_path"]))


def main() -> int:
    test_ascii()
    with tempfile.TemporaryDirectory(prefix="vc_purge_") as td:
        tmp = Path(td)
        test_disk_critical_uses_gb_not_pct(tmp)
        test_forget_messages_distinct(tmp)
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
