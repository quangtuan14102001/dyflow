#!/usr/bin/env python3
"""QA dub doc lap — bang so sanh duration goc vs TTS vs sau xu ly.

  python scripts/test_dub_quality.py work/<job>
  python scripts/test_dub_quality.py work/<job> --resynth
  python scripts/test_dub_quality.py work/<job> --from-translate --resynth --fail
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_constants import (  # noqa: E402
    CUMULATIVE_WARN_MS,
    TIMING_WARN_MS,
    TTS_RATE_MATCH_TOLERANCE,
    VOLUME_WARN_DB,
)
from dyflow.dub_report import qa_summary_stats  # noqa: E402
from dyflow.utils import load_config, probe_duration  # noqa: E402


def _load_segments(work: Path, *, fresh: bool = False) -> list[dict]:
    if fresh:
        order = (
            "segments_vi_goc.json",
            "segments_vi.json",
            "segments_prosody.json",
            "segments_cues.json",
        )
    else:
        order = (
            "segments_cues.json",
            "segments_prosody.json",
            "segments_vi.json",
            "segments_vi_fixed.json",
        )
    for name in order:
        p = work / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return []


def _slot_for_seg(segs: list[dict], i: int, video_dur: float) -> float:
    s = segs[i]
    start = float(s["start"])
    next_start = float(segs[i + 1]["start"]) if i + 1 < len(segs) else video_dur
    return max(0.12, next_start - start - 0.04)


def _clear_dub_cache(work: Path) -> None:
    """Xoa cache dich/TTS — khong tai dung shortened/seg cu."""
    work = Path(work)
    for name in (
        "segments_vi.json",
        "segments_cues.json",
        "segments_qa.json",
        "segments_prosody.json",
        "translate_report.csv",
        "script_auto_vi.txt",
        "transcript_goc.txt",
        "can_rut_gon_cau_dich.json",
        "translate_budget.json",
        "content_review_flags.json",
        "voice.mp3",
    ):
        p = work / name
        if p.exists():
            p.unlink()
    fixed = work / "segments_vi_fixed.json"
    if not fixed.exists():
        bak = work / "segments_vi_fixed.json.bak"
        if bak.exists():
            bak.rename(fixed)
    seg_dir = work / "seg_tts"
    if seg_dir.exists():
        shutil.rmtree(seg_dir)
    seg_dir.mkdir(parents=True, exist_ok=True)


def _run_from_translate(work: Path, cfg: dict) -> list[dict]:
    from dyflow.voiceover import auto_script, synthesize_segments  # noqa: WPS433

    video = work / "edited.mp4"
    if not video.exists():
        video = next((work / "raw").glob("*.mp4"), None)
    if not video or not video.exists():
        raise FileNotFoundError(f"Khong tim thay video trong {work}")

    auto_cfg = dict(cfg.get("voiceover", {}).get("auto", {}))
    auto_cfg.setdefault("translate_to", "vi")
    auto_cfg.setdefault("translate_from", "zh-CN")
    _, segments = auto_script(video, work, auto_cfg)

    vo_cfg = {
        **cfg.get("voiceover", {}),
        "tts_engine": "edge",
        "refresh_tts": True,
        "dub_auto_shorten": True,
        "match_original_prosody": True,
    }
    synthesize_segments(
        segments,
        work / "seg_tts",
        work / "voice.mp3",
        vo_cfg,
        video_dur=probe_duration(video),
        source_video=video,
    )
    return json.loads((work / "segments_qa.json").read_text(encoding="utf-8"))


def _voice_cfg(work: Path, cfg: dict) -> dict:
    cfg_path = work / "voice_cfg.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text())
    return {
        **cfg.get("voiceover", {}),
        "voice": cfg.get("voiceover", {}).get("voice", "vi-VN-NamMinhNeural"),
        "tts_engine": "edge",
        "refresh_tts": True,
        "dub_auto_shorten": True,
        "dub_chars_per_sec": cfg.get("voiceover", {}).get("auto", {}).get("dub_chars_per_sec", 12),
    }


def analyze_work_dir(
    work: Path,
    *,
    resynth: bool = False,
    from_translate: bool = False,
    from_goc: bool = False,
    cfg: dict | None = None,
) -> int:
    work = Path(work)
    cfg = cfg or load_config(str(ROOT / "config.yaml"))
    seg_dir = work / "seg_tts"
    qa_path = work / "segments_qa.json"

    if from_goc:
        _clear_dub_cache(work)
        import subprocess
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/prepare_goc_segments.py"), str(work)],
            check=True,
        )
        from_goc = False
        resynth = True

    if from_translate:
        _clear_dub_cache(work)
        qa_rows = _run_from_translate(work, cfg)
    elif resynth:
        segs = _load_segments(work, fresh=True)
        if not segs:
            print("Khong co segments de resynth (can segments_vi.json)")
            return 2
        video = work / "edited.mp4"
        from dyflow.voiceover import synthesize_segments  # noqa: WPS433

        synthesize_segments(
            segs, seg_dir, work / "voice.mp3", _voice_cfg(work, cfg),
            video_dur=probe_duration(video) if video.exists() else None,
            source_video=video if video.exists() else None,
        )
        qa_rows = json.loads(qa_path.read_text(encoding="utf-8"))
    else:
        qa_rows = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else []

    if not qa_rows:
        print(f"Khong tim thay segments_qa.json trong {work}")
        return 2

    hdr = (
        f"{'#':>2} {'start':>6} {'slot':>6} {'post':>6} {'play':>6} "
        f"{'drift':>6} {'file%':>6} {'cum_ms':>7} {'atempo':>6} {'vol':>6} flags"
    )
    print(hdr)
    print("-" * len(hdr))

    fails = 0
    n = len(qa_rows)
    for i, r in enumerate(qa_rows):
        slot = float(r.get("slot", 0))
        post = float(r.get("post_dur", 0))
        play = float(r.get("play", post))
        atempo = float(r.get("atempo", 1.0))
        mean_db = r.get("mean_db")
        cum_ms = float(r.get("cum_offset_ms", 0))
        flags = list(r.get("flags") or [])
        start = float(r.get("start", 0))
        drift_pct = float(r.get("drift_pct", abs(play - slot) / slot * 100 if slot else 0))
        drift_file = float(r.get("drift_file_pct", abs(post - slot) / slot * 100 if slot else 0))

        drift_ms = abs(play - slot) * 1000
        row_fail = []
        if "qua_dai" in flags:
            row_fail.append("shorten")
        elif "qua_ngan" in flags:
            row_fail.append("mapping")
        elif drift_ms > TIMING_WARN_MS:
            row_fail.append("timing")
        if abs(cum_ms) > CUMULATIVE_WARN_MS:
            row_fail.append("cum")
        if mean_db is not None and abs(float(mean_db) - (-16)) > VOLUME_WARN_DB:
            row_fail.append("vol")
        if row_fail:
            fails += 1

        vol_s = f"{float(mean_db):.1f}" if mean_db is not None else "?"
        print(
            f"{i:2d} {start:6.1f} {slot:6.2f} {post:6.2f} {play:6.2f} "
            f"{drift_pct:5.1f}% {drift_file:5.1f}% {cum_ms:7.0f} {atempo:6.3f} {vol_s:>6} "
            f"{','.join(sorted(set(flags))) or 'ok'}"
        )

    print("-" * len(hdr))
    stats = qa_summary_stats(qa_rows)
    slots = sum(float(r.get("slot", 0)) for r in qa_rows)
    plays = sum(float(r.get("play", 0)) for r in qa_rows)
    cov = plays / slots * 100 if slots else 0
    print(
        f"Tong: slot={slots:.1f}s play={plays:.1f}s coverage={cov:.0f}% "
        f"atempo={stats['atempo_applied']}/{n} flagged={stats['flagged_segments']} "
        f"cum={stats['final_cum_offset_ms']:.0f}ms loi={fails}/{n}"
    )

    compare_path = work / "dub_fix_compare.json"
    if compare_path.exists():
        cmp = json.loads(compare_path.read_text(encoding="utf-8"))
        d = cmp.get("delta", {})
        print(
            f"Compare vs baseline: atempo {d.get('atempo_applied', '?'):+d}, "
            f"cum_offset {d.get('final_cum_offset_ms', '?'):+.0f}ms, "
            f"dead_zone_ok_gia {d.get('dead_zone_ok_gia', '?'):+d}"
        )

    final = work / "dub_final_report.json"
    if final.exists():
        rep = json.loads(final.read_text(encoding="utf-8"))
        print(f"Final report: {rep.get('summary', '')}")

    shorten = work / "can_rut_gon_cau_dich.json"
    qua_dai_qa = sum(1 for r in qa_rows if "qua_dai" in (r.get("flags") or []))
    qua_ngan_qa = sum(1 for r in qa_rows if "qua_ngan" in (r.get("flags") or []))
    qua_dai_file = 0
    if shorten.exists():
        sf = json.loads(shorten.read_text(encoding="utf-8"))
        qua_dai_file = len(sf)
        ids_qa = sorted(r["i"] for r in qa_rows if "qua_dai" in (r.get("flags") or []))
        ids_file = sorted(x["i"] for x in sf)
        ids_ngan = sorted(r["i"] for r in qa_rows if "qua_ngan" in (r.get("flags") or []))
        print(
            f"Qua dai: qa={qua_dai_qa} file={qua_dai_file} "
            f"ids_qa={ids_qa} ids_file={ids_file}"
        )
        if qua_dai_qa != qua_dai_file:
            print("CANH BAO: dem qua_dai qa vs file KHONG KHOP")
    elif qua_dai_qa:
        print(f"Qua dai: qa={qua_dai_qa} (khong co file)")
    if qua_ngan_qa:
        ids_ngan = sorted(r["i"] for r in qa_rows if "qua_ngan" in (r.get("flags") or []))
        print(f"Qua ngan (mapping?): qa={qua_ngan_qa} ids={ids_ngan}")

    return 1 if fails else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Test dub quality metrics")
    p.add_argument("work_dir", help="Thu muc work/<job_id>")
    p.add_argument("--resynth", action="store_true", help="Chay lai synthesize_segments")
    p.add_argument(
        "--from-translate",
        action="store_true",
        help="Xoa cache dich/TTS, chay lai auto_script + synthesize",
    )
    p.add_argument("--fail", action="store_true", help="Exit 1 neu co loi")
    p.add_argument(
        "--from-goc",
        action="store_true",
        help="Whisper zh timing + Google dich (chua shorten) -> TTS",
    )
    args = p.parse_args()
    code = analyze_work_dir(
        Path(args.work_dir),
        resynth=args.resynth,
        from_translate=args.from_translate,
        from_goc=args.from_goc,
    )
    if args.fail:
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
