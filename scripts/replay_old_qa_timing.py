#!/usr/bin/env python3
"""Replay log TTS cu cua job 7670527051429907739 qua fitter MOI.

Day KHONG phai full Whisper/dich/TTS lai (may nay khong co video + key).
Day la: giu nguyen duration TTS cu, ap dung luat timing moi
(khong cat, tua toi da 1.15x, overflow neu van de).

So lieu goc: scripts/test_openai_ratefit.py BEFORE
(raw TTS, slot = max_avail = hard_limit toi cau sau).

  python scripts/replay_old_qa_timing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_audit import audit_qa_rows  # noqa: E402
from dyflow.dub_timing import decide_fit  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
from test_openai_ratefit import BEFORE  # noqa: E402

# (before_idx, nhan user hay dung)
FOCUS = (
    (17, "user#16 / slot 13.9s"),
    (21, "user#51 / raw 2.6s fit 1.1s"),
    (33, "user#52 / raw ~4.1s fit 1.5s"),
    (42, "user#38 / slot 10.7s"),
    (43, "user#39 / slot 16.2s"),
    (63, "user#63 / raw 4.1s fit 2.2s"),
)


def _replay_one(raw: float, hard: float) -> dict:
    if raw <= hard:
        speech = raw
    else:
        speech = hard
    nxt = hard + 0.04
    d = decide_fit(
        tts_dur=raw,
        hard_limit=hard,
        speech_slot=speech,
        source_start=0.0,
        next_start=nxt,
    )
    flags: list[str] = []
    if d["action"] == "keep_short":
        flags.append("qua_ngan")
    if d["action"] == "atempo":
        flags.append("atempo")
    if d["action"] == "overflow":
        flags.append("chong_doan")
        flags.append("overrun_fallback")
        flags.append("qua_dai")
    # cat_khop: khong bao gio
    return {**d, "raw": raw, "flags": flags or ["ok"]}


def main() -> int:
    rows = []
    n_over = n_atempo = n_ok = n_short = 0
    print("idx  raw  hard   can   action     overlap  flags")
    for i, raw, hard in BEFORE:
        r = _replay_one(raw, hard)
        if r["action"] == "overflow":
            n_over += 1
        elif r["action"] == "atempo":
            n_atempo += 1
        elif r["action"] == "keep_short":
            n_short += 1
        else:
            n_ok += 1
        mark = " <--" if i in {k for k, _ in FOCUS} else ""
        print(
            f"{i:3d} {raw:5.1f} {hard:5.1f}  {r['required_tempo']:4.2f}x  "
            f"{r['action']:11s} {r['overlap_sec']:6.2f}s  "
            f"{','.join(r['flags'])}{mark}"
        )
        rows.append({
            "i": i,
            "start": 0.0,
            "slot": r["speech_slot"],
            "hard_limit": hard,
            "play": r["fitted_dur"],
            "speed": r["tempo"],
            "flags": r["flags"],
            "text": "",
        })

    n = len(BEFORE)
    old_cat = sum(1 for _, raw, hard in BEFORE if raw > hard * 1.02)
    old_ngan = sum(1 for _, raw, hard in BEFORE if raw < hard * 0.85)
    print()
    print("=== So sanh toan clip (80 doan log cu) ===")
    print(f"Cu : cat_khop (raw>slot, se bi cat) ~ {old_cat}/{n}")
    print(f"Cu : qua_ngan (raw<0.85*slot pause) ~ {old_ngan}/{n}")
    print(f"Moi: cat_khop = 0/{n}  (khong con atrim)")
    print(f"Moi: qua_ngan (keep_short vs speech that) = {n_short}/{n}")
    print(f"Moi: atempo <=1.15x = {n_atempo}/{n}")
    print(f"Moi: overflow >1.15x (can gop/rut gon/flag) = {n_over}/{n}")
    print(f"Moi: keep dung = {n_ok}/{n}")
    print()
    print("=== Cac doan user chi dinh ===")
    for idx, label in FOCUS:
        raw, hard = BEFORE[idx][1], BEFORE[idx][2]
        r = _replay_one(raw, hard)
        print(
            f"{label}: raw={raw}s hard={hard}s can={r['required_tempo']:.2f}x "
            f"-> {r['action']} tua={r['tempo']:.2f} overlap={r['overlap_sec']:.2f}s"
        )
        if r["action"] == "overflow":
            print(
                "  fallback: gop cau sau cung nhan vat neu co; "
                "khong du thi rut gon loi; van khong vua thi GIU audio, "
                "gan chong_doan, QA FAIL. Khong cat. Khong doi start cau sau."
            )
        elif r["action"] == "keep":
            print("  noi xong som, khoang lang/nhac giu nguyen.")
        elif r["action"] == "atempo":
            print("  tua nhe trong 1.15x, khong de cau sau.")

    code, summary, _ = audit_qa_rows(rows, tts_engine="openai")
    print()
    print(f"QA replay (neu khong gop/rut gon overflow): exit={code} {summary}")
    print(
        "Luu y: full pipeline that (Whisper+gop cau+dich lai) se giam overflow "
        "vi nhieu shot 1.1s se duoc gop truoc TTS. Day chi replay duration cu."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
