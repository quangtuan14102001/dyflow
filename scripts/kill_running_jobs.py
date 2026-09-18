#!/usr/bin/env python3
"""Huy job dang running/queued, danh failed, xoa work/<job>.

  python scripts/kill_running_jobs.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def _default_db() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "VideosCreator" / "work" / "dyflow.db"
        if p.exists():
            return p
    return Path("work") / "dyflow.db"


def _kill_ffmpeg() -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/IM", "ffmpeg.exe"],
            capture_output=True, text=True, check=False,
        )
    else:
        subprocess.run(["pkill", "-9", "ffmpeg"], check=False)


def main() -> int:
    db = _default_db()
    print("db:", db, "exists:", db.exists())
    if not db.exists():
        print("Khong thay dyflow.db")
        return 1

    print("Kill ffmpeg...")
    _kill_ffmpeg()

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT id, status, stage, progress, message, error
        FROM jobs
        WHERE status IN ('running', 'queued', 'starting')
           OR stage IN ('running', 'transforming', 'trimming', 'downloading',
                        'transcribing', 'synthesizing', 'composing')
        ORDER BY created_at DESC
        """,
    ).fetchall()
    # Chi job chua ket thuc
    active = [
        r for r in rows
        if str(r["status"] or "") not in ("completed", "failed")
    ]
    if not active:
        print("Khong co job dang chay.")
        # van liet ke job moi nhat de doi chieu
        latest = con.execute(
            "SELECT id, status, stage, progress FROM jobs ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        for r in latest:
            print(f"  {r['id']}  {r['status']}/{r['stage']}  {r['progress']}%")
        return 0

    why = "Huy: Transform/ffmpeg treo (user kill_running_jobs)"
    now = time.time()
    work_root = db.parent
    for r in active:
        jid = r["id"]
        print(
            f"Huy {jid}  status={r['status']} stage={r['stage']} "
            f"progress={r['progress']} msg={r['message']}"
        )
        con.execute(
            """
            UPDATE jobs SET status=?, stage=?, message=?, error=?, finished_at=?
            WHERE id=?
            """,
            ("failed", "failed", "Xu ly that bai", why, now, jid),
        )
        folder = work_root / jid
        if folder.is_dir():
            try:
                shutil.rmtree(folder)
                print(f"  da xoa {folder}")
            except OSError as e:
                print(f"  xoa {folder} that bai: {e}")
        else:
            print(f"  khong co thu muc {folder}")
    con.commit()
    print("Xong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
