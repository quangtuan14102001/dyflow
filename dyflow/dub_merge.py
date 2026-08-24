"""Gop doan dub lien ke de mo rong slot (fix qua_dai do slot ngan)."""
from __future__ import annotations

import json
from pathlib import Path

from .translate_llm import (
    retranslate_segment_faithful,
    segment_slot,
    target_chars_for_slot,
    translate_segments_budgeted,
)
from .utils import log


def merge_adjacent_pairs(
    segments: list[dict],
    pairs: list[tuple[int, int]],
) -> tuple[list[dict], list[int]]:
    """Gop cap (left, right) lien ke; tra ve danh sach moi + chi so doan da gop (left truoc merge)."""
    segs = [dict(s) for s in segments]
    merged_indices: list[int] = []
    for left, right in sorted(pairs, key=lambda p: p[0], reverse=True):
        if right != left + 1:
            raise ValueError(f"Cap khong lien ke: ({left}, {right})")
        if left < 0 or right >= len(segs):
            raise ValueError(f"Chi so vuot ngoai: ({left}, {right}) / {len(segs)} doan")
        a, b = segs[left], segs[right]
        combined: dict = {
            "start": float(a["start"]),
            "end": float(b["end"]),
            "source_text": " ".join(
                x.strip()
                for x in (str(a.get("source_text") or ""), str(b.get("source_text") or ""))
                if x.strip()
            ),
            "text": " ".join(
                x.strip()
                for x in (str(a.get("text") or ""), str(b.get("text") or ""))
                if x.strip()
            ),
        }
        segs = segs[:left] + [combined] + segs[right + 1 :]
        # Chi so doan gop dich lai sau khi da shift do cac merge phia duoi
        merged_indices = [
            idx if idx <= left else idx - 1 for idx in merged_indices
        ]
        merged_indices.append(left)
    merged_indices.sort()
    return segs, merged_indices


def refresh_segment_budgets(
    segments: list[dict],
    *,
    video_dur: float,
    chars_per_sec: float,
) -> None:
    for i, seg in enumerate(segments):
        slot = segment_slot(segments, i, video_dur)
        seg["slot_sec"] = round(slot, 3)
        seg["target_chars"] = target_chars_for_slot(slot, chars_per_sec=chars_per_sec)
        seg.pop("spoken_synced", None)


def retranslate_merged_segments(
    segments: list[dict],
    merged_indices: list[int],
    *,
    video_dur: float,
    chars_per_sec: float,
    work_dir: Path | str | None = None,
) -> list[dict]:
    """Dich lai cac doan vua gop theo slot moi."""
    if not merged_indices:
        return segments

    batch = [segments[i] for i in merged_indices]
    translated = translate_segments_budgeted(
        batch,
        video_dur=video_dur,
        target="vi",
        chars_per_sec=chars_per_sec,
        work_dir=None,
    )
    out = [dict(s) for s in segments]
    for idx, seg in zip(merged_indices, translated):
        out[idx] = {**out[idx], **seg}
        out[idx].pop("spoken_synced", None)
    refresh_segment_budgets(out, video_dur=video_dur, chars_per_sec=chars_per_sec)
    if work_dir:
        wp = Path(work_dir)
        wp.mkdir(parents=True, exist_ok=True)
        log_path = wp / "merge_dub_log.json"
        prev = []
        if log_path.exists():
            try:
                prev = json.loads(log_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                prev = []
        if not isinstance(prev, list):
            prev = [prev]
        prev.append({"merged_indices": merged_indices, "segments": batch})
        log_path.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def find_segment_near_start(segments: list[dict], start: float, tol: float = 0.25) -> int | None:
    for i, seg in enumerate(segments):
        if abs(float(seg.get("start", 0)) - start) <= tol:
            return i
    return None


def merge_qua_dai_plan(
    segments: list[dict],
    qua_dai_ids: list[int],
    *,
    prefer_forward: bool = True,
) -> list[tuple[int, int]]:
    """Sinh cap gop: moi id qua_dai gop voi lang gieng (mac dinh i+1)."""
    n = len(segments)
    pairs: list[tuple[int, int]] = []
    used: set[int] = set()
    for seg_id in sorted(qua_dai_ids):
        if seg_id in used or seg_id < 0 or seg_id >= n:
            continue
        if prefer_forward and seg_id + 1 < n and seg_id + 1 not in used:
            pairs.append((seg_id, seg_id + 1))
            used.update((seg_id, seg_id + 1))
        elif seg_id - 1 >= 0 and seg_id - 1 not in used:
            pairs.append((seg_id - 1, seg_id))
            used.update((seg_id - 1, seg_id))
        else:
            log.warning("Khong gop duoc doan #%d — khong con lang gieng trong", seg_id)
    return pairs
