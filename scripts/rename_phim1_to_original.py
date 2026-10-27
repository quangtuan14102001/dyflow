# -*- coding: utf-8 -*-
"""Doi ten MP4 trong D:\\Phim1 theo ten file goc (tap) + cap nhat source_title trong DB."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DB = Path(r"C:\Users\Admin\AppData\Local\VideosCreator\work\dyflow.db")
FOLDER = Path(r"D:\Phim1")
_LOCAL_RE = re.compile(r"^local_[0-9a-f]{8}_(.+)$", re.I)


def original_stem(source_url: str) -> str:
    stem = Path(source_url or "").stem
    m = _LOCAL_RE.match(stem)
    return (m.group(1) if m else stem).strip()


def win_safe(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "video"


def main() -> None:
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT id, source_url, source_title, message
        FROM jobs
        WHERE status='completed'
          AND (message LIKE '%D:\\Phim1%' OR message LIKE '%D:/Phim1%')
        ORDER BY finished_at DESC
        """
    ).fetchall()
    print("jobs_phim1", len(rows))

    renamed = 0
    updated = 0
    missing = 0
    conflicts: list[str] = []

    for r in rows:
        src = str(r["source_url"] or "")
        stem = original_stem(src)
        if not stem:
            continue
        new_name = win_safe(stem) + ".mp4"
        # Tim file hien tai tu message
        msg = str(r["message"] or "")
        m = re.search(r"Da luu:\s*(.+\.mp4)", msg, re.I)
        old_path = Path(m.group(1).strip()) if m else None
        if old_path is None or not old_path.is_file():
            # thu theo source_title slug cu
            title = str(r["source_title"] or "").strip()
            cand = None
            if title:
                # match bat dau ten file
                for f in FOLDER.glob("*.mp4"):
                    if f.stem.replace("_", " ")[:20].lower() in title.lower().replace("đ", "d"):
                        pass
            missing += 1
            print("MISS", r["id"], new_name, "msg=", msg[:80])
            # van cap nhat title DB
            if stem and r["source_title"] != stem:
                con.execute(
                    "UPDATE jobs SET source_title=? WHERE id=?",
                    (stem, r["id"]),
                )
                updated += 1
            continue

        dest = FOLDER / new_name
        if old_path.resolve() == dest.resolve():
            if r["source_title"] != stem:
                con.execute(
                    "UPDATE jobs SET source_title=? WHERE id=?",
                    (stem, r["id"]),
                )
                updated += 1
            continue

        if dest.exists() and dest.resolve() != old_path.resolve():
            # trung ten — them id ngan
            dest = FOLDER / f"{win_safe(stem)}_{r['id'][-6:]}.mp4"
            conflicts.append(f"{new_name} -> {dest.name}")

        try:
            old_path.rename(dest)
            renamed += 1
            print("OK", old_path.name[:50], "->", dest.name)
        except OSError as e:
            print("FAIL", old_path, e)
            continue

        con.execute(
            "UPDATE jobs SET source_title=?, message=? WHERE id=?",
            (stem, f"Hoan tat! Da luu: {dest}", r["id"]),
        )
        updated += 1

    con.commit()
    con.close()
    print("renamed", renamed, "db_updated", updated, "missing", missing)
    if conflicts:
        print("conflicts", len(conflicts))
        for c in conflicts[:10]:
            print(" ", c)


if __name__ == "__main__":
    main()
