#!/usr/bin/env python3
"""Kiem tra i tang dan + in speech/tts/timeline + ZH/VI."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

p = Path(os.environ.get("DYFLOW_QA_JSON") or (sys.argv[1] if len(sys.argv) > 1 else ""))
if not p.exists():
    raise SystemExit("thieu segments_qa.json")
rows = json.loads(p.read_text(encoding="utf-8"))
idxs = [int(r.get("i")) for r in rows]
print("n", len(rows), "i", idxs)
print("monotonic", all(idxs[k] < idxs[k + 1] for k in range(len(idxs) - 1)))
for r in rows:
    zh = str(r.get("source_text") or "")[:48]
    vi = str(r.get("text") or "")[:48]
    print(
        f"i={r.get('i')} speech={r.get('slot')} tts={r.get('tts_dur')} "
        f"tl={r.get('timeline_start')}->{r.get('timeline_end')} "
        f"zh={zh!r} vi={vi!r}"
    )
