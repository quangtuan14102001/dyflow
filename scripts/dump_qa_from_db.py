#!/usr/bin/env python3
"""Doc jobs.qa_json tu dyflow.db. Ghi qa_dump.txt (khong cat cau).

  python scripts/dump_qa_from_db.py --job web_3790f57e7f6e45d18373098837d1dd4d
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def _default_db() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "VideosCreator" / "work" / "dyflow.db"
        if p.exists():
            return p
    return Path("work") / "dyflow.db"


def _ts(v) -> str:
    try:
        return datetime.fromtimestamp(float(v)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "-"


def _cov(r: dict) -> str:
    slot = float(r.get("slot") or 0)
    play = float(r.get("play") or r.get("tts_dur") or 0)
    if slot <= 0:
        return "-"
    return f"{play / slot * 100:.1f}"


def _field(r: dict, *keys: str, default: str = "") -> str:
    for k in keys:
        v = r.get(k)
        if v is None:
            continue
        s = str(v)
        if s.strip():
            return s
    return default


def _row_texts(r: dict) -> tuple[str, str, str, str, str, str]:
    ss = r.get("semantic_shorten") or {}
    zh = _field(ss, "source_text") or _field(r, "source_text", "zh")
    vi_goc = _field(ss, "text_before") or _field(r, "text_before")
    vi_rut = _field(ss, "text_after") or _field(r, "text")
    if not vi_goc:
        vi_goc = _field(r, "text")
    slot = ss.get("slot") if ss.get("slot") is not None else r.get("hard_limit")
    if slot is None:
        slot = r.get("slot")
    tts = ss.get("tts_dur")
    if tts is None:
        tts = r.get("tts_dur")
    if tts is None:
        tts = r.get("play")
    try:
        slot_s = f"{float(slot):.3f}"
    except (TypeError, ValueError):
        slot_s = str(slot or "-")
    try:
        tts_s = f"{float(tts):.3f}"
    except (TypeError, ValueError):
        tts_s = str(tts or "-")
    return zh, vi_goc, vi_rut, slot_s, tts_s, _cov(r)


def _block_row(title: str, r: dict) -> list[str]:
    zh, vi_goc, vi_rut, slot, tts, cov = _row_texts(r)
    lines = [
        title,
        f"i            : {r.get('i')}",
        f"start        : {r.get('start')}",
        f"flags        : {r.get('flags')}",
        f"action       : {r.get('overflow_action')}",
        f"overflow_why : {r.get('overflow_reason') or ((r.get('semantic_shorten') or {}).get('merge_why') or '-')}",
        f"speaker      : {r.get('speaker_id') or '-'}",
        f"relation     : {r.get('speaker_relation') or '-'}",
        f"pronoun      : {r.get('pronoun_pair_used') or '-'}",
        f"ZH           : {zh}",
        f"VI goc       : {vi_goc}",
        f"VI rut       : {vi_rut}",
        f"slot         : {slot}",
        f"tts_dur      : {tts}",
        f"cov%         : {cov}",
        f"atempo_applied: {r.get('atempo_applied') if r.get('atempo_applied') is not None else r.get('atempo') or '-'}",
        f"atempo_needed : {r.get('atempo_needed') if r.get('atempo_needed') is not None else '-'}",
        f"atempo_cap    : {r.get('atempo_cap') if r.get('atempo_cap') is not None else '-'}",
    ]
    ss = r.get("semantic_shorten") or {}
    for a in ss.get("attempts") or []:
        lines.append(
            f"  lan {a.get('attempt')}  target_chars={a.get('target_chars')}  "
            f"deficit_sec={a.get('deficit_sec')}  "
            f"tts {a.get('tts_before')} -> {a.get('tts_after')}  "
            f"atempo_applied={a.get('atempo_applied') if a.get('atempo_applied') is not None else a.get('atempo') or '-'} "
            f"needed={a.get('atempo_needed') if a.get('atempo_needed') is not None else a.get('needed_tempo') or '-'} "
            f"cap={a.get('atempo_cap') or '-'}"
        )
        if a.get("text_before") is not None:
            lines.append(f"    truoc: {a.get('text_before')}")
        if a.get("text_after") is not None:
            lines.append(f"    sau  : {a.get('text_after')}")
    lines.append("")
    return lines


def build_dump(job_id: str, status: str, rows: list[dict]) -> str:
    flags: Counter[str] = Counter()
    for r in rows:
        for f in r.get("flags") or []:
            flags[str(f)] += 1
    n = len(rows)
    chong_n = flags.get("chong_doan", 0)
    lines = [
        f"job          : {job_id}",
        f"status       : {status}",
        f"doan         : {n}",
        f"chong_doan   : {chong_n}" + (f" ({100 * chong_n / n:.1f}%)" if n else ""),
        f"flags        : {dict(flags)}",
        "",
        "==== CAST / XUNG HO / GIONG ====",
        "",
    ]
    if not rows:
        lines.append("(khong co doan)")
        lines.append("")
    else:
        by_sid: dict[str, dict] = {}
        for r in rows:
            sid = str(r.get("speaker_id") or "-")
            rec = by_sid.setdefault(sid, {
                "rel": "", "pair": "", "profile": "", "tts": "", "f0": None, "n": 0,
            })
            rec["n"] += 1
            rec["rel"] = rec["rel"] or str(r.get("speaker_relation") or "")
            rec["pair"] = rec["pair"] or str(r.get("pronoun_pair_used") or "")
            rec["profile"] = rec["profile"] or str(r.get("voice_profile") or "")
            rec["tts"] = rec["tts"] or str(
                r.get("tts_voice_name") or r.get("tts_voice") or ""
            )
            if rec["f0"] is None and r.get("median_f0_hz") is not None:
                rec["f0"] = r.get("median_f0_hz")
        for sid, rec in sorted(by_sid.items(), key=lambda x: -x[1]["n"]):
            f0 = rec["f0"]
            f0s = f"{f0} Hz" if f0 is not None else "-"
            lines.append(
                f"{sid}  |  {rec['rel'] or '-'}  |  {rec['pair'] or '-'}  |  "
                f"{rec['profile'] or '-'}  |  {f0s}  |  {rec['tts'] or '-'}  |  "
                f"{rec['n']} doan"
            )
        lines.append("")
        lines.append("==== TAT CA DOAN (xung ho + giong) ====")
        lines.append("")
        for r in rows:
            f0 = r.get("median_f0_hz")
            f0s = f"{f0} Hz" if f0 is not None else "-"
            lines.append(
                f"i={r.get('i')}  speaker={r.get('speaker_id') or '-'}  "
                f"profile={r.get('voice_profile') or '-'}  f0={f0s}  "
                f"tts={r.get('tts_voice_name') or r.get('tts_voice') or '-'}  "
                f"relation={r.get('speaker_relation') or '-'}  "
                f"pronoun={r.get('pronoun_pair_used') or '-'}"
            )
            lines.append(f"  VI: {r.get('text') or ''}")
        lines.append("")
    lines.extend(
        [
            "==== BANG RUT GON NGU NGHIA ====",
            "",
        ]
    )
    sem = [
        r
        for r in rows
        if r.get("semantic_shorten")
        or "shortened_semantic" in (r.get("flags") or [])
        or "shortened_semantic_fail" in (r.get("flags") or [])
    ]
    if not sem:
        lines.append("(khong co doan rut gon ngu nghia overflow)")
        lines.append("")
    else:
        for r in sem:
            ss = r.get("semantic_shorten") or {}
            lines.extend(_block_row(f"--- semantic i={r.get('i')} ok={ss.get('ok')} ---", r))

    lines.extend(
        [
            "==== HANG chong_doan ====",
            "",
        ]
    )
    chong = [r for r in rows if "chong_doan" in (r.get("flags") or [])]
    if not chong:
        lines.append("(khong co hang chong_doan)")
        lines.append("")
    else:
        for r in chong:
            lines.extend(_block_row(f"--- chong_doan i={r.get('i')} ---", r))
    return "\n".join(lines).rstrip() + "\n"


def _find_job(con: sqlite3.Connection, job_id: str):
    row = con.execute(
        """
        SELECT id, status, stage, source_url, created_at, finished_at,
               error, message, length(qa_json) AS qa_len, qa_json
        FROM jobs WHERE id = ?
        """,
        (job_id,),
    ).fetchone()
    if row:
        return row
    return con.execute(
        """
        SELECT id, status, stage, source_url, created_at, finished_at,
               error, message, length(qa_json) AS qa_len, qa_json
        FROM jobs WHERE id LIKE ? ORDER BY created_at DESC
        """,
        (f"{job_id}%",),
    ).fetchone()


def _recent_jobs(con: sqlite3.Connection, n: int = 8) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT id, status, stage, created_at,
               length(qa_json) AS qa_len,
               substr(COALESCE(error, ''), 1, 80) AS err
        FROM jobs ORDER BY created_at DESC LIMIT ?
        """,
        (n,),
    ).fetchall()


