#!/usr/bin/env python3
"""Verify archaic address rules + live 老夫 translation."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.preflight import hydrate_openai_key, openai_key  # noqa: E402
from dyflow.translate_llm import (  # noqa: E402
    _archaic_address_rules,
    _archaic_literal_bad,
    _drama_dub_rules,
    _dub_reslot_prompt,
    retranslate_segment_faithful,
    translate_segments_budgeted,
)


def main() -> int:
    rules = _archaic_address_rules("Vietnamese")
    assert "老夫" in rules
    assert "lao phu" in rules
    assert "ong gia" in rules or "ông già" in rules
    assert "老夫" in _drama_dub_rules("Vietnamese")
    sys_p, _ = _dub_reslot_prompt(
        source_lang="zh",
        target_lang="vi",
        source_text="老夫来也",
        current_translation="Ông già đến rồi",
        prev_context="",
        next_context="",
        target_chars=20,
    )
    assert "老夫" in sys_p
    assert _archaic_literal_bad("老夫今日来此", "Ông già hôm nay đến đây")
    assert not _archaic_literal_bad("老夫今日来此", "Lão phu hôm nay đến đây")
    print("STATIC OK")

    hydrate_openai_key()
    if not openai_key():
        print("SKIP live — no OpenAI key")
        return 0

    segs = [{
        "start": 0.0,
        "end": 3.5,
        "source_text": "老夫今日特来此地。",
        "text": "老夫今日特来此地。",
        "speaker_id": "elder",
    }]
    out = retranslate_segment_faithful(
        segs[0],
        segment_index=0,
        segments=segs,
        video_dur=4.0,
        target="vi",
        chars_per_sec=14.5,
        topic_context="Phim co trang / tien hiep — giu tu xung Han-Viet.",
    )
    print("RETRANSLATE:", out)
    assert out
    assert not _archaic_literal_bad(segs[0]["source_text"], out), out
    assert not re.search(r"ông\s*già|ong\s*gia", out, re.I), out
    assert re.search(r"lão\s*phu", out, re.I), f"can co dau: {out}"
    print("LAO_PHU_OK")

    segs2 = [
        {
            "start": 0.0, "end": 2.5,
            "source_text": "老夫看你资质不错。",
            "text": "老夫看你资质不错。",
            "speaker_id": "elder",
        },
        {
            "start": 2.5, "end": 5.0,
            "source_text": "本座今日便收你为徒。",
            "text": "本座今日便收你为徒。",
            "speaker_id": "elder",
        },
        {
            "start": 5.0, "end": 7.0,
            "source_text": "多谢前辈！",
            "text": "多谢前辈！",
            "speaker_id": "disciple",
        },
    ]
    wd = ROOT / "work" / "_verify_archaic"
    wd.mkdir(parents=True, exist_ok=True)
    tr = translate_segments_budgeted(
        segs2,
        video_dur=8.0,
        target="vi",
        chars_per_sec=14.5,
        work_dir=wd,
        require_llm=True,
    )
    for s in tr:
        print(f"{s.get('source_text')} => {s.get('text')}")
        assert not _archaic_literal_bad(
            str(s.get("source_text") or ""),
            str(s.get("text") or ""),
        ), s
    blob = " ".join(str(s.get("text") or "") for s in tr)
    assert not re.search(r"ông\s*già|ong\s*gia", blob, re.I), blob
    print("BATCH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
