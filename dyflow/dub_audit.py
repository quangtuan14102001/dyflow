"""Kiem tra chat luong dub — dung cho moi job (web, batch, CLI)."""
from __future__ import annotations

import json
import re
from pathlib import Path


def load_qa_rows(work: Path) -> list[dict]:
    work = Path(work)
    p = work / "segments_qa.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    cues_path = work / "segments_cues.json"
    if not cues_path.exists():
        return []
    cues = json.loads(cues_path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for i, c in enumerate(cues):
        slot = float(c.get("display_end", c["end"])) - float(c["start"])
        play = float(c["end"]) - float(c["start"])
        rows.append({
            "i": i,
            "start": float(c["start"]),
            "slot": slot,
            "tts_dur": float(c.get("tts_dur", play)),
            "post_dur": play,
            "play": play,
            "drift_pct": round(abs(play - slot) / slot * 100, 1) if slot else 0,
            "speed": 1.0,
            "flags": [],
            "text": c.get("text", ""),
        })
    return rows


def audit_qa_rows(
    rows: list[dict],
    *,
    tts_engine: str = "edge",
    min_coverage: float = 0.55,
    min_total_coverage: float = 0.75,
    speed_lo: float = 0.92,
    speed_hi: float = 1.10,
    max_im_lang_segments: int = 2,
) -> tuple[int, str, list[str]]:
    """Tra (exit_code, summary, lines log). exit_code 0 = pass."""
    if not rows:
        return 2, "Khong co segments_qa.json", []

    engine = (tts_engine or "edge").lower()
    strict_im = engine in ("edge", "")
    fails = 0
    im_lang_n = 0
    total_play = total_slot = 0.0
    lines: list[str] = []

    for r in rows:
        slot = float(r["slot"])
        play = float(r["play"])
        spd = float(r.get("speed") or 1.0)
        cov = (play / slot * 100) if slot > 0 else 0
        flags = list(r.get("flags") or [])
        row_fail: list[str] = []

        if spd < speed_lo or spd > speed_hi:
            row_fail.append(f"toc={spd:.2f}")
            flags.append("toc_lech")
        if cov < min_coverage * 100:
            row_fail.append(f"cov={cov:.0f}%")
            flags.append("im_lang")
            im_lang_n += 1
        if "chua_dich" in flags:
            row_fail.append("han")
        if row_fail:
            fails += 1

        total_play += play
        total_slot += slot
        txt = (r.get("text") or "")[:42]
        lines.append(
            f"#{r['i']} {r['start']:.1f}s cov={cov:.0f}% spd={spd:.2f} "
            f"{','.join(flags) or 'ok'} | {txt}"
        )

    pct = total_play / total_slot * 100 if total_slot else 0
    summary = f"giong {total_play:.1f}s/khung {total_slot:.1f}s={pct:.0f}% loi {fails}/{len(rows)}"

    for r in rows:
        if "chua_dich" in (r.get("flags") or []):
            return 1, summary + " | FAIL: con chu Han", lines

    if pct < min_total_coverage * 100 and strict_im:
        return 1, summary + f" | FAIL: tong cov<{min_total_coverage*100:.0f}%", lines

    if strict_im and im_lang_n > max_im_lang_segments:
        return 1, summary + f" | FAIL: {im_lang_n} doan im_lang", lines

    if strict_im and fails:
        return 1, summary, lines

    # OpenAI: chi fail toc qua nhanh / chu Han
    if not strict_im:
        for r in rows:
            spd = float(r.get("speed") or 1.0)
            if spd > speed_hi + 0.05 or "chua_dich" in (r.get("flags") or []):
                return 1, summary + " | FAIL: openai toc/han", lines

    return 0, summary, lines


def audit_work_dir(
    work: Path,
    *,
    tts_engine: str = "edge",
    **kwargs,
) -> tuple[int, str, list[str]]:
    rows = load_qa_rows(Path(work))
    return audit_qa_rows(rows, tts_engine=tts_engine, **kwargs)


def audit_script_han(work: Path) -> int:
    vi_path = Path(work) / "script_auto_vi.txt"
    if not vi_path.exists():
        return 0
    vi = vi_path.read_text(encoding="utf-8")
    return len(re.findall(r"[\u4e00-\u9fff]", vi))
