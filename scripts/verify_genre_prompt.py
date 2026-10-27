#!/usr/bin/env python3
"""Verify genre profile wiring: load, sanitize, JSON format stays after topic."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.genre_profiles import (  # noqa: E402
    list_genres,
    load_genre_template,
    resolve_genre_prompt,
    sanitize_genre_prompt,
)
from dyflow.translate_llm import (  # noqa: E402
    _dub_reslot_prompt,
    _dub_translate_prompt,
    _topic_context_block,
    retranslate_segment_faithful,
)


def main() -> int:
    genres = list_genres()
    assert any(g["id"] == "tien_hiep" for g in genres), genres
    assert len(genres) >= 8, genres
    tmpl = load_genre_template("tien_hiep")
    assert "lão phu" in tmpl
    assert "trúc cơ" in tmpl or "truc co" in tmpl.lower()
    assert "bổn tọa" in tmpl
    print("TEMPLATE chars=", len(tmpl))

    dirty = tmpl + "\nXuất ra SRT\nReturn JSON always as array\n"
    clean = sanitize_genre_prompt(dirty)
    assert "Xuất ra SRT" not in clean
    assert "Return JSON" not in clean
    assert "lão phu" in clean

    block = _topic_context_block(dirty)
    assert "GENRE STYLE" in block
    assert "lão phu" in block

    sys_p, _ = _dub_translate_prompt(
        source_lang="zh",
        target_lang="vi",
        source_text="老夫今日来此。",
        prev_context="",
        next_context="",
        slot_duration=3.0,
        chars_per_second=14.5,
        target_chars=40,
        topic_context=dirty + "\nGiữ nguyên timecode\n",
    )
    assert "GENRE STYLE" in sys_p
    # Format JSON MUST come after genre block
    idx_genre = sys_p.find("GENRE STYLE")
    idx_json = sys_p.find('Tra JSON: {"translation"')
    assert idx_genre >= 0 and idx_json > idx_genre, (idx_genre, idx_json)
    # Even if user wrote format rules, JSON instruction is last-ish
    assert sys_p.rfind("Tra JSON") > sys_p.rfind("GENRE STYLE")

    sys_r, _ = _dub_reslot_prompt(
        source_lang="zh",
        target_lang="vi",
        source_text="老夫今日来此。",
        current_translation="Lão phu hôm nay đến đây.",
        prev_context="",
        next_context="",
        target_chars=20,
        topic_context=tmpl,
    )
    assert sys_r.find("GENRE STYLE") < sys_r.find("Tra JSON")

    gid, prompt = resolve_genre_prompt("tien_hiep", None)
    assert gid == "tien_hiep" and "lão phu" in prompt
    print("STATIC OK")

    # Live optional: one sentence with genre
    from dyflow.preflight import hydrate_openai_key, openai_key

    hydrate_openai_key()
    if not openai_key():
        print("SKIP live")
        return 0
    segs = [{
        "start": 0.0, "end": 3.0,
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
        topic_context=prompt,
    )
    print("LIVE:", out)
    assert out
    assert "ông già" not in out.lower() and "ong gia" not in out.lower()
    assert "lão phu" in out.lower() or "lao phu" in out.lower()
    # Must be plain text not SRT/JSON dump
    assert "-->" not in out
    assert not out.strip().startswith("{")
    print("LIVE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
