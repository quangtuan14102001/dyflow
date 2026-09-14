#!/usr/bin/env python3
"""Quet QA job cu: tts_dur < tts_dur_raw/atempo - 0.5s = audio bi cat am tham.

  python scripts/scan_truncated_audio_qa.py
  python scripts/scan_truncated_audio_qa.py --db /path/to/dyflow.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_timing import expected_dur_after_atempo, is_audio_truncated  # noqa: E402


def _candidate_dbs() -> list[Path]:
    cands: list[Path] = []
    try:
        from dyflow.desktop_mode import data_dir

        cands.append(data_dir() / "work" / "dyflow.db")
    except Exception:  # noqa: BLE001
        pass
    home = Path.home()
    cands.append(home / "Library" / "Application Support" / "VideosCreator" / "work" / "dyflow.db")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(Path(local) / "VideosCreator" / "work" / "dyflow.db")
    cands.append(ROOT / "work" / "dyflow.db")
    cands.append(Path("work") / "dyflow.db")
    out: list[Path] = []
    seen: set[str] = set()
    for p in cands:
        try:
            key = str(p.resolve()) if p.exists() else str(p)
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _pick_db(explicit: str) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    for p in _candidate_dbs():
        if p.exists():
            return p
    return None


def _as_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "segments", "qa", "items"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        if "i" in payload or "tts_dur" in payload:
            return [payload]
    return []


def row_truncation(r: dict) -> dict | None:
    raw = float(r.get("tts_dur_raw") or 0)
    dur = float(r.get("tts_dur") if r.get("tts_dur") is not None else (r.get("post_dur") or r.get("play") or 0))
    atempo = float(r.get("atempo_applied") or r.get("atempo") or 1.0)
    if raw <= 0:
        return None
    if not is_audio_truncated(raw, dur, atempo):
        return None
    expected = expected_dur_after_atempo(raw, atempo)
    return {
        "i": r.get("i"),
        "tts_dur_raw": round(raw, 3),
        "tts_dur": round(dur, 3),
        "atempo_applied": round(atempo, 3),
        "expected_dur": round(expected, 3),
        "delta_sec": round(expected - dur, 3),
        "overflow_reason": r.get("overflow_reason") or "",
        "shorten_used": r.get("shorten_used"),
        "flags": list(r.get("flags") or []),
        "chars": r.get("chars") or len(str(r.get("text") or "")),
        "text": str(r.get("text") or "")[:80],
    }


def scan_rows(rows: list[dict]) -> list[dict]:
    hits = []
    for r in rows:
        hit = row_truncation(r)
        if hit:
            hits.append(hit)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="Duong dan dyflow.db")
    ap.add_argument(
        "--out",
        default="",
        help="File bao cao (mac dinh logs/truncated_audio_scan.txt)",
    )
    args = ap.parse_args()

    db = _pick_db(args.db)
    out_path = Path(args.out) if args.out else ROOT / "logs" / "truncated_audio_scan.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def emit(msg: str) -> None:
        print(msg)
        lines.append(msg)

    if not db:
        emit("Khong thay dyflow.db. Thu:")
        for p in _candidate_dbs():
            emit(f"  {p}  exists={p.exists()}")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1

    emit(f"db: {db}")
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    jobs = con.execute(
        """
        SELECT id, status, source_url, created_at, finished_at, qa_json
        FROM jobs
        WHERE status = 'completed'
          AND qa_json IS NOT NULL
          AND length(qa_json) > 2
        ORDER BY COALESCE(finished_at, created_at) DESC
        """
    ).fetchall()
    emit(f"completed jobs co qa_json: {len(jobs)}")

    affected = 0
    hit_rows = 0
    khong_tran_hits = 0
    details: list[str] = []
    for j in jobs:
        try:
            payload = json.loads(j["qa_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        rows = _as_rows(payload)
        hits = scan_rows(rows)
        if not hits:
            continue
        affected += 1
        hit_rows += len(hits)
        khong_tran_hits += sum(1 for h in hits if h["overflow_reason"] == "khong_tran")
        details.append(
            f"\n== {j['id']}  n={len(hits)}/{len(rows)}  url={j['source_url'] or '-'}"
        )
        for h in hits:
            details.append(
                f"  i={h['i']} raw={h['tts_dur_raw']}s atempo={h['atempo_applied']} "
                f"expected={h['expected_dur']}s got={h['tts_dur']}s "
                f"delta={h['delta_sec']}s reason={h['overflow_reason'] or '-'} "
                f"shorten={h['shorten_used']} flags={h['flags']} | {h['text']}"
            )

    emit(f"job bi anh huong: {affected}/{len(jobs)}")
    emit(f"doan truncated: {hit_rows}")
    emit(f"trong do overflow_reason=khong_tran: {khong_tran_hits}")
    if not details:
        emit("Khong co hang nao khop nguong tts_dur < raw/atempo - 0.5s")
    else:
        for d in details:
            emit(d)

    report = "\n".join(lines).rstrip() + "\n"
    out_path.write_text(report, encoding="utf-8")
    print(f"da ghi: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
