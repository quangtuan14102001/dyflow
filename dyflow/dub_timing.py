"""Timing planner: dat TTS len timeline tuyet doi cua video goc.

timeline_start = source_start (Whisper/phu de).
Khoang lang giua cau = silence — khong keo giong, khong keo cau sau ve truoc.
Khong dung duration TTS cau truoc de tinh start cau sau.
Khong cat TTS de ep vao slot tieng Trung.
"""
from __future__ import annotations

from .dub_constants import (
    DUB_ENCODER_PAD_SEC,
    DUB_LARGE_OVERLAP_CROSSFADE_MAX,
    DUB_MICRO_OVERLAP_SEC,
    DUB_OVERFLOW_SEMANTIC_MAX_TRIES,
    DUB_OVERFLOW_SEVERE_COV,
    DUB_OVERFLOW_SEVERE_EXTRA_TRIES,
    DUB_OVERFLOW_TIER4_EXTRA_TRIES,
    DUB_SHORTEN_MIN_ABS_SEC,
    DUB_SHORTEN_MIN_DEFICIT_RATIO,
    DUB_SHORTEN_TINY_ABS_SEC,
    DUB_SHORTEN_TINY_DEFICIT_SEC,
    DUB_SHORTEN_TINY_RATIO,
    DUB_SPEECH_SPILL_MIN,
    DUB_SPEECH_SPILL_RATIO,
    QA_AUDIO_TRUNC_SLACK_SEC,
    TTS_ATEMPO_SAFE_MAX,
    TTS_ATEMPO_SEVERE_MAX,
    TTS_RATE_MATCH_FAIL_RATIO,
)


def next_start_of(segments: list[dict], i: int, video_dur: float) -> float:
    if i + 1 < len(segments):
        return float(segments[i + 1]["start"])
    return float(video_dur or 0.0) or (float(segments[i].get("end") or 0) + 1.5)


def speech_slot(seg: dict) -> float:
    start = float(seg.get("start") or 0)
    end = float(seg.get("end") or start)
    return max(0.12, end - start)


def hard_limit(segments: list[dict], i: int, video_dur: float) -> float:
    """Moc khong duoc de len cau sau. Phan sau do la im lang/nhac that."""
    start = float(segments[i]["start"])
    nxt = next_start_of(segments, i, video_dur)
    return max(0.12, nxt - start - 0.04)


def budget_slot(segments: list[dict], i: int, video_dur: float) -> float:
    """Ngan sach noi (ky tu / rut gon): cua thoai + spill nhe, khong gom doan nhac dai."""
    speech = speech_slot(segments[i])
    hard = hard_limit(segments, i, video_dur)
    spill = min(
        max(0.0, hard - speech),
        max(DUB_SPEECH_SPILL_MIN, speech * DUB_SPEECH_SPILL_RATIO),
    )
    return max(0.12, min(hard, speech + spill))


def plan_placement(
    *,
    source_start: float,
    source_end: float,
    next_start: float,
    tts_dur: float,
) -> dict:
    start = float(source_start)
    speech = max(0.12, float(source_end) - start)
    hard = max(0.12, float(next_start) - start - 0.04)
    tts = max(0.0, float(tts_dur))
    play_end = start + tts
    would_overlap = play_end > float(next_start) - 0.02
    needs_shorten = tts > hard * (1.0 + TTS_RATE_MATCH_FAIL_RATIO)
    required_tempo = (tts / hard) if hard > 0 and tts > hard else 1.0
    safe_tempo = 1.0
    if required_tempo > 1.0:
        safe_tempo = min(float(TTS_ATEMPO_SAFE_MAX), required_tempo)
    return {
        "timeline_start": start,
        "timeline_end": play_end,
        "speech_slot": round(speech, 4),
        "hard_limit": round(hard, 4),
        "tts_dur": tts,
        "would_overlap": would_overlap,
        "needs_shorten": needs_shorten,
        "required_tempo": round(required_tempo, 4),
        "safe_tempo": round(safe_tempo, 4),
    }


