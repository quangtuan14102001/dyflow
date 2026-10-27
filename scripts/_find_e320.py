#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

base = Path(os.environ["LOCALAPPDATA"]) / "VideosCreator"
db = base / "work" / "dyflow.db"
con = sqlite3.connect(str(db))
con.row_factory = sqlite3.Row
rows = con.execute(
    "select id, status, output_path from jobs where id like ?",
    ("%e320a330%",),
).fetchall()
print("jobs", [dict(r) for r in rows])
pat = re.compile(
    r"Wide Containment|Azure Dragon|Jingwei|Bai Ze|Kwafu|白泽|夸父|精卫|Formation|Containment",
    re.I,
)
hits = []
for root in (base / "work", base / "output", Path(r"C:\Github\dyflow\work")):
    if not root.exists():
        continue
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".json", ".txt", ".ass", ".csv"}:
            continue
        if "e320a330" not in str(p) and "web_e320" not in str(p):
            # still scan for terms
            pass
        try:
            t = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if pat.search(t) and ("e320a330" in str(p) or "Formation" in t or "Bai Ze" in t):
            hits.append(p)
print("hit files", len(hits))
for p in hits[:30]:
    print(p)
