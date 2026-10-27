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


def _flags_of(row: dict) -> list:
    return list(row.get("flags") or [])


def is_chong_micro(flags: list) -> bool:
    return (
        "chong_doan_micro" in flags
        or "micro_overlap" in flags
    )


def is_chong_severe(flags: list) -> bool:
    return (
        "chong_doan_severe" in flags
        or "tier5_large_overlap" in flags
        or "chong_doan" in flags  # legacy
    )


def tier_review_rows(rows: list[dict]) -> list[dict]:
    """Danh sach doan dung tang 4/5 — luon hien tren report ke ca khi job PASS."""
    out: list[dict] = []
    for r in rows:
        flags = _flags_of(r)
        tier4 = "tier4_aggressive_cut" in flags
        tier5 = "tier5_large_overlap" in flags or "chong_doan_severe" in flags
        if not (tier4 or tier5):
            continue
        out.append({
            "i": r.get("i"),
            "start": r.get("start"),
            "text": (r.get("text") or "")[:120],
            "tier4_aggressive_cut": tier4,
            "tier5_large_overlap": tier5,
            "tier3_borrowed_sec": r.get("tier3_borrowed_sec") or 0.0,
            "tier5_overlap_sec": r.get("tier5_overlap_sec") or r.get("overlap_sec") or 0.0,
            "tier5_crossfade_sec": r.get("tier5_crossfade_sec") or 0.0,
            "flags": flags,
        })
    return out


def tier_stats(rows: list[dict]) -> dict:
    n = max(1, len(rows))
    t3 = sum(1 for r in rows if "tier3_borrowed_time" in _flags_of(r))
    t4 = sum(1 for r in rows if "tier4_aggressive_cut" in _flags_of(r))
    t5 = sum(
        1 for r in rows
        if "tier5_large_overlap" in _flags_of(r) or "chong_doan_severe" in _flags_of(r)
    )
    micro = sum(1 for r in rows if is_chong_micro(_flags_of(r)))
    return {
        "segments": len(rows),
        "tier3_borrowed_time": t3,
        "tier4_aggressive_cut": t4,
        "tier5_large_overlap": t5,
        "chong_doan_micro": micro,
        "tier4_or_5": t4 + t5,
        "tier4_or_5_ratio": round((t4 + t5) / n, 4),
        "needs_listen_review": t4 + t5 > 0,
    }


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
    """Tra (exit_code, summary, lines log). exit_code 0 = pass.

    Phuong an A (zero-fail overlap): chong_doan_micro / chong_doan_severe
    KHONG fail job — chi canh bao review. Van fail audio_truncated / chu Han.
    """
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
    stats = tier_stats(rows)

    for r in rows:
        hard = float(r.get("hard_limit") or 0)
        slot = float(r["slot"])
        play = float(r["play"])
        spd = float(r.get("speed") or 1.0)
        cov = (play / slot * 100) if slot > 0 else 0
        flags = _flags_of(r)
        row_fail: list[str] = []
        drift = float(r.get("drift_pct") or 0)
        micro = is_chong_micro(flags)
        severe = is_chong_severe(flags) and not micro
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
        # Overlap chap nhan (micro/severe): khong tinh drift fail.
        if hard > 0 and play > hard * 1.02 and not micro and not severe:
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
        if "qua_dai" in flags or is_chong_severe(flags) or is_chong_micro(flags):
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
        if "chua_dich" in _flags_of(r):
            return 1, summary + " | FAIL: con chu Han", lines

    # Tong cov / im_lang: clip goc co nghi dai thi giọng < khung — dung, khong fail.
    if pct < min_total_coverage * 100:
        summary += f" | khoang_lang tong={pct:.0f}%"
    if im_lang_n:
        summary += f" | {im_lang_n} doan nghi"

    # Thong ke tang 4/5 — luon co trong summary (ke ca PASS).
    summary += (
        f" | tang4/5={stats['tier4_aggressive_cut']}+{stats['tier5_large_overlap']}"
        f"/{stats['segments']}"
    )
    if stats["chong_doan_micro"]:
        summary += f" | chong_micro={stats['chong_doan_micro']}"
    if stats["tier5_large_overlap"]:
        summary += f" | REVIEW tang5={stats['tier5_large_overlap']} doan"

    if trunc_n:
        return (
            1,
            summary + f" | FAIL: {trunc_n} doan audio_truncated (TTS bi cat, phu de con dai)",
            lines,
        )

    # Zero-fail overlap: KHONG fail chong_doan_micro / chong_doan_severe.
    # Legacy "chong_doan" khong kem tier5 cung coi la severe review, van PASS.
    if qua_dai_ratio > float(max_qua_dai_ratio):
        # Nguong mac dinh 1.01 — thuc te khong fail; giu hook neu config siet.
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
            if spd > speed_hi + 0.05 or "chua_dich" in _flags_of(r):
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
    """Fail-closed ngay sau TTS (truoc Demucs/export). Khong phu thuoc engine.

    Phuong an A: overlap (micro/severe) khong fail — chi audio_truncated / Han.
    Tren Desktop mode: chi log canh bao va xuat tiep de nguoi dung nhan video hoan chinh.
    """
    if str((vo_cfg or {}).get("audio_mode", "dub")).lower() != "dub":
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
    # Ghi review tang 4/5 ke ca khi PASS.
    rows = load_qa_rows(work)
    review = tier_review_rows(rows)
    stats = tier_stats(rows)
    (work / "tier45_review.json").write_text(
        json.dumps({
            "stats": stats,
            "segments": review,
            "summary": summary,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if review:
        log.warning(
            "Dub QA REVIEW tang4/5: %d/%d doan — nghe lai truoc khi giao khach",
            len(review), stats["segments"],
        )
        for item in review[:12]:
            log.warning(
                "  REVIEW #%s tier4=%s tier5=%s borrow=%.2fs overlap=%.2fs xfade=%.2fs | %s",
                item.get("i"), item.get("tier4_aggressive_cut"),
                item.get("tier5_large_overlap"),
                float(item.get("tier3_borrowed_sec") or 0),
                float(item.get("tier5_overlap_sec") or 0),
                float(item.get("tier5_crossfade_sec") or 0),
                (item.get("text") or "")[:50],
            )
    if code != 0:
        for ln in lines[:8]:
            log.warning("  dub QA: %s", ln)

        # Desktop mode hoac dub_qa_fail_closed=False: log canh bao va van xuat video
        fail_closed = bool((vo_cfg or {}).get("dub_qa_fail_closed", True))
        try:
            from .desktop_mode import enabled as desktop_on
            if desktop_on() and not (vo_cfg or {}).get("dub_qa_fail_closed_desktop", False):
                fail_closed = False
        except Exception:
            pass

        if not fail_closed:
            log.warning(
                "Dub QA co canh bao nhung van tiep tuc xuat video cho user (desktop/fail-safe): %s",
                summary,
            )
            return

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
