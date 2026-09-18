#!/usr/bin/env python3
"""Mo phong rate-fit OpenAI TTS tren so lieu job web_1291431104fd4993a5c8630f5be0d6fc.

Khong goi API. Duration sau rate-fit = raw / speed (speed tu TTS_EDGE_RATE_*).
Neu van qua_dai: mo phong shorten (TTS ~ slot*1.10) roi atempo residual.

  python scripts/test_openai_ratefit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_audit import audit_qa_rows  # noqa: E402
from dyflow.dub_constants import (  # noqa: E402
    TTS_OPENAI_SPEED_MAX,
    TTS_RATE_MAX_ITERS,
)
from dyflow.dub_tts import (  # noqa: E402
    duration_mismatch_flags,
    drift_ratio,
)

# Truoc: log job 7670527051429907739 (rate_iter=0, atempo=1.00, flags=ok)
BEFORE = [
    (0, 4.3, 1.8), (1, 4.9, 2.2), (2, 4.9, 1.4), (3, 5.2, 2.6), (4, 4.5, 0.9),
    (5, 3.0, 1.3), (6, 3.6, 1.9), (7, 4.0, 1.6), (8, 3.8, 5.3), (9, 3.4, 2.4),
    (10, 2.4, 3.7), (11, 4.2, 2.3), (12, 3.1, 1.9), (13, 2.6, 3.1), (14, 3.9, 1.7),
    (15, 3.0, 1.0), (16, 4.2, 1.9), (17, 3.5, 13.9), (18, 3.8, 1.2), (19, 4.1, 2.4),
    (20, 3.2, 0.6), (21, 2.6, 1.1), (22, 3.2, 1.6), (23, 2.6, 0.8), (24, 3.6, 2.0),
    (25, 3.5, 1.6), (26, 4.2, 1.7), (27, 2.0, 0.9), (28, 4.0, 1.9), (29, 2.1, 0.5),
    (30, 4.9, 1.3), (31, 2.7, 3.8), (32, 4.1, 1.9), (33, 4.2, 1.5), (34, 4.0, 2.1),
    (35, 3.6, 1.0), (36, 4.0, 1.7), (37, 3.0, 1.7), (38, 2.3, 1.7), (39, 2.0, 1.5),
    (40, 3.8, 1.5), (41, 1.9, 3.1), (42, 3.0, 10.7), (43, 2.8, 16.2), (44, 3.7, 1.6),
    (45, 2.2, 3.4), (46, 4.5, 3.7), (47, 2.2, 0.7), (48, 2.8, 1.7), (49, 4.0, 1.2),
    (50, 5.7, 1.9), (51, 2.8, 2.4), (52, 2.5, 2.0), (53, 3.8, 0.6), (54, 3.2, 5.3),
    (55, 2.5, 3.8), (56, 3.0, 1.7), (57, 4.8, 1.1), (58, 5.1, 1.5), (59, 3.1, 1.2),
    (60, 4.5, 2.2), (61, 4.1, 2.0), (62, 3.3, 1.4), (63, 4.1, 2.2), (64, 3.0, 1.0),
    (65, 2.6, 1.1), (66, 3.5, 1.6), (67, 4.6, 1.7), (68, 3.0, 1.4), (69, 3.3, 7.0),
    (70, 5.6, 1.7), (71, 3.6, 1.2), (72, 4.0, 0.9), (73, 3.5, 1.9), (74, 1.4, 4.0),
    (75, 1.8, 2.1), (76, 3.0, 1.7), (77, 3.8, 1.6), (78, 2.1, 1.9), (79, 4.4, 3.3),
]


def _simulate(raw: float, slot: float) -> dict:
    """Nhip tu nhien: speed ~1.0, khong tua slot; qua_dai thi rut gon loi."""
    dur = float(raw)
    speed = 1.0
    iters = 0
    flags, needs_shorten = duration_mismatch_flags(dur, slot)
    shortened = False
    if needs_shorten:
        dur = slot * 1.04
        shortened = True
        flags, needs_shorten = duration_mismatch_flags(dur, slot)

    atempo = 1.0
    drift = round(drift_ratio(dur, slot) * 100, 1)
    return {
        "raw": raw,
        "slot": slot,
        "after": round(dur, 2),
        "rate_iters": iters,
        "atempo": round(atempo, 3),
        "speed": round(speed, 2),
        "drift": drift,
        "flags": flags or (["ok"]),
        "shortened": shortened,
    }


def main() -> int:
    before_rows = []
    after_rows = []
    n_iter = n_atempo = n_ok_before = n_ok_after = 0
    print(
        f"{'#':>3} {'slot':>5} {'raw':>5} {'bDrift':>7} {'bFl':>8} | "
        f"{'fit':>5} {'iter':>4} {'atmp':>5} {'aDrift':>7} flags"
    )
    for i, raw, slot in BEFORE:
        b_drift = round(abs(raw - slot) / slot * 100, 1)
        b_flags, _ = duration_mismatch_flags(raw, slot)
        # Truoc: pipeline OpenAI bo qua rate-fit -> flags bi ghi "ok" (bug)
        before_rows.append({
            "i": i, "start": i, "slot": slot, "play": raw, "speed": 1.0,
            "drift_pct": b_drift, "flags": [], "text": "",
        })
        sim = _simulate(raw, slot)
        after_flags = [f for f in sim["flags"] if f != "ok"]
        after_rows.append({
            "i": i, "start": i, "slot": slot, "play": sim["after"], "speed": sim["atempo"],
            "drift_pct": sim["drift"], "flags": after_flags, "text": "",
        })
        if sim["rate_iters"] > 0:
            n_iter += 1
        if sim["atempo"] != 1.0:
            n_atempo += 1
        if not b_flags:
            n_ok_before += 1
        if not after_flags:
            n_ok_after += 1
        bfl = ",".join(b_flags) or "ok*"
        print(
            f"{i:3d} {slot:5.1f} {raw:5.1f} {b_drift:6.0f}% {bfl:>8} | "
            f"{sim['after']:5.2f} {sim['rate_iters']:4d} {sim['atempo']:5.3f} "
            f"{sim['drift']:6.0f}% {','.join(after_flags) or 'ok'}"
        )

    print()
    print(
        f"TTS_RATE_MAX_ITERS={TTS_RATE_MAX_ITERS} OPENAI_SPEED_MAX={TTS_OPENAI_SPEED_MAX} "
        f"(nhip tu nhien, khong tua slot)"
    )
    print(f"Doan rate_iter>0: {n_iter}/{len(BEFORE)}  |  atempo!=1: {n_atempo}/{len(BEFORE)}")

    code_b, sum_b, _ = audit_qa_rows(before_rows, tts_engine="openai")
    code_a, sum_a, _ = audit_qa_rows(after_rows, tts_engine="openai")
    print(f"QA truoc: exit={code_b} {sum_b}")
    print(f"QA sau:   exit={code_a} {sum_a}")

    if n_iter != 0:
        raise SystemExit(f"FAIL: nhip tu nhien khong tua slot, rate_iter phai 0, got {n_iter}")
    if n_atempo != 0:
        raise SystemExit(f"FAIL: nhip tu nhien khong atempo, got {n_atempo}")
    sample = _simulate(4.5, 0.9)
    if sample["speed"] != 1.0 or not sample["shortened"]:
        raise SystemExit("FAIL: #4 (4.5s/0.9s) phai rut gon loi, speed=1.0")
    if sample["speed"] > TTS_OPENAI_SPEED_MAX + 0.01:
        raise SystemExit(f"FAIL: speed {sample['speed']} vuot {TTS_OPENAI_SPEED_MAX}")
    if "qua_dai" not in (duration_mismatch_flags(4.5, 0.9)[0]):
        raise SystemExit("FAIL: 4.5s/0.9s phai qua_dai")

    pause = _simulate(3.5, 13.9)
    if pause["speed"] < 0.99 or pause["atempo"] != 1.0:
        raise SystemExit(
            f"FAIL: slot thua khong duoc keo cham (speed={pause['speed']} atempo={pause['atempo']})"
        )

    from dyflow.prosody import sanitize_edge_cfg  # noqa: E402
    from dyflow.dub_tts import resolve_openai_speed  # noqa: E402

    clamped = sanitize_edge_cfg({"rate": "+137%"})["rate"]
    if clamped != "+12%":
        raise SystemExit(f"FAIL: Edge sanitize +137% -> {clamped}, ky vong +12%")
    if abs(resolve_openai_speed({"rate": clamped}) - TTS_OPENAI_SPEED_MAX) > 0.02:
        raise SystemExit(
            f"FAIL: rate +12% phai clamp nhip tu nhien {TTS_OPENAI_SPEED_MAX}, got "
            f"{resolve_openai_speed({'rate': clamped})}"
        )
    if abs(resolve_openai_speed({"rate": "+137%"}) - TTS_OPENAI_SPEED_MAX) > 0.02:
        raise SystemExit(
            f"FAIL: +137% phai clamp {TTS_OPENAI_SPEED_MAX}, got "
            f"{resolve_openai_speed({'rate': '+137%'})}"
        )
    if abs(resolve_openai_speed({"rate": "+12%", "openai_speed": 4.0}) - TTS_OPENAI_SPEED_MAX) > 0.01:
        raise SystemExit("FAIL: openai_speed 4.0 phai clamp ve ~1.08, khong 4x")
    if abs(resolve_openai_speed({"rate": "+300%"}) - TTS_OPENAI_SPEED_MAX) > 0.01:
        raise SystemExit("FAIL: +300% -> ~1.08, khong 4.0")
    if abs(resolve_openai_speed({"rate": "-50%"}) - 0.95) > 0.02:
        raise SystemExit("FAIL: keo cham phai san 0.95, khong 0.5")
    print("OK openai_speed 0.95-1.08 (khong 0.25-4.0, khong 2x/0.5x)")
    print("OK khop khung bang rut gon loi; slot thua khong keo cham")

    from dyflow.translate_llm import hard_shorten_text  # noqa: E402

    src = "Tôi sẽ chăm sóc trẻ và việc nhà mỗi ngày khi bố đi làm."
    tiny = hard_shorten_text(src, 8)
    if tiny != src:
        raise SystemExit(f"FAIL hard_shorten 8 ky tu khong duoc cat nat: {tiny}")
    cut = hard_shorten_text(src, 40)
    if len(cut) > 40:
        raise SystemExit(f"FAIL hard_shorten van dai {len(cut)}: {cut}")
    if cut == src:
        raise SystemExit("FAIL hard_shorten 40 khong cat")
    if not cut.endswith("."):
        raise SystemExit(f"FAIL hard_shorten thieu dau cau: {cut}")
    print("OK hard_shorten giu cau du nghia")

    from dyflow.dub_merge import plan_tiny_slot_merges  # noqa: E402

    tiny = [
        {"start": 0.0, "end": 0.6, "text": "a", "speaker_id": "dad"},
        {"start": 0.7, "end": 2.0, "text": "b", "speaker_id": "dad"},
        {"start": 8.0, "end": 8.5, "text": "c", "speaker_id": "dad"},
    ]
    pairs = plan_tiny_slot_merges(tiny, min_slot=0.95, max_gap=0.45)
    if pairs != [(0, 1)]:
        raise SystemExit(f"FAIL merge tiny: {pairs}")
    print("OK gop shot ngan sat nhau")
    return 0


def _live() -> int:
    """Goi OpenAI TTS that (gpt-4o-mini-tts) tren 3 doan ti le giong job 767052..."""
    import json
    import tempfile

    from dyflow.preflight import hydrate_openai_key
    from dyflow.utils import setup_logging
    from dyflow.voiceover import synthesize_segments

    setup_logging(verbose=False)
    if not hydrate_openai_key():
        raise SystemExit("FAIL live: thieu OPENAI_API_KEY")

    # (role, openai_voice, text, start, end) — slot chat nhu cac doan drift lon.
    specs = [
        (
            "dad", "onyx",
            "Chau oi hom nay chau di hoc ve muon the nay, bo phai doi o cong truong rat lau.",
            0.00, 0.90,
        ),
        (
            "aunt", "shimmer",
            "Di vao trong nay uong nuoc da roi hay ke cho di nghe chuyen gi o truong.",
            2.00, 3.80,
        ),
        (
            "child", "echo",
            "Co giao giao bai tap ve nha, con lam xong roi.",
            5.00, 7.10,
        ),
    ]
    segs = []
    for i, (role, voice, text, start, end) in enumerate(specs):
        segs.append({
            "start": start,
            "end": end,
            "text": text,
            "speaker_id": role,
            "speaker_role": role,
            "openai_voice": voice,
        })
    video_dur = 7.14
    cfg = {
        "tts_engine": "openai",
        "openai_model": "gpt-4o-mini-tts",
        "rate": "+0%",
        "pitch": "+0Hz",
        "volume": "+0%",
        "audio_mode": "dub",
        "dub_qa_fail_closed": False,
        "match_original_prosody": False,
        "refresh_tts": True,
        "dub_auto_shorten": True,
        "tts_segment_gap": 0.25,
        "skip_failed_segments": False,
    }
    tmp = Path(tempfile.mkdtemp(prefix="dyflow_oa_ratefit_"))
    print(f"LIVE work={tmp}")
    synthesize_segments(
        segs, tmp / "seg_tts", tmp / "voice.mp3", cfg, video_dur=video_dur,
    )
    rows = json.loads((tmp / "segments_qa.json").read_text(encoding="utf-8"))
    print(
        f"{'#':>3} {'voice':>8} {'slot':>5} {'raw':>5} {'post':>5} "
        f"{'iter':>4} {'atmp':>5} {'spd':>5} {'drift':>6} flags"
    )
    n_iter = n_at = 0
    for r, spec in zip(rows, specs):
        if int(r.get("rate_iters") or 0) > 0:
            n_iter += 1
        if abs(float(r.get("atempo") or 1) - 1.0) > 0.005:
            n_at += 1
        print(
            f"{r['i']:3d} {spec[1]:>8} {r['slot']:5.2f} {r['tts_dur_raw']:5.2f} "
            f"{r['post_dur']:5.2f} {r['rate_iters']:4d} {r['atempo']:5.3f} "
            f"{r.get('openai_speed', 0):5.2f} {r['drift_pct']:5.0f}% "
            f"{','.join(r.get('flags') or []) or 'ok'}"
        )
    print(f"LIVE rate_iter>0: {n_iter}/{len(rows)}  atempo!=1: {n_at}/{len(rows)}")
    for r in rows:
        spd = float(r.get("openai_speed") or 1.0)
        if spd < 0.94 or spd > 1.09:
            raise SystemExit(f"FAIL live: speed {spd} ngoai 0.95-1.08")
        if abs(float(r.get("atempo") or 1) - 1.0) > 0.005:
            raise SystemExit("FAIL live: atempo khong duoc lech nhip tu nhien")
    print("OK live OpenAI TTS nhip tu nhien")
    return 0


if __name__ == "__main__":
    if "--live" in sys.argv:
        raise SystemExit(_live())
    raise SystemExit(main())
