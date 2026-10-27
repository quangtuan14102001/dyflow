#!/usr/bin/env python3
"""Gop slot dub cho doan qua_dai + dich lai + (tuy chon) resynth QA."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_constants import TARGET_CHARS_PER_SEC  # noqa: E402
from dyflow.dub_merge import (  # noqa: E402
    find_segment_near_start,
    merge_adjacent_pairs,
    merge_qua_dai_plan,
    refresh_segment_budgets,
    retranslate_merged_segments,
)
from dyflow.utils import load_config, log, probe_duration  # noqa: E402


def _parse_pairs(raw: str) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for chunk in raw.split():
        a, b = chunk.split(",")
        pairs.append((int(a), int(b)))
    return pairs


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".pre_merge.bak")
    shutil.copy2(path, bak)
    return bak


def main() -> int:
    p = argparse.ArgumentParser(description="Merge dub segments (slot ngan / qua_dai)")
    p.add_argument("work_dir", nargs="?", default="work/review_sample")
    p.add_argument(
        "--pairs",
        default="",
        help='Cap gop "left,right" (vd. "3,4 8,9"). De trong = khong gop theo cap.',
    )
    p.add_argument(
        "--ids",
        default="",
        help="Tu dong sinh cap tu danh sach id qua_dai (vd: 3,8,12,14,15,17)",
    )
    p.add_argument(
        "--check-17",
        action="store_true",
        help="Sau resynth: neu doan ~41.56s van qua_dai thi gop voi doan truoc",
    )
    p.add_argument("--resynth", action="store_true", help="Chay test_dub_quality --resynth sau merge")
    p.add_argument("--dry-run", action="store_true", help="Chi in ke hoach, khong ghi file")
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
    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        pairs = merge_qua_dai_plan(segments, ids)
    else:
        pairs = _parse_pairs(args.pairs)

    print(f"Merge {len(pairs)} cap: {pairs}")
    for left, right in pairs:
        a, b = segments[left], segments[right]
        slot_new = float(b["end"]) - float(a["start"])
        print(
            f"  #{left}+#{right}: {a['start']:.2f}-{b['end']:.2f} "
            f"slot {slot_new:.2f}s | zh: {a.get('source_text','')[:20]}... + ..."
        )

    if args.dry_run:
        return 0

    merged, merged_left = merge_adjacent_pairs(segments, pairs)
    refresh_segment_budgets(merged, video_dur=video_dur, chars_per_sec=cps)
    merged = retranslate_merged_segments(
        merged,
        merged_left,
        video_dur=video_dur,
        chars_per_sec=cps,
        work_dir=work,
    )

    bak = _backup(seg_path)
    seg_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Da ghi {len(merged)} doan -> {seg_path} (backup {bak.name})")
    for idx in merged_left:
        seg = merged[idx]
        print(
            f"  merged #{idx}: slot={seg.get('slot_sec')}s "
            f"chars={len(seg.get('text',''))}/{seg.get('target_chars')} | {seg.get('text','')[:70]}"
        )

    if args.resynth:
        import subprocess

        cmd = [
            sys.executable,
            str(ROOT / "scripts/test_dub_quality.py"),
            str(work),
            "--resynth",
        ]
        print("Chay resynth QA...")
        subprocess.run(cmd, check=False)

        if args.check_17:
            qa_path = work / "segments_qa.json"
            if qa_path.exists():
                qa = json.loads(qa_path.read_text(encoding="utf-8"))
                idx17 = find_segment_near_start(merged, 41.56)
                qua_dai_ids = [r["i"] for r in qa if "qua_dai" in (r.get("flags") or [])]
                print(f"qua_dai sau phase 1: {qua_dai_ids}")
                if idx17 is not None and idx17 in qua_dai_ids and idx17 > 0:
                    print(f"#17 van qua_dai (idx {idx17}) — gop voi #{idx17 - 1}")
                    phase2_pairs = [(idx17 - 1, idx17)]
                    segments2 = json.loads(seg_path.read_text(encoding="utf-8"))
                    merged2, left2 = merge_adjacent_pairs(segments2, phase2_pairs)
                    refresh_segment_budgets(merged2, video_dur=video_dur, chars_per_sec=cps)
                    merged2 = retranslate_merged_segments(
                        merged2,
                        left2,
                        video_dur=video_dur,
                        chars_per_sec=cps,
                        work_dir=work,
                    )
                    seg_path.write_text(json.dumps(merged2, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"Phase 2: {len(merged2)} doan — resynth lai...")
                    subprocess.run([*cmd, "--fail"], check=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
