"""Bao cao dub: transcribe, dich, QA cuoi."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .dub_constants import (
    CUMULATIVE_WARN_MS,
    LOUDNORM_TARGET_I,
    TIMING_WARN_MS,
    VOLUME_WARN_DB,
    WHISPER_DURATION_WARN_RATIO,
)
from .utils import log

# Whisper hay nhan nham -> Google dich sai nghia (can ra tay, khong phai timing)
_HEMORRHOID_ZH = re.compile(r"肉球|瘡|痔疮|刚周|肛周|塞不回去|肉球跑出来")
_HEMORRHOID_VI = re.compile(
    r"trĩ|hậu môn|cục thịt|nhét|thụt|thò|thịt trĩ|thịt thò",
    re.I,
)
_CONTENT_REVIEW_RULES: tuple[dict, ...] = (
    {
        "match": "刚周",
        "kind": "whisper_misread",
        "expected_zh": "肛周",
        "note": "Whisper nham 肛周 thanh 刚周; Google co the dich thanh mang thai",
    },
)


def write_transcribe_report(
    work_dir: Path,
    segments: list[dict],
    video_dur: float,
    *,
    source: str = "whisper",
) -> dict:
    work_dir = Path(work_dir)
    spoken = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)
    vd = max(0.01, float(video_dur))
    ratio = abs(spoken - vd) / vd
    report = {
        "source": source,
        "segment_count": len(segments),
        "transcript_duration_sec": round(spoken, 3),
        "video_duration_sec": round(vd, 3),
        "duration_drift_ratio": round(ratio, 4),
        "duration_drift_pct": round(ratio * 100, 2),
        "warn": ratio > WHISPER_DURATION_WARN_RATIO,
        "warn_message": (
            f"Transcript duration lech {ratio*100:.1f}% so voi video (>{WHISPER_DURATION_WARN_RATIO*100:.0f}%)"
            if ratio > WHISPER_DURATION_WARN_RATIO
            else ""
        ),
    }
    out = work_dir / "transcribe_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Transcribe report: %d doan, transcript %.1fs / video %.1fs (lech %.1f%%)%s",
        len(segments), spoken, vd, ratio * 100,
        " — CANH BAO" if report["warn"] else "",
    )
    return report


def write_translate_csv(work_dir: Path, rows: list[dict]) -> Path:
    work_dir = Path(work_dir)
    out = work_dir / "translate_report.csv"
    fields = [
        "i", "start", "end", "src_chars", "dst_chars", "char_ratio",
        "retries", "status", "source_text", "translated_text",
    ]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    log.info("Da ghi translate_report.csv (%d dong)", len(rows))
    return out


def scan_content_review_flags(segments: list[dict]) -> list[dict]:
    """Tim loi noi dung (Whisper/dich) — tach khoi QA timing."""
    flags: list[dict] = []
    for i, seg in enumerate(segments):
        src = str(seg.get("source_text") or seg.get("text") or "")
        vi = str(seg.get("text") or "")
        for rule in _CONTENT_REVIEW_RULES:
            if rule["match"] not in src:
                continue
            flags.append({
                "i": i,
                "start": round(float(seg.get("start", 0)), 3),
                "end": round(float(seg.get("end", 0)), 3),
                "kind": rule["kind"],
                "source_text": src,
                "translated_text": vi,
                "expected_zh": rule.get("expected_zh", ""),
                "note": rule["note"],
            })
        src_len = len(re.sub(r"\s+", "", src))
        vi_len = len(re.sub(r"\s+", "", vi))
        if src_len >= 4 and vi_len / src_len > 3.8:
            flags.append({
                "i": i,
                "start": round(float(seg.get("start", 0)), 3),
                "end": round(float(seg.get("end", 0)), 3),
                "kind": "translation_suspicious_ratio",
                "source_text": src,
                "translated_text": vi,
                "char_ratio": round(vi_len / src_len, 2),
                "note": "Ti le ky tu dich/zh bat thuong — can doi chieu nghia",
            })
        if _HEMORRHOID_ZH.search(src) and not _HEMORRHOID_VI.search(vi):
            flags.append({
                "i": i,
                "start": round(float(seg.get("start", 0)), 3),
                "end": round(float(seg.get("end", 0)), 3),
                "kind": "meaning_mismatch_heuristic",
                "source_text": src,
                "translated_text": vi,
                "note": "Zh co cum trĩ/thịt thò nhung vi khong nhac y tuong tu",
            })
    return flags


def write_content_review_full(work_dir: Path, reviews: list[dict]) -> Path:
    work_dir = Path(work_dir)
    need_action = [r for r in reviews if r.get("score") in ("sai", "gan_dung")]
    payload = {
        "segment_count": len(reviews),
        "need_action_count": len(need_action),
        "summary": {
            "khop": sum(1 for r in reviews if r.get("score") == "khop"),
            "gan_dung": sum(1 for r in reviews if r.get("score") == "gan_dung"),
            "sai": sum(1 for r in reviews if r.get("score") == "sai"),
        },
        "need_action": need_action,
        "reviews": reviews,
    }
    out = work_dir / "content_review_full.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Content review full: khop=%d gan_dung=%d sai=%d -> %s",
        payload["summary"]["khop"],
        payload["summary"]["gan_dung"],
        payload["summary"]["sai"],
        out.name,
    )
    return out


def sync_spoken_text_to_segment_files(
    work_dir: Path,
    spoken_cues: list[dict],
) -> dict:
    """Ghi cau TTS thuc doc (sau shorten) nguoc vao file segments — 1 nguon su that."""
    work_dir = Path(work_dir)
    if not spoken_cues:
        return {"updated": 0, "files": []}

    def _apply_spoken(segs: list[dict]) -> int:
        updates = 0
        if len(segs) == len(spoken_cues):
            pairs = zip(segs, spoken_cues)
        else:
            by_end = {}
            for c in spoken_cues:
                end_key = round(float(c.get("display_end", c.get("end", 0))), 2)
                by_end[end_key] = str(c.get("text") or "").strip()
            pairs = (
                (seg, {"text": by_end.get(round(float(seg.get("end", 0)), 2), "")})
                for seg in segs
            )
        for seg, cue in pairs:
            spoken = str(cue.get("text") or "").strip()
            if not spoken:
                continue
            prev = str(seg.get("text") or "")
            if spoken != prev:
                seg["text"] = spoken
                seg["spoken_synced"] = True
                updates += 1
        return updates

    changed: list[str] = []
    total = 0

    for name in ("segments_vi_goc.json", "segments_prosody.json", "segments_vi.json"):
        path = work_dir / name
        if not path.exists():
            continue
        segs = json.loads(path.read_text(encoding="utf-8"))
        file_updates = _apply_spoken(segs)
        if file_updates:
            path.write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")
            changed.append(f"{name}({file_updates})")
            total += file_updates

    if total:
        log.info("Sync spoken text -> %s", ", ".join(changed))
    else:
        log.info("Sync spoken text: khong doi (file da khop cues)")
    return {"updated": total, "files": changed}


def write_content_review_flags(work_dir: Path, flags: list[dict]) -> Path | None:
    work_dir = Path(work_dir)
    out = work_dir / "content_review_flags.json"
    if not flags:
        if out.exists():
            out.unlink()
        return None
    payload = {
        "count": len(flags),
        "note": "Loi noi dung — ra tay sau, khong xu ly trong buoc timing/merge",
        "flags": flags,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.warning("Co %d doan can ra tay noi dung -> %s", len(flags), out.name)
    return out


def write_shorten_flags(work_dir: Path, flags: list[dict]) -> Path | None:
    work_dir = Path(work_dir)
    if not flags:
        p = work_dir / "can_rut_gon_cau_dich.json"
        if p.exists():
            p.unlink()
        return None
    out = work_dir / "can_rut_gon_cau_dich.json"
    out.write_text(json.dumps(flags, ensure_ascii=False, indent=2), encoding="utf-8")
    log.warning("Co %d doan can rut gon cau dich -> %s", len(flags), out.name)
    return out


def write_dub_final_report(work_dir: Path, qa_rows: list[dict], *, video_dur: float = 0.0) -> dict:
    work_dir = Path(work_dir)
    timing_issues = []
    volume_issues = []
    shorten_flags = []
    cumulative_issues = []

    for r in qa_rows:
        play_sec = float(r.get("play", r.get("post_dur", 0)))
        slot_sec = float(r.get("slot", 0))
        drift_ms = abs(play_sec - slot_sec) * 1000
        cum_ms = abs(float(r.get("cum_offset_ms", 0)))
        if drift_ms > TIMING_WARN_MS:
            timing_issues.append({
                "i": r["i"],
                "drift_ms": round(drift_ms, 1),
                "slot_sec": slot_sec,
                "play_sec": play_sec,
                "drift_file_pct": r.get("drift_file_pct"),
            })
        if cum_ms > CUMULATIVE_WARN_MS:
            cumulative_issues.append({
                "i": r["i"],
                "cum_offset_ms": round(float(r.get("cum_offset_ms", 0)), 1),
            })
        mean_db = r.get("mean_db")
        if mean_db is not None:
            vol_drift = abs(float(mean_db) - LOUDNORM_TARGET_I)
            if vol_drift > VOLUME_WARN_DB:
                volume_issues.append({
                    "i": r["i"],
                    "mean_db": mean_db,
                    "target_lufs": LOUDNORM_TARGET_I,
                    "drift_db": round(vol_drift, 2),
                })
        if "qua_dai" in (r.get("flags") or []):
            shorten_flags.append({"i": r["i"], "text": r.get("text", ""), "drift_pct": r.get("drift_pct")})

    total_slot = sum(float(r.get("slot", 0)) for r in qa_rows)
    total_play = sum(float(r.get("play", r.get("post_dur", 0))) for r in qa_rows)
    stats = qa_summary_stats(qa_rows)
    report = {
        "segment_count": len(qa_rows),
        "video_duration_sec": round(float(video_dur), 3) if video_dur else None,
        "total_slot_sec": round(total_slot, 3),
        "total_voice_sec": round(total_play, 3),
        "coverage_pct": round(total_play / total_slot * 100, 1) if total_slot else 0,
        "qa_stats": stats,
        "timing_warn_ms_threshold": TIMING_WARN_MS,
        "volume_warn_db_threshold": VOLUME_WARN_DB,
        "timing_issues": timing_issues,
        "cumulative_issues": cumulative_issues,
        "cumulative_warn_ms": CUMULATIVE_WARN_MS,
        "final_cum_offset_ms": round(float(qa_rows[-1].get("cum_offset_ms", 0)), 1) if qa_rows else 0,
        "volume_issues": volume_issues,
        "shorten_flags": shorten_flags,
        "summary": (
            f"{len(timing_issues)} doan lech timing >{TIMING_WARN_MS}ms, "
            f"{len(cumulative_issues)} doan cum_offset >{CUMULATIVE_WARN_MS}ms, "
            f"{len(volume_issues)} doan volume lech >{VOLUME_WARN_DB}dB, "
            f"{len(shorten_flags)} doan qua dai (can rut gon)"
        ),
    }
    out = work_dir / "dub_final_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Dub final report: %s", report["summary"])
    write_fix_compare(work_dir, stats)
    return report


def qa_summary_stats(qa_rows: list[dict]) -> dict:
    """Thong ke QA: atempo, flag, cum_offset (play-based)."""
    atempo_n = sum(1 for r in qa_rows if abs(float(r.get("atempo", 1)) - 1.0) > 0.005)
    flagged = sum(1 for r in qa_rows if r.get("flags"))
    shorten_n = sum(1 for r in qa_rows if int(r.get("shorten_used") or 0) > 0)
    qua_dai = sum(1 for r in qa_rows if "qua_dai" in (r.get("flags") or []))
    qua_ngan = sum(1 for r in qa_rows if "qua_ngan" in (r.get("flags") or []))
    dead_zone = sum(
        1 for r in qa_rows
        if abs(float(r.get("atempo", 1)) - 1.0) <= 0.005
        and float(r.get("drift_file_pct", r.get("drift_pct", 0))) > 8
        and float(r.get("drift_pct", 0)) <= 15
        and "qua_dai" not in (r.get("flags") or [])
        and "qua_ngan" not in (r.get("flags") or [])
    )
    return {
        "segments": len(qa_rows),
        "atempo_applied": atempo_n,
        "flagged_segments": flagged,
        "shorten_used_count": shorten_n,
        "qua_dai_count": qua_dai,
        "qua_ngan_count": qua_ngan,
        "can_rut_gon_count": qua_dai,
        "dead_zone_ok_gia": dead_zone,
        "final_cum_offset_ms": round(float(qa_rows[-1].get("cum_offset_ms", 0)), 1) if qa_rows else 0,
    }


def write_fix_compare(work_dir: Path, after_stats: dict) -> dict:
    work_dir = Path(work_dir)
    baseline_path = work_dir / "dub_qa_baseline.json"
    baseline = {}
    if baseline_path.exists():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    compare = {
        "before": baseline,
        "after": {**after_stats, "label": "after_fix"},
        "delta": {},
    }
    if baseline:
        compare["delta"] = {
            "atempo_applied": after_stats.get("atempo_applied", 0) - baseline.get("atempo_applied", 0),
            "final_cum_offset_ms": round(
                after_stats.get("final_cum_offset_ms", 0) - baseline.get("final_cum_offset_ms", 0), 1,
            ),
            "flagged_segments": after_stats.get("flagged_segments", 0) - baseline.get("flagged", 0),
            "dead_zone_ok_gia": after_stats.get("dead_zone_ok_gia", 0) - baseline.get("dead_zone_ok_gia", 0),
        }
    out = work_dir / "dub_fix_compare.json"
    out.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Fix compare: atempo %s->%s, cum_offset %sms->%sms",
        baseline.get("atempo_applied", "?"),
        after_stats.get("atempo_applied"),
        baseline.get("final_cum_offset_ms", "?"),
        after_stats.get("final_cum_offset_ms"),
    )
    return compare