def decide_fit(
    *,
    tts_dur: float,
    hard_limit: float,
    speech_slot: float,
    source_start: float,
    next_start: float,
    atempo_cap: float | None = None,
) -> dict:
    """Quyet dinh fit: keep / atempo / overflow. Khong bao gio de xuat cat TTS.

    overflow = can tua > atempo_cap de khong de len cau sau.
    Mac dinh cap = TTS_ATEMPO_SAFE_MAX (1.15). Sau lan rut 3 (severe, rut DAT)
    caller truyen TTS_ATEMPO_SEVERE_MAX. Khong tua hon muc can.
    """
    start = float(source_start)
    nxt = float(next_start)
    hard = max(0.12, float(hard_limit))
    speech = max(0.12, float(speech_slot))
    tts = max(0.0, float(tts_dur))
    required = (tts / hard) if tts > hard else 1.0
    cap = float(atempo_cap if atempo_cap is not None else TTS_ATEMPO_SAFE_MAX)
    tempo = 1.0
    action = "keep"
    if tts <= hard:
        if tts < speech * 0.85:
            action = "keep_short"
        fitted = tts
    elif required <= cap + 0.001:
        action = "atempo"
        tempo = required  # dung bang muc can, khong len cap neu khong can
        fitted = tts / tempo
    else:
        action = "overflow"
        tempo = cap
        fitted = tts / cap
    overlap_sec = max(0.0, start + fitted - (nxt - 0.02))
    return {
        "action": action,
        "tempo": round(tempo, 4),
        "required_tempo": round(required, 4),
        "fitted_dur": round(fitted, 4),
        "overlap_sec": round(overlap_sec, 4),
        "would_overlap": overlap_sec > 0.02,
        "speech_slot": round(speech, 4),
        "hard_limit": round(hard, 4),
        "atempo_cap": round(cap, 4),
    }


def semantic_shorten_budget(
    *,
    text: str,
    tts_dur: float,
    available_slot: float,
    chars_per_sec: float,
    attempt: int = 1,
) -> dict:
    """Ngan sach rut gon overflow: thieu dung (tts sau 1.15x - slot), khong san 32 ky tu.

    target_chars = cps * slot kha dung. cps uu tien toc do do duoc cua chinh cau nay
    (len(text)/tts_dur), fallback chars_per_sec cau hinh. Lan 2 siet hon.
    """
    slot = max(0.12, float(available_slot))
    tts = max(0.05, float(tts_dur))
    n = max(1, len(text or ""))
    measured = n / tts
    cfg_cps = max(4.0, float(chars_per_sec or 12.0))
    cps = measured if 4.0 <= measured <= 30.0 else cfg_cps
    deficit_sec = tts - slot
    target_chars = max(1, int(round(cps * slot)))
    if int(attempt) >= 4:
        # Tang 4: siet manh — muc tieu bo ~1 menh de phu.
        overflow_ratio = min(1.0, slot / tts)
        tighter = int(round(min(target_chars, n * overflow_ratio) * 0.55))
        target_chars = max(1, tighter)
    elif int(attempt) >= 3:
        overflow_ratio = min(1.0, slot / tts)
        tighter = int(round(min(target_chars, n * overflow_ratio) * 0.70))
        target_chars = max(1, tighter)
    elif int(attempt) >= 2:
        overflow_ratio = min(1.0, slot / tts)
        tighter = int(round(min(target_chars, n * overflow_ratio) * 0.85))
        target_chars = max(1, tighter)
    target_chars = min(target_chars, max(1, n - 1))
    return {
        "target_chars": int(target_chars),
        "target_dur": round(slot, 3),
        "deficit_sec": round(deficit_sec, 3),
        "measured_cps": round(cps, 2),
        "tts_dur": round(tts, 3),
        "available_slot": round(slot, 3),
        "attempt": int(attempt),
        "tier4": int(attempt) >= 4,
    }


def shorten_attempt_closes_enough(
    tts_before: float,
    tts_after: float,
    deficit_sec: float,
) -> bool:
    """Lan rut dat neu TTS giam du so voi deficit. Bo 1 trang tu (~0.2s / deficit 2s+) = False."""
    closed = float(tts_before) - float(tts_after)
    need = max(0.0, float(deficit_sec))
    if need >= float(DUB_SHORTEN_TINY_DEFICIT_SEC):
        min_need = max(float(DUB_SHORTEN_MIN_ABS_SEC), need * float(DUB_SHORTEN_MIN_DEFICIT_RATIO))
    else:
        min_need = max(float(DUB_SHORTEN_TINY_ABS_SEC), need * float(DUB_SHORTEN_TINY_RATIO))
    return closed + 1e-9 >= min_need


