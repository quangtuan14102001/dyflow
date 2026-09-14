"""Gop doan dub lien ke de mo rong slot (fix qua_dai do slot ngan)."""
from __future__ import annotations

import json
from pathlib import Path

from .dub_constants import (
    DUB_GROUP_MAX_CHARS,
    DUB_GROUP_MAX_DUR_SEC,
    DUB_GROUP_MAX_GAP_SEC,
    DUB_GROUP_TINY_SLOT_SEC,
    DUB_OVERFLOW_MAX_MERGES,
)
from .translate_llm import (
    retranslate_segment_faithful,
    segment_slot,
    target_chars_for_slot,
    translate_segments_budgeted,
)
from .utils import log

_SENT_END = ".!?。！？…"


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
            **a,
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


def _incomplete_utterance(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if t[-1] in _SENT_END:
        return False
    return len(t) < 24


def same_speaker(a: dict, b: dict) -> bool:
    sid = str(a.get("speaker_id") or a.get("voice_id") or "")
    nid = str(b.get("speaker_id") or b.get("voice_id") or "")
    if sid and nid and sid != nid:
        return False
    return True


def _same_speaker(a: dict, b: dict) -> bool:
    return same_speaker(a, b)


def next_live_index(n: int, i: int, skip_idx: set[int]) -> int | None:
    j = i + 1
    while j < n and j in skip_idx:
        j += 1
    return j if j < n else None


def overflow_merge_plan(
    segments: list[dict],
    i: int,
    *,
    skip_idx: set[int] | None = None,
    overflow_merges: int = 0,
    max_merges: int = DUB_OVERFLOW_MAX_MERGES,
) -> tuple[int | None, str]:
    """Chon cau sau de gop khi overflow. Tra (index, ly_do).

    ly_do: gop | cuoi_clip | khac_nhan_vat | max_gop
    """
    skip = skip_idx or set()
    nxt = next_live_index(len(segments), i, skip)
    if nxt is None:
        return None, "cuoi_clip"
    if int(overflow_merges) >= int(max_merges):
        return None, "max_gop"
    if not same_speaker(segments[i], segments[nxt]):
        return None, "khac_nhan_vat"
    return nxt, "gop"


def plan_tiny_slot_merges(
    segments: list[dict],
    *,
    video_dur: float | None = None,
    min_slot: float = 0.95,
    max_gap: float = 0.45,
) -> list[tuple[int, int]]:
    """Gop shot qua ngan khi gap toi doan sau rat nho (neu khong cau Viet se bi cat nat)."""
    n = len(segments)
    pairs: list[tuple[int, int]] = []
    used: set[int] = set()
    for i, seg in enumerate(segments):
        if i in used or i + 1 >= n:
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        orig = max(0.0, end - start)
        if orig >= float(min_slot):
            continue
        nxt = segments[i + 1]
        nstart = float(nxt.get("start") or end)
        gap = max(0.0, nstart - end)
        if gap > float(max_gap):
            continue
        if not _same_speaker(seg, nxt):
            continue
        pairs.append((i, i + 1))
        used.update((i, i + 1))
    return pairs


def plan_utterance_groups(
    segments: list[dict],
    *,
    max_gap: float = DUB_GROUP_MAX_GAP_SEC,
    max_dur: float = DUB_GROUP_MAX_DUR_SEC,
    max_chars: int = DUB_GROUP_MAX_CHARS,
    tiny_slot: float = DUB_GROUP_TINY_SLOT_SEC,
) -> list[tuple[int, int]]:
    """Gop manh Whisper/dich thanh cau hoan chinh cung nhan vat."""
    n = len(segments)
    pairs: list[tuple[int, int]] = []
    used: set[int] = set()
    for i, seg in enumerate(segments):
        if i in used or i + 1 >= n:
            continue
        nxt = segments[i + 1]
        end = float(seg.get("end") or 0)
        nstart = float(nxt.get("start") or end)
        gap = nstart - end
        if gap > float(max_gap) or gap < -0.35:
            continue
        if not _same_speaker(seg, nxt):
            continue
        start = float(seg.get("start") or 0)
        nend = float(nxt.get("end") or nstart)
        span = nend - start
        a_txt = str(seg.get("text") or "")
        b_txt = str(nxt.get("text") or "")
        chars = len(a_txt) + len(b_txt) + 1
        orig = max(0.0, end - start)
        should = orig < float(tiny_slot) or _incomplete_utterance(a_txt)
        if should and span <= float(max_dur) and chars <= int(max_chars):
            pairs.append((i, i + 1))
            used.update((i, i + 1))
    return pairs


def group_utterances(
    segments: list[dict],
    *,
    max_gap: float = DUB_GROUP_MAX_GAP_SEC,
    max_dur: float = DUB_GROUP_MAX_DUR_SEC,
    max_chars: int = DUB_GROUP_MAX_CHARS,
    tiny_slot: float = DUB_GROUP_TINY_SLOT_SEC,
    rounds: int = 12,
) -> list[dict]:
    """Lap gop cap lien ke den khi het manh vo cau."""
    segs = [dict(s) for s in segments]
    for _ in range(max(1, int(rounds))):
        pairs = plan_utterance_groups(
            segs,
            max_gap=max_gap,
            max_dur=max_dur,
            max_chars=max_chars,
            tiny_slot=tiny_slot,
        )
        if not pairs:
            break
        segs, _ = merge_adjacent_pairs(segs, pairs)
    return segs


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
