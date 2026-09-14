"""Kiem tra chat luong dub — dung cho moi job (web, batch, CLI)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .dub_constants import (
    QA_MAX_DRIFT_PCT,
    QA_MAX_QUA_DAI_RATIO,
    TTS_ATEMPO_MAX,
    TTS_ATEMPO_MIN,
)
from .dub_timing import is_audio_truncated
from .utils import log


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
    speed_lo: float = TTS_ATEMPO_MIN,
    speed_hi: float = TTS_ATEMPO_MAX,
    max_im_lang_segments: int = 2,
    max_qua_dai_ratio: float = QA_MAX_QUA_DAI_RATIO,
    max_drift_pct: float = QA_MAX_DRIFT_PCT,
) -> tuple[int, str, list[str]]:
    """Tra (exit_code, summary, lines log). exit_code 0 = pass."""
    if not rows:
        return 2, "Khong co segments_qa.json", []

    engine = (tts_engine or "edge").lower()
    strict_im = engine in ("edge", "")
    fails = 0
    im_lang_n = 0
    qua_dai_n = 0
    max_row_drift = 0.0
    total_play = total_slot = 0.0
    trunc_n = 0
    lines: list[str] = []

    for r in rows:
        hard = float(r.get("hard_limit") or 0)
        slot = float(r["slot"])
        play = float(r["play"])
        spd = float(r.get("speed") or 1.0)
        cov = (play / slot * 100) if slot > 0 else 0
        flags = list(r.get("flags") or [])
        row_fail: list[str] = []
        drift = float(r.get("drift_pct") or 0)
        micro = "micro_overlap" in flags
        raw = float(r.get("tts_dur_raw") or 0)
        dur = float(r.get("tts_dur") or r.get("post_dur") or play)
        atempo = float(r.get("atempo_applied") or r.get("atempo") or 1.0)
        truncated = (
            "audio_truncated" in flags
            or "cat_khop" in flags
            or (raw > 0 and is_audio_truncated(raw, dur, atempo))
        )
        if truncated:
            trunc_n += 1
            row_fail.append("audio_truncated")
            if "audio_truncated" not in flags:
                flags.append("audio_truncated")
        if hard > 0 and play > hard * 1.02 and not micro:
            if not drift:
                drift = (play - hard) / hard * 100
            max_row_drift = max(max_row_drift, drift)
        elif not drift and slot > 0 and play > slot:
            drift = 0.0

        if spd < speed_lo or spd > speed_hi:
            row_fail.append(f"toc={spd:.2f}")
            flags.append("toc_lech")
        if cov < min_coverage * 100:
            # Slot goc co khoang lang — khong tinh loi, khong fail job.
            flags.append("im_lang")
            im_lang_n += 1
        if "qua_dai" in flags or "chong_doan" in flags:
            qua_dai_n += 1
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

    nseg = max(1, len(rows))
    qua_dai_ratio = qua_dai_n / nseg
    pct = total_play / total_slot * 100 if total_slot else 0
    summary = f"giong {total_play:.1f}s/khung {total_slot:.1f}s={pct:.0f}% loi {fails}/{len(rows)}"

    for r in rows:
        if "chua_dich" in (r.get("flags") or []):
            return 1, summary + " | FAIL: con chu Han", lines

    # Tong cov / im_lang: clip goc co nghi dai thi giọng < khung — dung, khong fail.
    if pct < min_total_coverage * 100:
        summary += f" | khoang_lang tong={pct:.0f}%"
    if im_lang_n:
        summary += f" | {im_lang_n} doan nghi"

    chong_n = sum(1 for r in rows if "chong_doan" in (r.get("flags") or []))
    if trunc_n:
        return (
            1,
            summary + f" | FAIL: {trunc_n} doan audio_truncated (TTS bi cat, phu de con dai)",
            lines,
        )
    if chong_n:
        return (
            1,
            summary + f" | FAIL: {chong_n} doan chong_doan (TTS de len cau sau, khong cat chu)",
            lines,
        )

    if qua_dai_ratio > float(max_qua_dai_ratio):
        return (
            1,
            summary + f" | FAIL: qua_dai {qua_dai_n}/{len(rows)}={qua_dai_ratio:.0%} (nguong {max_qua_dai_ratio:.0%})",
            lines,
        )

    if max_row_drift > float(max_drift_pct):
        return (
            1,
            summary + f" | FAIL: drift {max_row_drift:.0f}% > {max_drift_pct:.0f}%",
            lines,
        )

    # OpenAI: chi fail toc qua nhanh / chu Han (qua_dai/drift da chan o tren)
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


def raise_if_dub_qa_fail(work: Path, vo_cfg: dict) -> None:
    """Fail-closed ngay sau TTS (truoc Demucs/export). Khong phu thuoc engine."""
    if str((vo_cfg or {}).get("audio_mode", "dub")).lower() != "dub":
        return
    if not (vo_cfg or {}).get("dub_qa_fail_closed", True):
        return
    work = Path(work)
    if not (work / "segments_qa.json").exists():
        return
    engine = str((vo_cfg or {}).get("tts_engine") or "edge")
    model = str((vo_cfg or {}).get("openai_model") or "").lower()
    if model.startswith(("tts-", "gpt-4o")) and engine in ("edge", ""):
        engine = "openai"
    code, summary, lines = audit_work_dir(
        work,
        tts_engine=engine,
        speed_lo=TTS_ATEMPO_MIN,
        speed_hi=TTS_ATEMPO_MAX,
        max_qua_dai_ratio=float((vo_cfg or {}).get("qa_max_qua_dai_ratio", QA_MAX_QUA_DAI_RATIO)),
        max_drift_pct=float((vo_cfg or {}).get("qa_max_drift_pct", QA_MAX_DRIFT_PCT)),
    )
    if audit_script_han(work):
        code = 1
        summary += " | FAIL: con chu Han"
    if code != 0:
        for ln in lines[:8]:
            log.warning("  dub QA: %s", ln)
        raise RuntimeError(
            f"Dub QA fail — khong giao clip loi. {summary} "
            f"(job web: hoan credit tu dong; desktop: khong tru credit)"
        )
    log.info("Dub QA pass: %s", summary)


def audit_script_han(work: Path) -> int:
    vi_path = Path(work) / "script_auto_vi.txt"
    if not vi_path.exists():
        return 0
    vi = vi_path.read_text(encoding="utf-8")
    return len(re.findall(r"[\u4e00-\u9fff]", vi))
