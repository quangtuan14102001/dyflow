#!/usr/bin/env python3
"""Tao segments_vi_goc.json: Whisper zh + merge + dich budget theo slot."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_constants import (  # noqa: E402
    DUB_GROUP_MAX_CHARS,
    DUB_GROUP_MAX_DUR_SEC,
    DUB_GROUP_MAX_GAP_SEC,
    TARGET_CHARS_PER_SEC,
)
from dyflow.dub_report import (  # noqa: E402
    scan_content_review_flags,
    write_content_review_flags,
    write_transcribe_report,
)
from dyflow.dub_merge import group_utterances  # noqa: E402
from dyflow.transcribe import (  # noqa: E402
    dedupe_segments,
    extract_audio,
    transcribe_segments,
)
from dyflow.translate_llm import translate_segments_budgeted  # noqa: E402
from dyflow.utils import load_config, log, probe_duration  # noqa: E402


def main() -> int:
    work = Path(sys.argv[1] if len(sys.argv) > 1 else "work/review_sample")
    audio = work / "audio16k.wav"
    video = work / "edited.mp4"
    if not audio.exists():
        if video.exists():
            extract_audio(video, audio)
        else:
            print("Thieu audio16k.wav — chay auto_script hoac extract truoc")
            return 2

    cfg = load_config(str(ROOT / "config.yaml"))
    auto_cfg = cfg.get("voiceover", {}).get("auto", {})
    model_size = auto_cfg.get("whisper_model", "medium")
    source_lang = auto_cfg.get("source_lang", "zh")
    merge_gap = DUB_GROUP_MAX_GAP_SEC
    merge_max_dur = DUB_GROUP_MAX_DUR_SEC
    merge_max_chars = DUB_GROUP_MAX_CHARS
    cps = float(auto_cfg.get("target_chars_per_sec") or TARGET_CHARS_PER_SEC)

    zh_segs, had_loops = transcribe_segments(
        audio,
        model_size=model_size,
        source_lang=source_lang,
        vad=False,
    )
    if had_loops:
        log.warning("Whisper co lap — van dung ban goc (xem transcribe_report)")

    raw_n = len(zh_segs)
    zh_segs = dedupe_segments(zh_segs)
    if len(zh_segs) != raw_n:
        print(f"Dedupe zh: {raw_n} -> {len(zh_segs)} doan")

    video_dur = probe_duration(video) if video.exists() else probe_duration(audio)
    write_transcribe_report(work, zh_segs, video_dur, source="whisper_goc_raw")

    zh_path = work / "segments_zh_whisper.json"
    zh_path.write_text(json.dumps(zh_segs, ensure_ascii=False, indent=2), encoding="utf-8")

    zh_merged = group_utterances(
        [
            {
                "start": float(s["start"]),
                "end": float(s["end"]),
                "text": s["text"],
                "source_text": s["text"],
            }
            for s in zh_segs
        ],
        max_gap=merge_gap,
        max_dur=merge_max_dur,
        max_chars=merge_max_chars,
    )
    zh_merged = dedupe_segments(zh_merged)
    print(
        f"Merge zh: {len(zh_segs)} -> {len(zh_merged)} doan "
        f"(gap<={merge_gap}s, max_dur={merge_max_dur}s, max_chars={merge_max_chars})"
    )

    vi = translate_segments_budgeted(
        zh_merged,
        video_dur=video_dur,
        target="vi",
        chars_per_sec=cps,
        work_dir=work,
    )

    content_flags = scan_content_review_flags(vi)
    write_content_review_flags(work, content_flags)

    out = work / "segments_vi_goc.json"
    out.write_text(json.dumps(vi, ensure_ascii=False, indent=2), encoding="utf-8")
    write_transcribe_report(work, vi, video_dur, source="whisper_goc_merged")

    print(f"Da ghi {len(vi)} doan -> {out} (Whisper + merge + dich budget)")
    print(
        f"Zh raw -> {zh_path.name} | Video {video_dur:.1f}s | "
        f"TARGET_CHARS_PER_SEC={cps}"
    )
    if content_flags:
        print(f"Content review: {len(content_flags)} doan -> content_review_flags.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