def overflow_semantic_try_budget(cov_ratio: float, attempt_done: int) -> int:
    """Sau N lan co so: severe +1; tang 4 them 1 lan bo menh de phu neu van tran."""
    base = int(DUB_OVERFLOW_SEMANTIC_MAX_TRIES)
    budget = base
    if int(attempt_done) >= base and float(cov_ratio) > float(DUB_OVERFLOW_SEVERE_COV):
        budget = base + int(DUB_OVERFLOW_SEVERE_EXTRA_TRIES)
    # Tang 4: luon cho them 1 lan aggressive sau khi da het severe budget.
    severe_cap = base + int(DUB_OVERFLOW_SEVERE_EXTRA_TRIES)
    if int(attempt_done) >= severe_cap and float(cov_ratio) > 1.0:
        budget = max(budget, severe_cap + int(DUB_OVERFLOW_TIER4_EXTRA_TRIES))
    return budget


def borrow_time_from_next(
    *,
    need_sec: float,
    next_source_start: float,
    next_source_end: float,
    next_timeline_start: float,
    following_start: float,
) -> dict:
    """Tang 3: muon thoi gian bang cach lui (tre) timeline_start doan sau.

    Chi muon vao khoang cho SAU khi doan sau ket thuc noi (toi following_start).
    Khong bao gio dat timeline_start < source_start (khong bat dau som hon mieng mo).
    """
    need = max(0.0, float(need_sec))
    src_start = float(next_source_start)
    src_end = max(src_start + 0.12, float(next_source_end))
    cur_tl = max(src_start, float(next_timeline_start))
    after = float(following_start)
    speech = max(0.12, src_end - src_start)
    # Doan sau van can cho speech; moc start tre nhat = after - speech.
    max_tl = max(src_start, after - speech - 0.04)
    room = max(0.0, max_tl - cur_tl)
    borrowed = min(need, room)
    if borrowed < 0.02:
        return {
            "borrowed_sec": 0.0,
            "new_next_start": round(cur_tl, 4),
            "room_sec": round(room, 4),
            "applied": False,
        }
    new_tl = cur_tl + borrowed
    return {
        "borrowed_sec": round(borrowed, 4),
        "new_next_start": round(new_tl, 4),
        "room_sec": round(room, 4),
        "applied": True,
    }


def large_overlap_crossfade(overlap_sec: float) -> float:
    """Tang 5: crossfade = min(overlap, 0.8s) — khong tran overlap."""
    ov = max(0.0, float(overlap_sec))
    if ov <= 0.01:
        return 0.0
    return round(min(ov, float(DUB_LARGE_OVERLAP_CROSSFADE_MAX)), 4)


def atempo_cap_for_attempt(attempt: int, *, shorten_effective: bool) -> float:
    """Lan 1-2: 1.15. Lan 3 CHI khi rut DAT: TTS_ATEMPO_SEVERE_MAX. Khong dung tua de bu rut yeu."""
    if int(attempt) >= 3 and shorten_effective:
        return float(TTS_ATEMPO_SEVERE_MAX)
    return float(TTS_ATEMPO_SAFE_MAX)


def expected_dur_after_atempo(tts_dur_raw: float, atempo_applied: float) -> float:
    """Thoi luong ky vong neu chi tua ffmpeg, khong cat file."""
    tempo = max(1.0, float(atempo_applied) or 1.0)
    return max(0.0, float(tts_dur_raw)) / tempo


def is_audio_truncated(
    tts_dur_raw: float,
    tts_dur: float,
    atempo_applied: float = 1.0,
    slack_sec: float = QA_AUDIO_TRUNC_SLACK_SEC,
) -> bool:
    """True khi file ngan hon phep tinh atempo thuan tuy — dau hieu cat vat ly.

    Nguong: tts_dur < tts_dur_raw / atempo_applied - slack (mac dinh 0.5s).
    """
    expected = expected_dur_after_atempo(tts_dur_raw, atempo_applied)
    return float(tts_dur) + 1e-9 < expected - max(0.0, float(slack_sec))