def _format_recent(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "(bang jobs trong)\n"
    lines = ["job gan nhat:"]
    for r in rows:
        lines.append(
            f"  {r['id']}  {r['status']}/{r['stage'] or '-'}  "
            f"{_ts(r['created_at'])}  qa_bytes={r['qa_len'] or 0}"
            + (f"  err={r['err']}" if r["err"] else "")
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="Duong dan dyflow.db")
    ap.add_argument(
        "--job",
        default="latest",
        help="Job id, hoac 'latest' = job moi nhat co qa_json",
    )
    ap.add_argument(
        "--list",
        action="store_true",
        help="Chi liet ke job gan nhat, khong dump QA",
    )
    ap.add_argument(
        "--dump",
        default="",
        help="File text (mac dinh: qa_dump.txt cung thu muc script)",
    )
    args = ap.parse_args()

    db = Path(args.db) if args.db else _default_db()
    dump_path = Path(args.dump) if args.dump else (SCRIPT_DIR / "qa_dump.txt")
    print("db:", db, "exists:", db.exists())
    print("job:", args.job)
    print("dump:", dump_path)
    if not db.exists():
        dump_path.write_text(f"Khong thay db: {db}\n", encoding="utf-8")
        return 1

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    if args.list:
        print(_format_recent(_recent_jobs(con, 15)), end="")
        return 0

    db = Path(args.db) if args.db else _default_db()
    dump_path = Path(args.dump) if args.dump else (SCRIPT_DIR / "qa_dump.txt")
    print("db:", db, "exists:", db.exists())
    print("job:", args.job)
    print("dump:", dump_path)
    if not db.exists():
        dump_path.write_text(f"Khong thay db: {db}\n", encoding="utf-8")
        return 1

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    if str(args.job).strip().lower() in ("", "latest", "-"):
        j = con.execute(
            """
            SELECT id, status, stage, source_url, created_at, finished_at,
                   error, message, length(qa_json) AS qa_len, qa_json
            FROM jobs WHERE qa_json IS NOT NULL AND length(qa_json) > 2
            ORDER BY created_at DESC LIMIT 1
            """,
        ).fetchone()
    else:
        j = _find_job(con, args.job)
    if not j:
        recent = _recent_jobs(con)
        if str(args.job).strip().lower() in ("", "latest", "-"):
            msg = (
                "Khong co job nao da ghi qa_json (job chua chay xong TTS/QA).\n"
                "Chay clip dub xong roi dump lai. --job latest chi lay job co QA.\n\n"
                + _format_recent(recent)
            )
        else:
            msg = f"Khong thay job {args.job}\n\n" + _format_recent(recent)
        dump_path.write_text(msg, encoding="utf-8")
        print(msg, end="")
        return 1

    print("found:", j["id"], j["status"], _ts(j["created_at"]), "qa_bytes=", j["qa_len"])
    if not j["qa_json"]:
        msg = (
            f"job {j['id']} status={j['status']} khong co qa_json.\n"
            f"error: {j['error']}\n"
        )
        dump_path.write_text(msg, encoding="utf-8")
        print(msg, end="")
        return 1

    rows = json.loads(j["qa_json"])
    text = build_dump(j["id"], j["status"], rows)
    timing_path = db.parent / str(j["id"]) / "timing.txt"
    if timing_path.exists():
        text += "\n==== TIMING ====\n\n" + timing_path.read_text(encoding="utf-8")
    else:
        text += f"\n==== TIMING ====\n\n(khong co {timing_path})\n"
    dump_path.write_text(text, encoding="utf-8")
    print(text)
    print("da ghi:", dump_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
