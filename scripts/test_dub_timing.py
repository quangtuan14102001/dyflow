#!/usr/bin/env python3
"""Kiem tra timing planner: moc tuyet doi, khong cum_offset, khong cat TTS.

  python scripts/test_dub_timing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_audit import audit_qa_rows  # noqa: E402
from dyflow.dub_merge import (  # noqa: E402
    group_utterances,
    overflow_merge_plan,
    plan_tiny_slot_merges,
)
from dyflow.dub_timing import (  # noqa: E402
    budget_slot,
    decide_fit,
    hard_limit,
    overlap_close_plan,
    plan_placement,
    semantic_shorten_budget,
)
from dyflow.dub_tts import _finite_loudness  # noqa: E402
from dyflow.translate_llm import (  # noqa: E402
    _dub_reslot_prompt,
    _dub_translate_prompt,
    _parse_line_rows,
    segment_slot,
)


def _fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def test_absolute_timeline() -> None:
    """A TTS ngan, B TTS dai: B van bat dau dung source_start."""
    a = plan_placement(source_start=0.0, source_end=2.0, next_start=5.0, tts_dur=1.5)
    b = plan_placement(source_start=5.0, source_end=7.0, next_start=10.0, tts_dur=3.0)
    c = plan_placement(source_start=10.0, source_end=15.0, next_start=20.0, tts_dur=4.0)
    if a["timeline_start"] != 0.0 or abs(a["timeline_end"] - 1.5) > 1e-6:
        _fail(f"A placement {a}")
    if a["would_overlap"] or a["needs_shorten"]:
        _fail(f"A khong duoc overlap/shorten: {a}")
    if b["timeline_start"] != 5.0:
        _fail(f"B phai bat dau 5.0, got {b['timeline_start']}")
    if abs(b["timeline_end"] - 8.0) > 1e-6:
        _fail(f"B play het TTS 3s -> 8.0, got {b['timeline_end']}")
    if b["would_overlap"]:
        _fail("B 8s khong de len C o 10s")
    if c["timeline_start"] != 10.0:
        _fail(f"C start {c['timeline_start']}")
    print("OK timeline tuyet doi: A=0 B=5 C=10 (khong cong duration TTS truoc)")


def test_pause_is_not_speech() -> None:
    """Slot 16s vi Whisper gom nhac: TTS 1.8s khong can rut gon, khong overlap."""
    p = plan_placement(source_start=40.0, source_end=41.8, next_start=57.0, tts_dur=1.8)
    if p["needs_shorten"] or p["would_overlap"]:
        _fail(f"pause dai khong phai loi TTS: {p}")
    if p["timeline_start"] != 40.0:
        _fail("phai dat dung 40.0")
    segs = [
        {"start": 40.0, "end": 41.8, "text": "x"},
        {"start": 57.0, "end": 59.0, "text": "y"},
    ]
    bud = budget_slot(segs, 0, 90.0)
    hard = hard_limit(segs, 0, 90.0)
    if hard < 15:
        _fail(f"hard_limit phai ~16s, got {hard}")
    if bud > 4.0:
        _fail(f"ngan sach khong duoc gom 16s nhac: {bud}")
    print("OK slot nhac/im lang: ngan sach ~speech, TTS ngan khong fail")


def test_no_hard_trim_decision() -> None:
    """TTS dai hon cua thoai nhung van truoc cau sau: noi vao pause, khong cat."""
    p = plan_placement(source_start=10.0, source_end=11.1, next_start=14.0, tts_dur=2.6)
    if p["timeline_start"] != 10.0:
        _fail("start")
    if abs(p["timeline_end"] - 12.6) > 1e-6:
        _fail(f"phai giu 2.6s TTS, got {p['timeline_end']}")
    if p["would_overlap"]:
        _fail("2.6s trong cua den 14s khong overlap")
    if p["needs_shorten"]:
        _fail("chua de len cau sau — khong rut gon")
    print("OK khong cat TTS khi con khoang lang den cau sau")


def test_group_fragments() -> None:
    segs = [
        {"start": 0.0, "end": 0.4, "text": "Cô ấy", "speaker_id": "a"},
        {"start": 0.5, "end": 1.0, "text": "thỉnh thoảng", "speaker_id": "a"},
        {"start": 1.1, "end": 2.4, "text": "thật sự không đáng tiêu tiền.", "speaker_id": "a"},
        {"start": 8.0, "end": 9.0, "text": "Khác hẳn.", "speaker_id": "a"},
        {"start": 9.1, "end": 10.0, "text": "Đổi người.", "speaker_id": "b"},
    ]
    grouped = group_utterances(segs)
    if len(grouped) != 3:
        _fail(f"ky vong 3 cau (1 gop + 2 rieng), got {len(grouped)} {grouped}")
    if grouped[0]["start"] != 0.0 or abs(grouped[0]["end"] - 2.4) > 1e-6:
        _fail(f"group 0 timestamp {grouped[0]}")
    if "Cô ấy" not in grouped[0]["text"] or "tiêu tiền" not in grouped[0]["text"]:
        _fail(f"text gop: {grouped[0]['text']}")
    tiny = plan_tiny_slot_merges(
        [
            {"start": 0.0, "end": 0.6, "text": "a", "speaker_id": "dad"},
            {"start": 0.7, "end": 2.0, "text": "b", "speaker_id": "dad"},
            {"start": 8.0, "end": 8.5, "text": "c", "speaker_id": "dad"},
        ],
        min_slot=0.95,
        max_gap=0.45,
    )
    if tiny != [(0, 1)]:
        _fail(f"tiny merge {tiny}")
    print("OK gop manh Whisper thanh cau; khong gop qua pause/doi nhan vat")


def test_budget_not_pause() -> None:
    segs = [
        {"start": 0.0, "end": 2.0, "text": "aa"},
        {"start": 12.0, "end": 13.0, "text": "bb"},
    ]
    slot = segment_slot(segs, 0, 20.0)
    if slot > 4.0:
        _fail(f"segment_slot gom nhac: {slot}")
    print(f"OK ngan sach dich {slot:.2f}s (khong phai 12s pause)")


def test_overflow_beyond_atempo() -> None:
    """2.6s vao 1.1s: tua 1.15x van de — phai overflow, khong cat."""
    d = decide_fit(
        tts_dur=2.6, hard_limit=1.1, speech_slot=1.1,
        source_start=10.0, next_start=11.14,
    )
    if d["action"] != "overflow":
        _fail(f"2.6/1.1 phai overflow, got {d}")
    if d["tempo"] != 1.15:
        _fail(f"tempo cap 1.15, got {d['tempo']}")
    if d["overlap_sec"] < 1.0:
        _fail(f"sau 1.15x van de ~1.16s, got {d['overlap_sec']}")
    d2 = decide_fit(
        tts_dur=4.1, hard_limit=1.5, speech_slot=1.5,
        source_start=20.0, next_start=21.54,
    )
    if d2["action"] != "overflow" or d2["required_tempo"] < 2.5:
        _fail(f"4.1/1.5 phai overflow, got {d2}")
    mild = decide_fit(
        tts_dur=2.4, hard_limit=2.2, speech_slot=2.0,
        source_start=0.0, next_start=2.24,
    )
    if mild["action"] != "atempo":
        _fail(f"2.4/2.2 phai atempo, got {mild}")
    pause = decide_fit(
        tts_dur=1.8, hard_limit=16.2, speech_slot=1.8,
        source_start=40.0, next_start=56.24,
    )
    if pause["action"] != "keep" or pause["would_overlap"]:
        _fail(f"TTS ngan trong pause dai phai keep: {pause}")
    print("OK overflow: 2.6/1.1 va 4.1/1.5 khong cat; pause dai keep")


def test_overflow_merge_edges() -> None:
    last = [{"start": 10.0, "end": 11.1, "text": "a", "speaker_id": "dad"}]
    idx, why = overflow_merge_plan(last, 0)
    if (idx, why) != (None, "cuoi_clip"):
        _fail(f"cuoi clip: {idx}, {why}")
    duo = [
        {"start": 10.0, "end": 11.1, "text": "a", "speaker_id": "dad"},
        {"start": 11.2, "end": 13.0, "text": "b", "speaker_id": "aunt"},
    ]
    idx, why = overflow_merge_plan(duo, 0)
    if (idx, why) != (None, "khac_nhan_vat"):
        _fail(f"khac nhan vat: {idx}, {why}")
    same = [
        {"start": 10.0, "end": 11.1, "text": "a", "speaker_id": "dad"},
        {"start": 11.2, "end": 13.0, "text": "b", "speaker_id": "dad"},
    ]
    idx, why = overflow_merge_plan(same, 0, overflow_merges=3)
    if (idx, why) != (None, "max_gop"):
        _fail(f"max gop: {idx}, {why}")
    idx, why = overflow_merge_plan(same, 0, overflow_merges=0)
    if (idx, why) != (1, "gop"):
        _fail(f"gop duoc: {idx}, {why}")
    print("OK overflow merge: cuoi_clip / khac_nhan_vat / max 3 lan")


def test_shorten_16_loses_meaning() -> None:
    from dyflow.translate_llm import hard_shorten_text

    src = "Đến lúc đó tôi mới nhận ra mình mệt mỏi thế nào"
    naive = src[:16]
    kept = hard_shorten_text(src, 16)
    if "mệt mỏi" not in src:
        _fail("cau mau")
    if naive == src or "mệt mỏi" in naive:
        _fail(f"cat 16 ky tu van giu du nghia? {naive!r}")
    # San 32 ky tu: van cat, nhung overflow path MOI khong goi ham nay.
    if kept != src and len(kept) < 32:
        _fail(f"hard_shorten 16 khong duoc xuong duoi san 32: {kept!r}")
    print(f"OK cat 16 ky tu se ra {naive!r} — overflow path khong dung nua")


def test_loudnorm_skip_inf() -> None:
    if _finite_loudness("-inf") or _finite_loudness("inf") or _finite_loudness(None):
        _fail("loudness -inf phai skip")
    if not _finite_loudness(-16.2):
        _fail("-16.2 hop le")
    print("OK loudnorm skip measured_I=-inf")


def test_qa_pause_is_pass() -> None:
    rows = [
        {
            "i": 0,
            "start": 0.0,
            "slot": 1.8,
            "hard_limit": 10.7,
            "play": 1.8,
            "speed": 1.0,
            "flags": ["qua_ngan"],
            "text": "ok",
        },
        {
            "i": 1,
            "start": 12.0,
            "slot": 2.0,
            "hard_limit": 3.5,
            "play": 2.2,
            "speed": 1.0,
            "flags": [],
            "text": "ok",
        },
    ]
    code, summary, _ = audit_qa_rows(rows, tts_engine="openai")
    if code != 0:
        _fail(f"qua_ngan hop le khong duoc fail: {summary}")
    bad = [
        {
            "i": 0,
            "start": 0.0,
            "slot": 1.0,
            "hard_limit": 1.2,
            "play": 3.0,
            "speed": 1.0,
            "drift_pct": 150,
            "flags": ["qua_dai", "chong_doan_severe", "tier5_large_overlap"],
            "tier5_overlap_sec": 1.8,
            "tier5_crossfade_sec": 0.8,
            "text": "x",
        }
    ]
    code2, summary2, _ = audit_qa_rows(bad, tts_engine="openai")
    if code2 != 0:
        _fail(f"chong_doan_severe phai PASS (zero-fail A): {summary2}")
    if "tang5" not in summary2 and "REVIEW" not in summary2:
        _fail(f"summary phai canh bao REVIEW tang5: {summary2}")
    print("OK QA: im lang/qua_ngan pass; chong_doan_severe PASS + REVIEW")


def test_semantic_shorten_budget() -> None:
    text = "A" * 80
    b1 = semantic_shorten_budget(
        text=text, tts_dur=4.0, available_slot=2.0, chars_per_sec=12.0, attempt=1,
    )
    b2 = semantic_shorten_budget(
        text=text, tts_dur=4.0, available_slot=2.0, chars_per_sec=12.0, attempt=2,
    )
    b3 = semantic_shorten_budget(
        text=text, tts_dur=4.0, available_slot=2.0, chars_per_sec=12.0, attempt=3,
    )
    if abs(b1["deficit_sec"] - 2.0) > 1e-6:
        _fail(f"deficit phai 2.0s, got {b1}")
    # cps do duoc = 80/4 = 20 -> target lan 1 = 40, khong san 32
    if b1["target_chars"] != 40:
        _fail(f"lan 1 target_chars=40 (20 cps * 2s), got {b1['target_chars']}")
    if b2["target_chars"] >= b1["target_chars"]:
        _fail(f"lan 2 phai siet hon lan 1: {b1['target_chars']} vs {b2['target_chars']}")
    if b3["target_chars"] >= b2["target_chars"]:
        _fail(f"lan 3 phai siet hon lan 2: {b2['target_chars']} vs {b3['target_chars']}")
    tiny = semantic_shorten_budget(
        text="x" * 20, tts_dur=2.0, available_slot=1.0, chars_per_sec=12.0, attempt=1,
    )
    if tiny["target_chars"] >= 32:
        _fail(f"khong duoc san 32 ky tu: {tiny}")
    print(
        "OK semantic budget: lan1=%d lan2=%d lan3=%d deficit=%.1fs (khong san 32)"
        % (b1["target_chars"], b2["target_chars"], b3["target_chars"], b1["deficit_sec"])
    )


def test_qa_semantic_fail_still_chong_doan() -> None:
    rows = [
        {
            "i": 0,
            "start": 0.0,
            "slot": 2.0,
            "hard_limit": 2.2,
            "play": 3.5,
            "speed": 1.0,
            "flags": ["qua_dai", "chong_doan_severe", "tier5_large_overlap", "shortened_semantic_fail"],
            "tier5_overlap_sec": 1.3,
            "tier5_crossfade_sec": 0.8,
            "text": "rut gon van dai",
        }
    ]
    code, summary, _ = audit_qa_rows(rows, tts_engine="openai")
    if code != 0:
        _fail(f"tier5/chong_doan_severe phai PASS (zero-fail): {summary}")
    if "tang5" not in summary and "REVIEW" not in summary:
        _fail(f"summary thieu REVIEW tang5: {summary}")
    print("OK QA: semantic fail + tier5 PASS (REVIEW), khong fail-closed")


def test_qa_audio_truncated_is_fail() -> None:
    """Cat audio am tham (ke ca cat_khop cu) phai fail-closed, khong xuat clip."""
    rows = [
        {
            "i": 104,
            "start": 913.76,
            "slot": 5.74,
            "hard_limit": 5.74,
            "play": 1.6,
            "tts_dur_raw": 8.16,
            "tts_dur": 1.6,
            "atempo_applied": 1.15,
            "speed": 1.0,
            "flags": ["atempo", "qua_ngan", "im_lang"],
            "overflow_reason": "khong_tran",
            "text": "Cánh trái máy bay của tôi sắp vỡ.",
        }
    ]
    code, summary, _ = audit_qa_rows(rows, tts_engine="edge")
    if code == 0:
        _fail(f"audio_truncated (i=104 8.16->1.6) phai fail QA: {summary}")
    if "audio_truncated" not in summary:
        _fail(f"summary phai ghi audio_truncated: {summary}")
    flagged = [
        {
            "i": 0,
            "start": 0.0,
            "slot": 2.0,
            "hard_limit": 2.2,
            "play": 2.18,
            "speed": 1.0,
            "flags": ["cat_khop"],
            "text": "cu cat_khop",
        }
    ]
    code2, summary2, _ = audit_qa_rows(flagged, tts_engine="edge")
    if code2 == 0:
        _fail(f"cat_khop cu phai fail nhu audio_truncated: {summary2}")
    print("OK QA: audio_truncated / cat_khop fail-closed")


def test_overlap_close_plan() -> None:
    a = overlap_close_plan(tts_dur=3.0, slot=3.2, atempo_already=1.0, cap=1.15)
    if a["action"] != "keep" or not a["fits"]:
        _fail(f"da vua phai keep: {a}")
    b = overlap_close_plan(tts_dur=3.801, slot=3.600, atempo_already=1.0, cap=1.15)
    if b["action"] != "atempo" or not b["fits"] or b["tempo"] > 1.15:
        _fail(f"3.801/3.600 phai atempo <=1.15: {b}")
    c = overlap_close_plan(
        tts_dur=3.68, slot=3.60, atempo_already=1.15, cap=1.15, allow_speech_trim=False,
    )
    if c["action"] != "pad_trim" or not c["fits"] or not c["pad_trim"]:
        _fail(f"pad 80ms sau 1.15x phai pad_trim: {c}")
    d = overlap_close_plan(
        tts_dur=4.2, slot=3.6, atempo_already=1.15, cap=1.15, allow_speech_trim=False,
    )
    if d["action"] != "overflow" or d["fits"]:
        _fail(f"tran that su khong cat loi o buoc 1.15: {d}")
    e = overlap_close_plan(
        tts_dur=4.2, slot=3.6, atempo_already=1.15, cap=1.20, allow_speech_trim=True,
    )
    if e["action"] != "overflow" or e["fits"] or e.get("speech_trim"):
        _fail(f"leftover >250ms khong duoc speech_trim, phai overflow: {e}")
    g = overlap_close_plan(
        tts_dur=3.90, slot=3.6, atempo_already=1.15, cap=1.20,
        allow_speech_trim=False, allow_micro_overlap=True,
    )
    if g["action"] != "micro_overlap" or not g["fits"] or g.get("trim"):
        _fail(f"leftover <=250ms sau atempo 1.20 phai micro_overlap, got {g}")
    h = overlap_close_plan(
        tts_dur=8.0, slot=3.6, atempo_already=1.20, cap=1.20,
        allow_speech_trim=False, allow_micro_overlap=True, allow_large_overlap=True,
    )
    if h["action"] != "large_overlap" or not h["fits"] or not h.get("large_overlap"):
        _fail(f"tang5 phai large_overlap + fits: {h}")
    if float(h.get("crossfade_sec") or 0) > 0.80 + 1e-6:
        _fail(f"crossfade max 0.8s: {h}")
    f = overlap_close_plan(tts_dur=4.0, slot=3.6, atempo_already=1.0, cap=1.20)
    if f["action"] != "atempo" or f["tempo"] > 1.20 + 1e-6 or not f["fits"]:
        _fail(f"4.0/3.6 vua 1.12x: {f}")
    print("OK overlap_close_plan: atempo / pad / overflow / micro / large_overlap")


def test_borrow_time_from_next() -> None:
    from dyflow.dub_timing import borrow_time_from_next, large_overlap_crossfade

    # Next: speech 10-12, following at 15 -> room to delay = 15-2-10 = 3s (approx)
    b = borrow_time_from_next(
        need_sec=1.5,
        next_source_start=10.0,
        next_source_end=12.0,
        next_timeline_start=10.0,
        following_start=15.0,
    )
    if not b["applied"] or abs(b["borrowed_sec"] - 1.5) > 1e-3:
        _fail(f"muon 1.5s: {b}")
    if b["new_next_start"] < 10.0 - 1e-6:
        _fail(f"khong duoc som hon source_start: {b}")
    # Khong du room
    c = borrow_time_from_next(
        need_sec=5.0,
        next_source_start=10.0,
        next_source_end=12.0,
        next_timeline_start=10.0,
        following_start=12.5,
    )
    # max_tl = 12.5 - 2 - 0.04 = 10.46; room ~0.46
    if c["borrowed_sec"] > 0.5:
        _fail(f"room nho: {c}")
    if large_overlap_crossfade(4.52) != 0.8:
        _fail("crossfade 4.52 -> 0.8")
    if large_overlap_crossfade(0.3) != 0.3:
        _fail("crossfade 0.3 -> 0.3")
    print("OK borrow_time + large_overlap_crossfade")


def test_atempo_fitted_hides_actual_overlap() -> None:
    """Sau tua, file van dai hon hard: decide_fit action=atempo + would_overlap=False
    (tinh fitted), nhung plan_placement tren tts that van overlap — phai semantic."""
    start, nxt = 10.0, 12.0
    hard = nxt - start - 0.04
    tts_after = hard * 1.10
    fit = decide_fit(
        tts_dur=tts_after,
        hard_limit=hard,
        speech_slot=1.5,
        source_start=start,
        next_start=nxt,
    )
    if fit["action"] != "atempo":
        _fail(f"expected atempo, got {fit}")
    if fit["would_overlap"]:
        _fail(f"trap: fitted would_overlap phai False, got {fit}")
    actual = plan_placement(
        source_start=start, source_end=start + 1.5, next_start=nxt, tts_dur=tts_after,
    )
    if not actual["would_overlap"]:
        _fail(f"file that phai overlap: {actual}")
    print("OK atempo fitted an overlap — caller phai dung actual tts, khong fit.would_overlap")


def test_semantic_4776_fits_with_atempo() -> None:
    """Job web_400a48: tts 4.776 / slot 4.2 = 1.137x <= 1.15 — tua la vua, khong fail."""
    start = 0.0
    nxt = 4.24
    hard = nxt - start - 0.04
    tts = 4.776
    if abs(hard - 4.2) > 0.01:
        _fail(f"hard {hard}")
    needed = tts / hard
    if needed > 1.15 + 1e-6:
        _fail(f"needed {needed} phai <= 1.15")
    fit = decide_fit(
        tts_dur=tts, hard_limit=hard, speech_slot=3.0,
        source_start=start, next_start=nxt,
    )
    if fit["would_overlap"]:
        _fail("decide_fit.would_overlap False (fitted) — day la ly do code cu break lan 1")
    actual = plan_placement(
        source_start=start, source_end=start + 3.0, next_start=nxt, tts_dur=tts,
    )
    if not actual["would_overlap"]:
        _fail("file 4.776s phai de cau sau truoc khi tua")
    fitted = tts / min(needed, 1.15)
    after = plan_placement(
        source_start=start, source_end=start + 3.0, next_start=nxt, tts_dur=fitted,
    )
    if after["would_overlap"]:
        _fail(f"sau tua 1.137x con de? fitted={fitted} {after}")
    print("OK 4.776s / 4.2s: tua 1.137x la vua — semantic phai atempo that, khong break theo fitted")


def test_tiny_leftover_must_atempo() -> None:
    """web_33946d9d i=2 (3.801/3.600) va i=7 (5.424/5.320): vua 1.15x, phai tua, khong fail."""
    cases = (
        (3.801, 3.600, "i=2"),
        (5.424, 5.320, "i=7"),
    )
    for tts, hard, name in cases:
        start = 10.0
        nxt = start + hard + 0.04
        needed = tts / hard
        if needed > 1.15 + 1e-6:
            _fail(f"{name} needed {needed} > 1.15")
        fit = decide_fit(
            tts_dur=tts, hard_limit=hard, speech_slot=hard * 0.9,
            source_start=start, next_start=nxt,
        )
        if fit["action"] != "atempo":
            _fail(f"{name} phai atempo (khong keep vi duoi 1.02), got {fit}")
        actual = plan_placement(
            source_start=start, source_end=start + hard * 0.9,
            next_start=nxt, tts_dur=tts,
        )
        if not actual["would_overlap"]:
            _fail(f"{name} file that phai de cau sau: {actual}")
        after = plan_placement(
            source_start=start, source_end=start + hard * 0.9,
            next_start=nxt, tts_dur=tts / needed,
        )
        if after["would_overlap"]:
            _fail(f"{name} sau tua {needed:.3f}x van de: {after}")
    print("OK 3.801/3.600 va 5.424/5.320: tua nhe la vua, khong fail chong_doan")


def test_dub_prompts_slot_and_coherence() -> None:
    sys1, u1 = _dub_translate_prompt(
        source_lang="zh",
        target_lang="vi",
        source_text="还差两千",
        prev_context="你还差多少？",
        next_context="",
        slot_duration=2.0,
        chars_per_second=20.0,
        target_chars=40,
    )
    if "还差X" not in sys1:
        _fail("prompt dich thieu quy tac 还差X")
    if "BUOC 0" not in sys1 or "toi/ban" not in sys1:
        _fail("prompt dich thieu Buoc 0 xung ho")
    if "speaker_relation" not in sys1 or "pronoun_pair_used" not in sys1:
        _fail("prompt dich thieu field xung ho")
    if u1["target_chars"] != 40 or u1["prev_context"] != "你还差多少？":
        _fail(f"payload dich sai: {u1}")
    sys2, u2 = _dub_reslot_prompt(
        source_lang="zh",
        target_lang="vi",
        source_text="还差两千",
        current_translation="x" * 50,
        prev_context="问",
        next_context="答",
        target_chars=40,
    )
    if u2["deficit_chars"] != 10:
        _fail(f"deficit_chars {u2['deficit_chars']}")
    if "KHONG nhay coc" not in sys2:
        _fail("prompt reslot thieu thu tu uu tien")
    if "locked_pronoun" not in sys2:
        _fail("reslot phai khoa cap xung ho")
    sys_en, u_en = _dub_translate_prompt(
        source_lang="zh",
        target_lang="en",
        source_text="还差两千",
        prev_context="",
        next_context="",
        slot_duration=2.0,
        chars_per_second=20.0,
        target_chars=40,
    )
    if "English" not in sys_en or u_en.get("target_lang_code") != "en":
        _fail(f"prompt en thieu target English: {u_en}")
    if "BAT BUOC bang English" not in sys_en:
        _fail("prompt en phai khoa output English")
    sys_rs, u_rs = _dub_reslot_prompt(
        source_lang="zh",
        target_lang="en",
        source_text="x",
        current_translation="Not knowing how to write a script can also create a story.",
        prev_context="",
        next_context="",
        target_chars=40,
    )
    if u_rs.get("target_lang_code") != "en" or "English" not in sys_rs:
        _fail(f"reslot en thieu target: {u_rs}")
    parsed = _parse_line_rows(
        '{"cast":[{"speaker_id":"A","speaker_relation":"me noi voi con",'
        '"pronoun_pair_used":"me/con"}],'
        '"lines":[{"i":1,"text":"Con an di","speaker_relation":"me noi voi con",'
        '"pronoun_pair_used":"me/con"}]}',
        1,
    )
    if not parsed:
        _fail("parse line rows xung ho that bai")
    rows, cast = parsed
    if rows[0]["pronoun_pair_used"] != "me/con" or cast[0]["speaker_relation"] != "me noi voi con":
        _fail(f"parse xung ho sai: {parsed}")
    print("OK prompt dub: dich vua khung + reslot giu mach + xung ho")


def test_classify_f0_profile() -> None:
    from dyflow.voices import (
        PROFILE_CHILD,
        PROFILE_FEMALE_ADULT,
        PROFILE_MALE_ADULT,
        classify_f0_profile,
        resolve_voice_profile,
    )

    cases = (
        (None, None),
        (0, None),
        (84, PROFILE_MALE_ADULT),
        (85, PROFILE_MALE_ADULT),
        (120, PROFILE_MALE_ADULT),
        (155, PROFILE_MALE_ADULT),
        (160, PROFILE_MALE_ADULT),
        (161, PROFILE_FEMALE_ADULT),
        (165, PROFILE_FEMALE_ADULT),
        (200, PROFILE_FEMALE_ADULT),
        (249, PROFILE_FEMALE_ADULT),
        (250, PROFILE_CHILD),
        (255, PROFILE_CHILD),
        (312, PROFILE_CHILD),
        (400, PROFILE_CHILD),
        (410, PROFILE_CHILD),
    )
    for hz, expect in cases:
        got = classify_f0_profile(hz)
        if got != expect:
            _fail(f"classify_f0_profile({hz}) = {got!r}, expect {expect!r}")
    child = resolve_voice_profile("vi", PROFILE_CHILD)
    if child["voice_id"] != "male_child":
        _fail(f"vi child voice_id {child['voice_id']}")
    if child["tts_voice_name"] != "vi-VN-NamMinhNeural":
        _fail(f"vi child tts {child['tts_voice_name']}")
    if child["tts_voice"] != child["tts_voice_name"]:
        _fail("tts_voice phai trung tts_voice_name")
    female = resolve_voice_profile("vi", PROFILE_FEMALE_ADULT)
    if female["tts_voice_name"] != "vi-VN-HoaiMyNeural":
        _fail(f"vi female tts {female['tts_voice_name']}")
    print("OK classify F0 + VOICE_PROFILE_MAP vi")


def test_voice_profile_lock_same_speaker() -> None:
    """Moi speaker_id dung dung 1 giong TTS, 2 doan boy khong lech giong."""
    import tempfile

    from dyflow.speakers import apply_speaker_voice_profiles, detect_speaker_voice_profile
    from dyflow.voices import resolve_voice_profile

    child = resolve_voice_profile("vi", "child")
    segs = [
        {
            "speaker_id": "boy", "start": 0.0, "end": 0.4, "text": "a",
            "source_text": "啊", "tts_voice": "wrong-A", "voice_id": "male_child",
        },
        {
            "speaker_id": "boy", "start": 1.0, "end": 3.2, "text": "b",
            "source_text": "啊", "tts_voice": "wrong-B", "voice_id": "male_child",
            "f0_hz": 305.0,
        },
        {
            "speaker_id": "dad", "start": 4.0, "end": 6.5, "text": "c",
            "source_text": "你", "tts_voice": "wrong-C", "f0_hz": 118.0,
        },
    ]
    stamped = apply_speaker_voice_profiles(segs, {
        "boy": {**child, "median_f0_hz": 305.0},
    })
    if stamped[0]["tts_voice_name"] != stamped[1]["tts_voice_name"]:
        _fail("2 doan boy lech tts_voice_name")
    if stamped[0]["tts_voice_name"] != child["tts_voice_name"]:
        _fail(f"boy tts sai: {stamped[0]['tts_voice_name']}")
    if stamped[0]["voice_profile"] != "child" or stamped[1]["voice_profile"] != "child":
        _fail("boy chua khoa voice_profile=child")

    with tempfile.TemporaryDirectory() as tmp:
        out = detect_speaker_voice_profile(
            segs, None, tmp, target_lang="vi",
        )
    if out[0]["voice_profile"] != "child" or out[1]["voice_profile"] != "child":
        _fail(f"detect boy profile {out[0].get('voice_profile')} / {out[1].get('voice_profile')}")
    if out[0]["tts_voice"] != out[1]["tts_voice"]:
        _fail("detect: 2 doan boy khac tts_voice")
    if out[2]["voice_profile"] != "male_adult":
        _fail(f"detect dad profile {out[2].get('voice_profile')} (f0=118)")
    if out[0]["voice_id"] == out[2]["voice_id"]:
        _fail("boy (child) va dad (male_adult) khong duoc cung voice_id")
    print("OK khoa 1 giong TTS / speaker_id (voice profile)")


def test_shorten_rejects_wrong_lang() -> None:
    from dyflow.translate_llm import (
        _finalize_shortened,
        dub_target_lang,
        lang_mismatch_reason,
    )

    vi = "Không biết viết kịch bản cũng có thể tạo ra câu chuyện."
    en = "You can still tell a story without a script."
    if lang_mismatch_reason(vi, "en") is None:
        _fail("phai bat Vietnamese khi target=en")
    if lang_mismatch_reason(en, "en") is not None:
        _fail(f"English hop le bi reject: {lang_mismatch_reason(en, 'en')}")
    if dub_target_lang({"auto": {"translate_to": "en"}}) != "en":
        _fail("dub_target_lang phai doc auto.translate_to")
    if dub_target_lang({"lang": "en-US"}) != "en":
        _fail("en-US -> en")
    if dub_target_lang({}) != "vi":
        _fail("thieu key van default vi")
    if _finalize_shortened(vi, en, "en", label="t") is not None:
        _fail("shorten en khong duoc chap nhan tieng Viet")
    longer = en + " extra words here"
    if _finalize_shortened(longer, en, "en", label="t") is not None:
        _fail("khong ngan hon phai reject, khong gia JSON None")
    short_en = "Still tell a story."
    if _finalize_shortened(short_en, en, "en", label="t") != short_en:
        _fail("ban en ngan hon phai chap nhan")
    print("OK shorten reject sai ngon ngu + khong ngan hon")


def test_llm_age_wins_over_child_f0() -> None:
    """Narrator adult + F0 tre em: giu adult. Boy child + F0 tre: giu child."""
    import tempfile

    from dyflow.speakers import detect_speaker_voice_profile, resolve_profile_prefer_llm
    from dyflow.voices import PROFILE_CHILD, PROFILE_MALE_ADULT

    fields, why = resolve_profile_prefer_llm(
        {"voice_id": "male_adult", "speaker_id": "narrator"},
        PROFILE_CHILD,
        lang="en",
    )
    if fields.get("age") != "adult" or fields.get("voice_id") != "male_adult":
        _fail(f"narrator phai giu adult, got {fields} | {why}")
    if "khoa LLM" not in why:
        _fail(f"log phai noi khoa LLM: {why}")
    aunt, why_a = resolve_profile_prefer_llm(
        {"voice_id": "female_adult", "gender": "female", "age": "adult"},
        PROFILE_MALE_ADULT,
        lang="en",
    )
    if aunt.get("voice_id") != "female_adult":
        _fail(f"aunt khong duoc F0 doi thanh nam: {aunt} | {why_a}")
    boy, _ = resolve_profile_prefer_llm(
        {"voice_id": "male_child", "speaker_id": "boy"},
        PROFILE_CHILD,
        lang="en",
    )
    if boy.get("voice_id") != "male_child":
        _fail(f"boy phai giu child, got {boy}")

    segs = [
        {
            "speaker_id": "narrator", "start": 0.0, "end": 2.5, "text": "a",
            "voice_id": "male_adult", "gender": "male", "age": "adult",
            "f0_hz": 312.0,
        },
        {
            "speaker_id": "boy", "start": 3.0, "end": 5.5, "text": "b",
            "voice_id": "male_child", "gender": "male", "age": "child",
            "f0_hz": 305.0,
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = detect_speaker_voice_profile(segs, None, tmp, target_lang="en")
    if out[0]["voice_id"] != "male_adult":
        _fail(f"detect narrator bi F0 ghi de: {out[0].get('voice_id')}")
    if out[1]["voice_id"] != "male_child":
        _fail(f"detect boy mat child: {out[1].get('voice_id')}")
    if out[0]["voice_id"] == out[1]["voice_id"]:
        _fail("narrator va boy khong duoc trung giong")
    print("OK LLM age thang F0 child (narrator adult / boy child)")


def main() -> int:
    test_absolute_timeline()
    test_pause_is_not_speech()
    test_no_hard_trim_decision()
    test_group_fragments()
    test_budget_not_pause()
    test_overflow_beyond_atempo()
    test_overflow_merge_edges()
    test_shorten_16_loses_meaning()
    test_loudnorm_skip_inf()
    test_qa_pause_is_pass()
    test_semantic_shorten_budget()
    test_qa_semantic_fail_still_chong_doan()
    test_qa_audio_truncated_is_fail()
    test_overlap_close_plan()
    test_borrow_time_from_next()
    test_atempo_fitted_hides_actual_overlap()
    test_semantic_4776_fits_with_atempo()
    test_tiny_leftover_must_atempo()
    test_dub_prompts_slot_and_coherence()
    test_classify_f0_profile()
    test_voice_profile_lock_same_speaker()
    test_shorten_rejects_wrong_lang()
    test_llm_age_wins_over_child_f0()
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from test_dub_timing_cases import main as cases_main  # noqa: E402

    cases_main()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
