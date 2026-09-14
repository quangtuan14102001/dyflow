#!/usr/bin/env python3
"""Regression cases — 4 nhom QA co dinh. Khong bia so: moi case ghi source.

Nhom (gan trong comment + field group):
  A TIMING — TTS de cau sau sau tua+rut (chong_doan)
  B TIMING — TTS ngan hon slot (pause left in slot)
  C TRANSLATION — sai nghia phu dinh/so luong/thoi gian ZH (还差/才/没/怎么又)
  D TRANSLATION — xung ho sai / khong nhat quan

Quy trinh khi job fail moi (bat buoc):
  1. Xep A/B/C/D (hoac no khong khop)
  2. Them bien the vao case cung nhom, hoac case moi neu pattern moi
  3. Viet nguyen nhan goc: ham Y khong cover Z (khong chi 'cau X sai')
  4. Liet ke 2-3 tinh huong gia dinh cung bug
  5. Sua pattern, khong chi so lieu 1 job
  6. Chay TOAN BO file nay

  python scripts/test_dub_timing_cases.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_audit import audit_qa_rows  # noqa: E402
from dyflow.dub_merge import overflow_merge_plan  # noqa: E402
from dyflow.dub_timing import (  # noqa: E402
    decide_fit,
    is_audio_truncated,
    overflow_atempo_plan,
    overflow_semantic_try_budget,
    overlap_close_plan,
    plan_placement,
    semantic_shorten_budget,
    shorten_attempt_closes_enough,
)
from dyflow.translate_llm import (  # noqa: E402
    _dub_reslot_prompt,
    _dub_translate_prompt,
    _parse_line_rows,
    _pronoun_rules,
    apply_cast_lock,
    locked_cast_payload,
)

# --- nhom A/B/C/D: danh sach case (so lieu that) ---

# Nhom C + A: web_400a48a8 i=6 — slot 4.200, tts sau semantic 4.776 (1.137x).
# ZH "还差两千" la fixture prompt user viet; "前头还房贷" lay tu log dich job 767052
# (23:35:30). User dinh danh case "房贷 还差两千" slot 4.2s.
CASE_C1_HAI_CHA = {
    "id": "C1_hai_cha_slot_4_2",
    "group": "C",
    "zh": "还差两千",
    "prev_zh": "你还差多少？",
    "slot": 4.2,
    "target_lang": "vi",
    "expect": "pass",
    "cov_min": 98.0,
    "cov_max": 102.0,
    "tts_after_semantic": 4.776,
    "source": "web_400a48a8 i=6 (tts 4.776/slot 4.200); ZH prompt 还差两千",
}

# Nhom C: hoi-dap can nang/di ung — ZH that tu log dich 23:34:22.
# Slot dai do duoc gan nhat: job 767052 #42 hard=10.7s (BEFORE idx 42).
# User nho 10.8s; dung 10.7s do duoc, khong bia 10.8.
CASE_C2_QA_CAN = {
    "id": "C2_qa_can_nang_slot_10_7",
    "group": "C",
    "zh": "孩子多重 有没有过敏",
    "prev_zh": "九点十五才喂过退烧药",
    "next_zh": "晚上九点15吃的",
    "slot": 10.7,
    "target_lang": "vi",
    "expect": "pass",
    "cov_min": None,
    "cov_max": None,
    "source": "log dich 767052: 孩子多重 有没有过敏; slot 10.7s = BEFORE[42]",
}

# Nhom C: 才 / 没 / 怎么又 — ZH that + trap trong prompt user.
CASE_C3_CAI_MEI = {
    "id": "C3_cai_mei_zenme_you",
    "group": "C",
    "zh_list": (
        "九点十五才喂过退烧药",  # log 23:34:07
        "你最近是不是没认真听课",  # web_33946d9d i=7 ZH
        "怎么又",  # trap prompt user 2026-09-05, chua co dong job day du
    ),
    "slot": 4.2,
    "target_lang": "vi",
    "expect": "pass",
    "source": "才: log 767052; 没: 33946d9d i=7; 怎么又: prompt user",
}

# Nhom D: me-con / cha-con — VI that tu dump 33946d9d + screenshot job 19 doan.
CASE_D1_ME_CON = {
    "id": "D1_me_con_nhat_quan",
    "group": "D",
    "lines": (
        {"zh": "妈", "vi_bad": "Bạn luôn đến cuối cùng. Mẹ xin lỗi!", "i": 5},
        {"zh": "对不起 妈妈来晚了", "vi_ok_hint": "mẹ", "i": 4},
    ),
    "target_lang": "vi",
    "expect": "pass",
    "source": "qa_dump web_33946d9d i=4/5 (toi/ban lan voi me)",
}

# Nhom B: TTS ngan, pause left — so lieu BEFORE job 767052.
CASE_B1_PAUSE_10_7 = {
    "id": "B1_pause_slot_10_7",
    "group": "B",
    "zh": "我还是你老婆呢",  # QA #42 log 22:00:12 VI 'Mình vẫn là vợ của bạn mà'
    "slot": 10.7,
    "tts_dur": 3.0,
    "target_lang": "vi",
    "expect": "pass",
    "cov_min": 20.0,
    "cov_max": 40.0,
    "source": "test_openai_ratefit BEFORE[42]=(3.0, 10.7); QA doan 42",
}

CASE_B2_PAUSE_16_2 = {
    "id": "B2_pause_slot_16_2",
    "group": "B",
    "zh": "",
    "slot": 16.2,
    "tts_dur": 2.8,
    "target_lang": "vi",
    "expect": "pass",
    "cov_min": 10.0,
    "cov_max": 25.0,
    "source": "BEFORE[43]=(2.8, 16.2) job 767052 #43",
}

# Nhom A: du 1-14% sau semantic — dump 33946d9d + 400a48 (near-miss, tua <=1.15x du).
CASE_A1_LEFTOVER_I2 = {
    "id": "A1_leftover_33946_i2",
    "group": "A",
    "zh": "不要养 不擦脸等会就变成小花猫了",
    "slot": 3.600,
    "tts_dur": 3.801,
    "target_lang": "vi",
    "expect": "pass",
    "cov_min": 98.0,
    "cov_max": 102.0,
    "source": "qa_dump web_33946d9d i=2: 3.801/3.600 cov 105.6% sau 2 lan rut",
}

CASE_A2_LEFTOVER_I7 = {
    "id": "A2_leftover_33946_i7",
    "group": "A",
    "zh": "忘了 你最近是不是没认真听课 什么都不懂 别人都有新书包 新手机",
    "slot": 5.320,
    "tts_dur": 5.424,
    "target_lang": "vi",
    "expect": "pass",
    "cov_min": 98.0,
    "cov_max": 102.0,
    "source": "qa_dump web_33946d9d i=7: 5.424/5.320 cov 101.9%",
}

CASE_A1C_NEARMISS_400A48 = {
    "id": "A1c_400a48_i6_4776",
    "group": "A",
    "zh": "还差两千",
    "slot": 4.200,
    "tts_dur": 4.776,
    "target_lang": "vi",
    "expect": "pass",
    "cov_min": 98.0,
    "cov_max": 102.0,
    "source": "web_400a48a8 i=6 tts 4.776 / slot 4.200 = 1.137x (near-miss, khong phai severe)",
}

# Nhom A severe: web_49e073e3 — cov 120-200%, atempo 1.15x khong bao gio du.
# i=3 lan 2 tts=5.451 cov 191.9% => slot = 5.451/1.919 ≈ 2.841
# i=4 lan 1 tts=6.66 cov 142.5% => slot = 6.66/1.425 ≈ 4.674
# i=20 chi co cov 126.4% (chua do tts rieng trong dump user)
CASE_A3_SEVERE_49E073 = {
    "id": "A3_severe_49e073",
    "group": "A",
    "rows": (
        {"i": 3, "tts_dur": 5.451, "slot": 2.841, "cov_pct": 191.9},
        {"i": 4, "tts_dur": 6.66, "slot": 4.674, "cov_pct": 142.5},
        {"i": 20, "tts_dur": 4.0, "slot": 3.165, "cov_pct": 126.4},
    ),
    "source": "qa_dump web_49e073e3 i=3/4/20 cov 191.9/142.5/126.4%",
}

# Nhom A4: severe nhung SAU lan rut 3, atempo dong van nam trong tran.
# User vi du "cov 126% -> ~1.10x": 126% = 1.26x se VUOT tran 1.20 (fail A3).
# A4 = phan du sau lan 3: 1.10x (thap hon ca 1.15) va 1.18x (giua 1.15 va 1.20).
# Nguon: pattern i=20 web_49e073e3 (126.4% truoc lan 3).
CASE_A4_SEVERE_DYNAMIC_FIT = {
    "id": "A4_severe_dynamic_fit",
    "group": "A",
    "rows": (
        {"tts_dur": 3.482, "slot": 3.165, "needed": 1.10, "expect_tempo": 1.10},
        {"tts_dur": 3.735, "slot": 3.165, "needed": 1.18, "expect_tempo": 1.18},
    ),
    "source": "pattern 49e073 i=20 sau lan 3: 1.10x/1.18x < tran 1.20, PASS",
}
CASE_D2_LAP_CUNG_ZH = {
    "id": "D2_cung_zh_2_vi_tri",
    "group": "D",
    "speaker_id": "boy",
    "zh": "我还是你老婆呢",
    "i_a": 0,
    "i_b": 21,
    "pair": "em/anh",
    "source": "qa_dump web_49e073e3 i=0 vs i=21 cung ZH, toi/ban vs em/anh",
}


# Nhom A5: cung speaker, doan dau dai hon khung nhieu — atempo cap khong du.
# web_e320a330 i=104: tts_raw=8.16 atempo=1.15 nhung tts_dur=1.6 (cat am tham).
# Cung speaker voi i=105. Khong duoc speech_trim; phai semantic hoac fail-closed.
CASE_A5_SAME_SPEAKER_NO_TRIM = {
    "id": "A5_same_speaker_no_silent_trim",
    "group": "A",
    "speaker_id": "narrator",
    "seg_a": {
        "i": 104,
        "tts_dur_raw": 8.16,
        "slot": 5.74,
        "tts_dur_bug": 1.6,
        "atempo": 1.15,
        "text": (
            "Cánh trái máy bay của tôi sắp vỡ. Tín hiệu của bạn đã sẵn sàng. "
            "Không còn chỉ số nhiễu nào trong vùng đỏ."
        ),
    },
    "seg_b": {"i": 105, "tts_dur": 1.87, "slot": 0.58},
    "source": "web_e320a330 i=104/105 cung narrator; 8.16/1.15≈7.09 vs tts_dur 1.6",
}


def classify_qa_row(row: dict) -> str | None:
    """Xep 1 hang QA vao A/B/C/D. Tra None neu khong khop."""
    flags = {str(f) for f in (row.get("flags") or [])}
    # A truoc B: chong_doan / cat audio am tham la fail-closed.
    if "chong_doan" in flags or "audio_truncated" in flags or "cat_khop" in flags:
        return "A"
    if "qua_ngan" in flags or "im_lang" in flags:
        return "B"
    zh = str(row.get("source_text") or "")
    if any(k in zh for k in ("还差", "才", "怎么又", "没")):
        return "C"
    rel = str(row.get("speaker_relation") or "")
    pair = str(row.get("pronoun_pair_used") or "")
    vi = str(row.get("text") or "")
    if rel or pair or any(k in vi for k in ("mẹ", "con", "ba", "bố", "cha")):
        return "D"
    return None


def _fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def _hard_next(slot: float) -> tuple[float, float, float]:
    """hard = next - start - 0.04 → next = start + slot + 0.04."""
    start = 10.0
    nxt = start + float(slot) + 0.04
    return start, nxt, float(slot)


def _fitted_cov(tts: float, hard: float) -> tuple[str, float, float]:
    """Nhom A: neu tts/hard <= 1.15 phai atempo, cov fitted ~100% hard."""
    start, nxt, hard = _hard_next(hard)
    fit = decide_fit(
        tts_dur=tts, hard_limit=hard, speech_slot=max(0.12, hard * 0.9),
        source_start=start, next_start=nxt,
    )
    needed = tts / hard if tts > hard else 1.0
    fitted = tts / needed if needed > 1.0 else tts
    after = plan_placement(
        source_start=start, source_end=start + hard * 0.9,
        next_start=nxt, tts_dur=fitted,
    )
    cov = fitted / hard * 100
    return fit["action"], cov, after["would_overlap"]


def test_c1_prompt_hai_cha() -> None:
    sys1, u1 = _dub_translate_prompt(
        source_lang="zh",
        target_lang="vi",
        source_text=CASE_C1_HAI_CHA["zh"],
        prev_context=CASE_C1_HAI_CHA["prev_zh"],
        next_context="",
        slot_duration=CASE_C1_HAI_CHA["slot"],
        chars_per_second=12.0,
        target_chars=50,
    )
    if "还差X" not in sys1:
        _fail("C1: prompt thieu quy tac 还差X — nhom C")
    if u1["source_text"] != "还差两千":
        _fail(f"C1 payload sai: {u1}")
    print("OK C1 prompt 还差X (nhom C)")


def test_c2_prompt_qa_khong_cat_traloi() -> None:
    sys2, _ = _dub_reslot_prompt(
        source_lang="zh",
        target_lang="vi",
        source_text=CASE_C2_QA_CAN["zh"],
        current_translation="x" * 80,
        prev_context=CASE_C2_QA_CAN["prev_zh"],
        next_context=CASE_C2_QA_CAN["next_zh"],
        target_chars=40,
    )
    if "cau tra loi" not in sys2 and "KHONG bo" not in sys2:
        _fail("C2: reslot phai cam bo menh de tra loi — nhom C")
    if CASE_C2_QA_CAN["slot"] <= 10:
        _fail("C2 slot phai > 10s (do duoc 10.7)")
    print("OK C2 reslot giu hoi-dap slot 10.7s (nhom C)")


def test_c3_traps_trong_prompt() -> None:
    sys1, _ = _dub_translate_prompt(
        source_lang="zh", target_lang="vi",
        source_text="才", prev_context="", next_context="",
        slot_duration=2.0, chars_per_second=12.0, target_chars=24,
    )
    for needle in ("才", "怎么又", "没"):
        if needle not in sys1:
            _fail(f"C3: prompt thieu trap {needle}")
    print("OK C3 trap 才/没/怎么又 trong prompt (nhom C)")


def test_d1_xung_ho_khoa() -> None:
    rules = _pronoun_rules("vi")
    if "BUOC 0" not in rules or "toi/ban" not in rules:
        _fail("D1: thieu Buoc 0 xung ho")
    parsed = _parse_line_rows(
        '{"cast":[{"speaker_id":"mom","speaker_relation":"me noi voi con",'
        '"pronoun_pair_used":"me/con"}],'
        '"lines":['
        '{"i":1,"text":"Me o day","speaker_relation":"me noi voi con",'
        '"pronoun_pair_used":"me/con"},'
        '{"i":2,"text":"Me xin loi con","speaker_relation":"me noi voi con",'
        '"pronoun_pair_used":"me/con"}'
        "]}",
        2,
    )
    if not parsed:
        _fail("D1 parse that bai")
    rows, cast = parsed
    pairs = {r["pronoun_pair_used"] for r in rows}
    if pairs != {"me/con"} or cast[0]["pronoun_pair_used"] != "me/con":
        _fail(f"D1 khong nhat quan: {parsed}")
    print("OK D1 khoa me/con xuyen doan (nhom D)")


def test_d2_cung_zh_hai_vi_tri() -> None:
    """Cung 1 cau ZH lap 2 vi tri (cung speaker) phai cung 1 cap xung ho.

    Nguyen nhan goc: dich Google truoc assign_cast; moi doan tu doan toi/ban vs em/anh.
    Tinh huong giong: (1) cau hook lap cuoi clip (2) Whisper cat 1 cau 2 lan
    (3) overflow reslot khong nhan locked_cast vi relation dang trong.
    """
    zh = CASE_D2_LAP_CUNG_ZH["zh"]
    sid = CASE_D2_LAP_CUNG_ZH["speaker_id"]
    pair = CASE_D2_LAP_CUNG_ZH["pair"]
    segs = [
        {
            "i": CASE_D2_LAP_CUNG_ZH["i_a"],
            "speaker_id": sid,
            "source_text": zh,
            "text": "Toi van la vo cua ban ma",
            "speaker_relation": "",
            "pronoun_pair_used": "",
        },
        {
            "i": 7,
            "speaker_id": "dad",
            "source_text": "你还差多少",
            "text": "Con thieu bao nhieu",
        },
        {
            "i": CASE_D2_LAP_CUNG_ZH["i_b"],
            "speaker_id": sid,
            "source_text": zh,
            "text": "Em van la vo cua anh ma",
            "speaker_relation": "",
            "pronoun_pair_used": "",
        },
    ]
    locked = apply_cast_lock(segs, {
        sid: {
            "speaker_id": sid,
            "speaker_relation": "vo noi voi chong",
            "pronoun_pair_used": pair,
        },
        "dad": {
            "speaker_id": "dad",
            "speaker_relation": "ba noi voi con",
            "pronoun_pair_used": "ba/con",
        },
    })
    a = locked[0]
    b = locked[2]
    if a["pronoun_pair_used"] != b["pronoun_pair_used"]:
        _fail(f"D2 i=0 vs i=21 khac cap: {a['pronoun_pair_used']!r} vs {b['pronoun_pair_used']!r}")
    if a["pronoun_pair_used"] != pair:
        _fail(f"D2 cap sai: {a['pronoun_pair_used']!r}")
    payload = locked_cast_payload(locked)
    boy = next(x for x in payload if x["speaker_id"] == sid)
    if boy["pronoun_pair_used"] != pair:
        _fail(f"D2 locked_cast_payload thieu cap: {payload}")
    empty_payload = locked_cast_payload(segs)
    if not any(x["speaker_id"] == sid for x in empty_payload):
        _fail("D2: payload phai giu speaker_id du relation dang trong")
    print("OK D2 cung ZH 2 vi tri -> cung cap em/anh (nhom D)")


def test_b_pause_khong_fail_qa() -> None:
    rows = []
    for i, case in enumerate((CASE_B1_PAUSE_10_7, CASE_B2_PAUSE_16_2)):
        play = float(case["tts_dur"])
        slot = float(case["slot"])
        cov = play / slot * 100
        if not (case["cov_min"] <= cov <= case["cov_max"]):
            _fail(f"{case['id']} cov {cov:.1f} ngoai {case['cov_min']}-{case['cov_max']}")
        rows.append({
            "i": i,
            "start": 0.0,
            "slot": play,  # speech slot = TTS ngan
            "hard_limit": slot,
            "play": play,
            "speed": 1.0,
            "flags": ["qua_ngan"],
            "text": case["zh"],
        })
        if classify_qa_row(rows[-1]) != "B":
            _fail(f"{case['id']} classify phai B")
    code, summary, _ = audit_qa_rows(rows, tts_engine="openai")
    if code != 0:
        _fail(f"Nhom B pause phai PASS QA: {summary}")
    print("OK B1/B2 pause 3.0/10.7 va 2.8/16.2 khong fail (nhom B)")


def test_a_leftover_atempo_pass() -> None:
    for case in (CASE_A1_LEFTOVER_I2, CASE_A2_LEFTOVER_I7, CASE_A1C_NEARMISS_400A48):
        tts = float(case["tts_dur"])
        hard = float(case["slot"])
        needed = tts / hard
        if needed > 1.15 + 1e-6:
            _fail(f"{case['id']} needed {needed:.3f} > 1.15 — day la overflow that")
        action, cov, still = _fitted_cov(tts, hard)
        if action != "atempo":
            _fail(
                f"{case['id']}: decide_fit phai atempo (nhom A, du 1-14%), got {action} "
                f"tts={tts} hard={hard}"
            )
        if still:
            _fail(f"{case['id']}: sau tua van de cau sau")
        if not (case["cov_min"] <= cov <= case["cov_max"]):
            _fail(f"{case['id']} cov fitted {cov:.1f} ngoai {case['cov_min']}-{case['cov_max']}")
        fake_row = {"flags": ["chong_doan", "shortened_semantic_fail"], "source_text": case["zh"]}
        if classify_qa_row(fake_row) != "A":
            _fail(f"{case['id']} classify hang fail phai A")
    print("OK A1/A2/A1c near-miss tua <=1.15x roi pass (nhom A)")


def test_a3_severe_khong_atempo() -> None:
    """cov 120-200% sau rut: 1.15x khong du. Sau lan 3 + tran 1.20 van overflow -> fail-closed.

    Nguyen nhan goc: A1/A2 la near-miss 101-114%. Pattern moi la ZH dong den muc
    2 lan rut + 1.15x van de; lan 3 + atempo dong van khong vua neu needed > 1.20.
    Tinh huong giong: cau ZH doc rat nhanh, 2 cau gop 1 slot, TTS noi cham hon Whisper.
    """
    from dyflow.dub_constants import TTS_ATEMPO_SEVERE_MAX

    for row in CASE_A3_SEVERE_49E073["rows"]:
        tts = float(row["tts_dur"])
        hard = float(row["slot"])
        cov = tts / hard * 100
        if abs(cov - float(row["cov_pct"])) > 0.6:
            _fail(f"A3 i={row['i']} cov {cov:.1f} != {row['cov_pct']}")
        if cov < 120:
            _fail(f"A3 i={row['i']} cov {cov:.1f} < 120 — phai tach khoi A1/A2")
        start, nxt, h = _hard_next(hard)
        fit = decide_fit(
            tts_dur=tts, hard_limit=h, speech_slot=max(0.12, h * 0.9),
            source_start=start, next_start=nxt,
        )
        if fit["action"] != "overflow":
            _fail(f"A3 i={row['i']}: needed {tts/hard:.3f} > 1.15 phai overflow, got {fit['action']}")
        if not fit["would_overlap"]:
            _fail(f"A3 i={row['i']}: sau 1.15x van phai de cau sau (fail-closed)")
        plan3 = overflow_atempo_plan(
            tts_dur=tts, slot=hard, attempt=3, shorten_effective=True,
        )
        if plan3["cap"] != TTS_ATEMPO_SEVERE_MAX:
            _fail(f"A3 i={row['i']}: lan 3 rut DAT cap phai {TTS_ATEMPO_SEVERE_MAX}, got {plan3}")
        if plan3["action"] != "overflow" or plan3["fits"]:
            _fail(f"A3 i={row['i']}: needed {tts/hard:.3f} > 1.20 van fail-closed, got {plan3}")
        if abs(plan3["tempo"] - TTS_ATEMPO_SEVERE_MAX) > 1e-6:
            _fail(f"A3 i={row['i']}: overflow van tua dung tran, khong {plan3['tempo']}")
        extra = overflow_semantic_try_budget(tts / hard, 2)
        if extra != 3:
            _fail(f"A3 i={row['i']}: sau 2 lan severe phai cho lan 3, got {extra}")
    # i=3 lan 2: chi bot 'that su' — tts 5.638->5.451, deficit ~2.8s
    if shorten_attempt_closes_enough(5.638, 5.451, 5.638 - 2.841):
        _fail("A3 i=3 lan 2 giam 0.187s / deficit ~2.8s phai KHONG DAT")
    weak = overflow_atempo_plan(
        tts_dur=5.451, slot=2.841, attempt=3, shorten_effective=False,
    )
    if weak["cap"] != 1.15:
        _fail(f"A3: lan 3 rut KHONG DAT khong duoc mo tran 1.20, got {weak}")
    sys3, u3 = _dub_reslot_prompt(
        source_lang="zh", target_lang="vi",
        source_text="x", current_translation="y" * 80,
        prev_context="", next_context="", target_chars=40,
        aggressive=True,
        ineffective_hint="chi bo that su",
    )
    if "LAN CAT MANH" not in sys3 or "menh de phu" not in sys3:
        _fail("A3: lan 3 phai yeu cau cat menh de phu")
    if not u3.get("aggressive"):
        _fail("A3: payload thieu aggressive")
    b3 = semantic_shorten_budget(
        text="A" * 80, tts_dur=4.0, available_slot=2.0, chars_per_sec=12.0, attempt=3,
    )
    b2 = semantic_shorten_budget(
        text="A" * 80, tts_dur=4.0, available_slot=2.0, chars_per_sec=12.0, attempt=2,
    )
    if b3["target_chars"] >= b2["target_chars"]:
        _fail(f"A3 lan 3 phai siet hon lan 2: {b2['target_chars']} vs {b3['target_chars']}")
    print("OK A3 severe 120-200% fail-closed o tran 1.20 sau lan 3 (nhom A)")


def test_a4_severe_dynamic_atempo_pass() -> None:
    """Sau lan rut 3, atempo = dung muc can, nam trong tran 1.20 -> PASS.

    Nguyen nhan goc: decide_fit / _force_atempo chi cap 1.15 nen 1.18x fail du da rut 3.
    Tinh huong giong: i=20 126% sau cat menh de phu con ~1.10-1.18x.
    """
    from dyflow.dub_constants import TTS_ATEMPO_SAFE_MAX, TTS_ATEMPO_SEVERE_MAX

    for row in CASE_A4_SEVERE_DYNAMIC_FIT["rows"]:
        tts = float(row["tts_dur"])
        hard = float(row["slot"])
        expect = float(row["expect_tempo"])
        needed = tts / hard
        if abs(needed - float(row["needed"])) > 0.005:
            _fail(f"A4 needed {needed:.3f} != {row['needed']}")
        plan = overflow_atempo_plan(
            tts_dur=tts, slot=hard, attempt=3, shorten_effective=True,
        )
        if plan["action"] != "atempo" or not plan["fits"]:
            _fail(f"A4 phai PASS atempo dong, got {plan}")
        if abs(plan["tempo"] - expect) > 0.005:
            _fail(f"A4 tempo phai dung {expect} (khong snap {TTS_ATEMPO_SEVERE_MAX}), got {plan['tempo']}")
        if plan["tempo"] > TTS_ATEMPO_SEVERE_MAX + 1e-9:
            _fail(f"A4 vuot tran {TTS_ATEMPO_SEVERE_MAX}: {plan}")
        start, nxt, h = _hard_next(hard)
        fit = decide_fit(
            tts_dur=tts, hard_limit=h, speech_slot=max(0.12, h * 0.9),
            source_start=start, next_start=nxt,
            atempo_cap=TTS_ATEMPO_SEVERE_MAX,
        )
        if fit["action"] != "atempo" or fit["would_overlap"]:
            _fail(f"A4 decide_fit cap 1.20 phai vua, got {fit}")
        if abs(fit["tempo"] - expect) > 0.005:
            _fail(f"A4 decide_fit tempo {fit['tempo']} != {expect}")
        if expect > TTS_ATEMPO_SAFE_MAX + 0.001:
            old = decide_fit(
                tts_dur=tts, hard_limit=h, speech_slot=max(0.12, h * 0.9),
                source_start=start, next_start=nxt,
            )
            if old["action"] != "overflow":
                _fail(f"A4 {expect}x phai overflow o cap 1.15 cu, got {old}")
        weak = overflow_atempo_plan(
            tts_dur=tts, slot=hard, attempt=3, shorten_effective=False,
        )
        if weak["cap"] != TTS_ATEMPO_SAFE_MAX:
            _fail(f"A4 khong duoc mo tran khi rut chua dat: {weak}")
    print("OK A4 severe atempo dong 1.10/1.18x trong tran 1.20 PASS (nhom A)")


def test_a5_same_speaker_khong_cat_audio() -> None:
    """Cung speaker, doan dau dai hon khung: atempo cap khong du.

    Nguyen nhan goc: close_file_to_slot + silenceremove stop_periods / atrim mix
    cat file roi gan overflow_reason=khong_tran — QA coi la binh thuong.
    Tinh huong giong: 2 cau narrator lien tiep, cau 1 ZH dai, TTS 8s / slot 5.7s.
    """
    from dyflow.dub_constants import TTS_ATEMPO_SAFE_MAX, TTS_ATEMPO_SEVERE_MAX

    a = CASE_A5_SAME_SPEAKER_NO_TRIM["seg_a"]
    raw = float(a["tts_dur_raw"])
    hard = float(a["slot"])
    bug = float(a["tts_dur_bug"])
    tempo = float(a["atempo"])
    expected = raw / tempo
    if abs(expected - 8.16 / 1.15) > 0.02:
        _fail(f"A5 expected after atempo {expected}")
    if not is_audio_truncated(raw, bug, tempo):
        _fail(f"A5 bug 8.16->1.6 sau 1.15x phai audio_truncated (expected {expected:.2f})")
    if is_audio_truncated(raw, expected, tempo):
        _fail("A5 atempo thuan tuy khong duoc coi la truncated")

    after_15 = raw / TTS_ATEMPO_SAFE_MAX
    if after_15 <= hard:
        _fail("A5 1.15x phai van tran")
    plan15 = overlap_close_plan(
        tts_dur=after_15, slot=hard, atempo_already=TTS_ATEMPO_SAFE_MAX,
        cap=TTS_ATEMPO_SAFE_MAX, allow_speech_trim=False, allow_micro_overlap=False,
    )
    if plan15["action"] != "overflow" or plan15.get("trim") or plan15.get("speech_trim"):
        _fail(f"A5 sau 1.15x leftover > pad: khong cat, phai overflow de semantic, got {plan15}")

    after_20 = raw / TTS_ATEMPO_SEVERE_MAX
    plan20 = overlap_close_plan(
        tts_dur=after_20, slot=hard, atempo_already=TTS_ATEMPO_SEVERE_MAX,
        cap=TTS_ATEMPO_SEVERE_MAX, allow_speech_trim=True, allow_micro_overlap=True,
    )
    leftover20 = after_20 - hard
    if leftover20 <= 0.25:
        _fail(f"A5 fixture phai leftover >250ms o 1.20x, got {leftover20:.3f}")
    if plan20["action"] != "overflow" or plan20.get("trim") or plan20["fits"]:
        _fail(f"A5 sau semantic+1.20 van tran: fail-closed, KHONG speech_trim, got {plan20}")

    segs = [
        {"speaker_id": CASE_A5_SAME_SPEAKER_NO_TRIM["speaker_id"], "start": 0.0, "end": hard, "text": "a"},
        {"speaker_id": CASE_A5_SAME_SPEAKER_NO_TRIM["speaker_id"], "start": hard + 0.04, "end": hard + 1.0, "text": "b"},
    ]
    nxt, why = overflow_merge_plan(segs, 0)
    if nxt != 1 or why != "gop":
        _fail(f"A5 cung speaker phai gop truoc semantic, got nxt={nxt} why={why}")
    segs_diff = [
        segs[0],
        {**segs[1], "speaker_id": "other"},
    ]
    nxt2, why2 = overflow_merge_plan(segs_diff, 0)
    if nxt2 is not None or why2 != "khac_nhan_vat":
        _fail(f"A5 khac speaker phai khac_nhan_vat, got {nxt2} {why2}")

    extra = overflow_semantic_try_budget(raw / hard, 2)
    if extra != 3:
        _fail(f"A5 cov {raw/hard:.2f} severe phai cho lan 3 semantic, got {extra}")

    qa_bug = {
        "i": a["i"],
        "start": 913.76,
        "slot": hard,
        "hard_limit": hard,
        "play": bug,
        "tts_dur_raw": raw,
        "tts_dur": bug,
        "atempo_applied": tempo,
        "speed": 1.0,
        "flags": ["atempo"],
        "overflow_reason": "khong_tran",
        "text": a["text"],
        "speaker_id": CASE_A5_SAME_SPEAKER_NO_TRIM["speaker_id"],
    }
    if classify_qa_row({**qa_bug, "flags": ["audio_truncated"]}) != "A":
        _fail("A5 audio_truncated phai nhom A")
    code, summary, _ = audit_qa_rows([qa_bug], tts_engine="edge")
    if code == 0:
        _fail(f"A5 QA hang bug khong_tran 8.16->1.6 phai FAIL, got {summary}")
    print("OK A5 cung speaker khong cat audio — semantic/fail-closed (nhom A)")


def test_groups_day_du() -> None:
    ids = {
        CASE_C1_HAI_CHA["id"], CASE_C2_QA_CAN["id"], CASE_C3_CAI_MEI["id"],
        CASE_D1_ME_CON["id"], CASE_D2_LAP_CUNG_ZH["id"],
        CASE_B1_PAUSE_10_7["id"], CASE_B2_PAUSE_16_2["id"],
        CASE_A1_LEFTOVER_I2["id"], CASE_A2_LEFTOVER_I7["id"],
        CASE_A1C_NEARMISS_400A48["id"], CASE_A3_SEVERE_49E073["id"],
        CASE_A4_SEVERE_DYNAMIC_FIT["id"], CASE_A5_SAME_SPEAKER_NO_TRIM["id"],
    }
    if len(ids) != 13:
        _fail(f"trung id hoac thieu case: {ids}")
    print("OK catalog 13 case, 4 nhom A/B/C/D")


def main() -> int:
    test_groups_day_du()
    test_c1_prompt_hai_cha()
    test_c2_prompt_qa_khong_cat_traloi()
    test_c3_traps_trong_prompt()
    test_d1_xung_ho_khoa()
    test_d2_cung_zh_hai_vi_tri()
    test_b_pause_khong_fail_qa()
    test_a_leftover_atempo_pass()
    test_a3_severe_khong_atempo()
    test_a4_severe_dynamic_atempo_pass()
    test_a5_same_speaker_khong_cat_audio()
    print("ALL CASES OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