def overlap_close_plan(
    *,
    tts_dur: float,
    slot: float,
    atempo_already: float = 1.0,
    cap: float = TTS_ATEMPO_SEVERE_MAX,
    encoder_pad: float = DUB_ENCODER_PAD_SEC,
    allow_speech_trim: bool = False,
    allow_micro_overlap: bool = False,
    micro_overlap_sec: float = DUB_MICRO_OVERLAP_SEC,
    allow_large_overlap: bool = False,
) -> dict:
    """Ke hoach khop file TTS vao hard_limit ma khong de cau sau.

    Thu tu: atempo con lai (toi cap) -> cat pad encoder -> micro-overlap
    (neu bat, <=250ms) -> large_overlap (tang 5, neu bat) -> overflow.
    Khong cat loi noi (speech_trim da cam).
    """
    hard = max(0.12, float(slot))
    tts = max(0.0, float(tts_dur))
    already = max(1.0, float(atempo_already) or 1.0)
    cap_v = max(already, float(cap))
    room = cap_v / already
    _ = allow_speech_trim  # cam cat loi noi — khong dung
    if tts <= hard + 0.02:
        return {
            "action": "keep",
            "tempo": 1.0,
            "trim": False,
            "pad_trim": False,
            "speech_trim": False,
            "micro_overlap": False,
            "large_overlap": False,
            "fits": True,
            "leftover": 0.0,
        }
    needed = tts / hard
    extra = min(needed, room)
    if extra < 1.005:
        extra = 1.0
    after = tts / extra if extra > 1.0 else tts
    leftover = max(0.0, after - hard)
    base = {
        "tempo": round(extra, 4),
        "leftover": round(leftover, 4),
        "needed": round(needed, 4),
        "room": round(room, 4),
        "speech_trim": False,
        "micro_overlap": False,
        "large_overlap": False,
    }
    if leftover <= 0.02:
        return {
            **base,
            "action": "atempo" if extra > 1.0 else "keep",
            "trim": False,
            "pad_trim": False,
            "fits": True,
        }
    if leftover <= float(encoder_pad) + 1e-6:
        return {
            **base,
            "action": "pad_trim",
            "trim": True,
            "pad_trim": True,
            "fits": True,
        }
    if allow_micro_overlap and leftover <= float(micro_overlap_sec) + 1e-6:
        return {
            **base,
            "action": "micro_overlap",
            "trim": False,
            "pad_trim": False,
            "micro_overlap": True,
            "fits": True,
            "overlap_sec": round(leftover, 4),
            "crossfade_sec": round(leftover, 4),
        }
    if allow_large_overlap and leftover > 0.02:
        xfade = large_overlap_crossfade(leftover)
        return {
            **base,
            "action": "large_overlap",
            "trim": False,
            "pad_trim": False,
            "large_overlap": True,
            "fits": True,
            "overlap_sec": round(leftover, 4),
            "crossfade_sec": xfade,
        }
    return {
        **base,
        "action": "overflow",
        "trim": False,
        "pad_trim": False,
        "fits": False,
    }


def overflow_atempo_plan(
    *,
    tts_dur: float,
    slot: float,
    attempt: int = 1,
    shorten_effective: bool = True,
) -> dict:
    """Atempo dong: tempo = min(tts/slot, cap). Khong tua hon muc can, khong vuot tran.

    Vuot cap -> action overflow (fail-closed). Khong 'tang toi khi vua'.
    """
    hard = max(0.12, float(slot))
    tts = max(0.0, float(tts_dur))
    needed = (tts / hard) if tts > hard else 1.0
    cap = atempo_cap_for_attempt(attempt, shorten_effective=shorten_effective)
    if needed <= 1.0 + 1e-9:
        tempo = 1.0
        action = "keep"
        fits = True
    else:
        tempo = min(needed, cap)
        fits = needed <= cap + 0.001
        action = "atempo" if fits else "overflow"
    return {
        "needed": round(needed, 4),
        "tempo": round(tempo, 4),
        "cap": round(cap, 4),
        "fits": fits,
        "action": action,
        "attempt": int(attempt),
        "severe": cap > float(TTS_ATEMPO_SAFE_MAX) + 1e-9,
        "shorten_effective": bool(shorten_effective),
    }
