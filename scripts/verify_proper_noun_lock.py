#!/usr/bin/env python3
"""Verify proper-noun / term-lock rules (sci-fi job web_e320a330 style)."""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.preflight import hydrate_openai_key, openai_key  # noqa: E402
from dyflow.translate_llm import (  # noqa: E402
    _drama_dub_rules,
    _dub_reslot_prompt,
    _english_term_leak,
    _proper_noun_rules,
    _term_lock_note,
    apply_term_lock,
    load_term_lock,
    lock_cast_relations,
    retranslate_segment_faithful,
)


def _assert_rules() -> None:
    rules = _proper_noun_rules("Vietnamese")
    assert "Bạch Trạch" in rules or "Bach Trach" in rules
    assert "Bai Ze" in rules
    assert "Khoa Phụ" in rules or "Khoa Phu" in rules
    assert "Kwafu" in rules
    assert "Tinh Vệ" in rules or "Tinh Ve" in rules
    assert "Formation" in rules
    assert "Cung Vây Rộng" in rules or "Cung Vay Rong" in rules
    drama = _drama_dub_rules("Vietnamese")
    assert "Bai Ze" in drama
    assert "Azure Dragon Formation" in drama
    assert _english_term_leak("Bai Ze xuất hiện")
    assert _english_term_leak("Kích hoạt Azure Dragon Formation")
    assert _english_term_leak("Wide Containment Bow sẵn sàng")
    assert _english_term_leak("Hoàng hậu Kwafu")
    assert not _english_term_leak("Bạch Trạch xuất hiện")
    assert not _english_term_leak("Đội Hình Thanh Long")
    assert not _english_term_leak("Cung Vây Rộng sẵn sàng")
    note = _term_lock_note([
        {"source": "白泽", "locked_vi": "Bạch Trạch", "kind": "myth"},
        {"source": "青龙阵", "locked_vi": "Đội Hình Thanh Long", "kind": "formation"},
    ])
    assert "LOCKED TERMS" in note
    assert "Bạch Trạch" in note
    sys_p, user = _dub_reslot_prompt(
        source_lang="zh",
        target_lang="vi",
        source_text="启动青龙阵",
        current_translation="Kích hoạt Azure Dragon Formation",
        prev_context="",
        next_context="",
        target_chars=40,
        locked_terms=[
            {"source": "青龙阵", "locked_vi": "Đội Hình Thanh Long", "kind": "formation"},
        ],
    )
    assert "LOCKED TERMS" in sys_p
    assert "Đội Hình Thanh Long" in sys_p
    assert user.get("locked_terms")
    print("STATIC OK")


def _assert_apply_load() -> None:
    segs = [
        {"start": 0, "end": 2, "source_text": "白泽来了", "text": "白泽来了", "speaker_id": "a"},
        {"start": 2, "end": 4, "source_text": "青龙阵", "text": "青龙阵", "speaker_id": "b"},
    ]
    terms = [
        {"source": "白泽", "locked_vi": "Bạch Trạch", "kind": "myth"},
        {"source": "青龙阵", "locked_vi": "Đội Hình Thanh Long", "kind": "formation"},
    ]
    stamped = apply_term_lock(segs, terms)
    assert stamped[0]["term_lock"][0]["locked_vi"] == "Bạch Trạch"
    with tempfile.TemporaryDirectory() as td:
        wp = Path(td)
        (wp / "term_lock.json").write_text(
            json.dumps({"terms": terms}, ensure_ascii=False), encoding="utf-8",
        )
        loaded = load_term_lock(None, wp)
        assert len(loaded) == 2
        assert loaded[1]["locked_vi"] == "Đội Hình Thanh Long"
    print("APPLY_LOAD OK")


def _live() -> int:
    hydrate_openai_key()
    if not openai_key():
        print("SKIP live — no OpenAI key")
        return 0

    segs = [
        {
            "start": 0.0, "end": 3.0,
            "source_text": "启动宽域围困弓！",
            "text": "启动宽域围困弓！",
            "speaker_id": "commander",
            "speaker_role": "commander",
        },
        {
            "start": 3.0, "end": 6.0,
            "source_text": "青龙阵已就绪。",
            "text": "青龙阵已就绪。",
            "speaker_id": "commander",
            "speaker_role": "commander",
        },
        {
            "start": 6.0, "end": 9.0,
            "source_text": "精卫阵展开！",
            "text": "精卫阵展开！",
            "speaker_id": "soldier",
            "speaker_role": "soldier",
        },
        {
            "start": 9.0, "end": 12.0,
            "source_text": "白泽现身了。",
            "text": "白泽现身了。",
            "speaker_id": "soldier",
            "speaker_role": "soldier",
        },
        {
            "start": 12.0, "end": 15.0,
            "source_text": "夸父在追日。",
            "text": "夸父在追日。",
            "speaker_id": "narrator",
            "speaker_role": "narrator",
        },
    ]
    with tempfile.TemporaryDirectory() as td:
        wp = Path(td)
        locked = lock_cast_relations(segs, target="vi", work_dir=wp)
        terms = load_term_lock(locked, wp)
        print("TERM_LOCK:", json.dumps(terms, ensure_ascii=False, indent=2))
        assert terms, "lock_cast phải trả terms"
        joined_src = " ".join(t["source"] for t in terms)
        joined_vi = " ".join(t["locked_vi"] for t in terms)
        # Ít nhất phải khóa được vài tên quan trọng
        assert any("白泽" in t["source"] or "Bạch" in t["locked_vi"] for t in terms), terms
        for t in terms:
            assert not _english_term_leak(t["locked_vi"]), t
            assert not re.search(
                r"\b(?:Formation|Bai\s*Ze|Kwafu|Jingwei|Containment)\b",
                t["locked_vi"],
                re.I,
            ), t

        # Repair path: bản dịch dính tiếng Anh → phải sửa
        bad = {
            **locked[0],
            "text": "Kích hoạt Wide Containment Bow!",
            "term_lock": terms,
        }
        fixed = retranslate_segment_faithful(
            bad,
            segment_index=0,
            segments=locked,
            video_dur=16.0,
            target="vi",
            chars_per_sec=14.5,
            emphasis=(
                "CAM de Formation/Bow/Bai Ze bang tieng Anh. "
                "Dung locked_vi: "
                + "; ".join(f"{t['source']}→{t['locked_vi']}" for t in terms)
            ),
            topic_context=_term_lock_note(terms).strip(),
        )
        print("REPAIR Wide Containment Bow ->", fixed)
        assert fixed
        assert not _english_term_leak(fixed), fixed
        assert "Formation" not in fixed and "Containment" not in fixed, fixed

        bad2 = {
            **locked[3],
            "text": "Bai Ze xuất hiện rồi.",
            "term_lock": terms,
        }
        fixed2 = retranslate_segment_faithful(
            bad2,
            segment_index=3,
            segments=locked,
            video_dur=16.0,
            target="vi",
            chars_per_sec=14.5,
            emphasis="白泽 = Bạch Trạch, CAM Bai Ze.",
            topic_context=_term_lock_note(terms).strip(),
        )
        print("REPAIR Bai Ze ->", fixed2)
        assert fixed2
        assert not _english_term_leak(fixed2), fixed2
        assert re.search(r"Bạch\s*Trạch|Bach\s*Trach", fixed2, re.I), fixed2

        print("LIVE OK", "sources=", joined_src[:80], "vi=", joined_vi[:80])
    return 0


def main() -> int:
    _assert_rules()
    _assert_apply_load()
    return _live()


if __name__ == "__main__":
    raise SystemExit(main())
