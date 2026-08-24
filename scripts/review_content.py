#!/usr/bin/env python3
"""Validate noi dung dich: sua #0, LLM reviewer 18 doan -> content_review_full.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_constants import TARGET_CHARS_PER_SEC  # noqa: E402
from dyflow.dub_report import (  # noqa: E402
    scan_content_review_flags,
    write_content_review_flags,
    write_content_review_full,
)
from dyflow.translate_llm import retranslate_segment_faithful, review_segments_content  # noqa: E402
from dyflow.utils import load_config, log, probe_duration  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Review noi dung dich vs zh goc")
    p.add_argument("work_dir", nargs="?", default="work/hemorrhoid_care_vi_edge")
    p.add_argument("--fix-zero", action="store_true", help="Dich lai doan #0 truoc khi review")
    p.add_argument("--no-fix-zero", action="store_true", help="Khong sua #0 (mac dinh khi review lai)")
    p.add_argument("--write", action="store_true", help="Ghi segments_vi_goc.json sau khi sua #0")
    args = p.parse_args()

    work = Path(args.work_dir)
    seg_path = work / "segments_vi_goc.json"
    if not seg_path.exists():
        print(f"Thieu {seg_path}")
        return 2

    cfg = load_config(str(ROOT / "config.yaml"))
    cps = float(cfg.get("voiceover", {}).get("auto", {}).get("target_chars_per_sec") or TARGET_CHARS_PER_SEC)
    video = work / "edited.mp4"
    video_dur = probe_duration(video) if video.exists() else 44.0

    segments = json.loads(seg_path.read_text(encoding="utf-8"))

    if args.fix_zero and not args.no_fix_zero and segments:
        emphasis = (
            "This line is about prolapsed hemorrhoid that cannot be pushed back in. "
            "Do NOT mention spicy food or eating habits."
        )
        fixed = retranslate_segment_faithful(
            segments[0],
            segment_index=0,
            segments=segments,
            video_dur=video_dur,
            chars_per_sec=cps,
            emphasis=emphasis,
        )
        if fixed:
            old = segments[0].get("text", "")
            segments[0]["text"] = fixed
            print(f"Fixed #0:\n  cu: {old}\n  moi: {fixed}")
            if args.write:
                seg_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"Da ghi {seg_path}")
        else:
            print("Khong dich lai duoc #0 — kiem tra OPENAI_API_KEY")

    reviews = review_segments_content(segments)
    if not reviews:
        print("Khong co ket qua review (API loi?)")
        return 1

    write_content_review_full(work, reviews)
    flags = scan_content_review_flags(segments)
    write_content_review_flags(work, flags)

    summary = {
        "khop": sum(1 for r in reviews if r["score"] == "khop"),
        "gan_dung": sum(1 for r in reviews if r["score"] == "gan_dung"),
        "sai": sum(1 for r in reviews if r["score"] == "sai"),
    }
    print(f"\nTong ket: khop={summary['khop']} gan_dung={summary['gan_dung']} sai={summary['sai']}/{len(reviews)}")
    print(f"-> {work / 'content_review_full.json'}")

    print(f"\nBang review day du {len(reviews)}/{len(reviews)}:")
    print(f"{'#':>2} {'start':>6} {'score':>9}  vi (text thuc te trong file)")
    print("-" * 72)
    for r in reviews:
        vi = (r.get("translated_text") or "")[:52]
        print(f"{r['i']:2d} {r['start']:6.1f} {r['score']:>9}  {vi}")

    need = [r for r in reviews if r["score"] in ("sai", "gan_dung")]
    if need:
        print("\nCan xem lai (sai / gan_dung):")
        for r in need:
            print(
                f"  #{r['i']} [{r['score']}] {r['start']:.1f}-{r['end']:.1f}s\n"
                f"    zh: {r['source_text'][:60]}\n"
                f"    vi: {r['translated_text'][:60]}\n"
                f"    ly do: {r['reason'][:120]}"
            )
            if r.get("suggested_fix"):
                print(f"    goi y: {r['suggested_fix'][:100]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
