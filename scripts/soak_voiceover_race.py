"""Soak voiceover-only tren job cache (do race mix_recovered).

Mac dinh: cli_f8f2_planA_rerun trong AppData VideosCreator (da co segments_vi).
Moi lan ~15-20 phut — chay N lan, dem mix_recovered.

  .\\.venv\\Scripts\\python.exe scripts\\soak_voiceover_race.py --runs 5

BENCHMARK / TOC DO (2026-09-19 soak):
  Cung 1 video, khong doi code: wall_sec dao dong ~350s–870s (~2.5x).
  Khi do tac dong patch toc do (song song shorten_slot, ffmpeg preset, ...):
  chay MOI phien ban it nhat 2–3 lan, lay trung binh hoac min — dung ket
  luan tu 1 lan (nhieu Defender/mang Edge-TTS/LLM). Khong can dieu tra
  nguyen nhan bien dong nay tru khi no chan viec khac.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _default_work() -> Path:
    return Path(os.path.expandvars(r"%LOCALAPPDATA%\VideosCreator\work\cli_f8f2_planA_rerun"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=_default_work())
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", type=Path, default=ROOT / "work" / "soak_voiceover_race.json")
    args = ap.parse_args()

    work: Path = args.work
    segs_path = work / "segments_vi.json"
    if not segs_path.exists():
        raise SystemExit(f"Thieu {segs_path}")

    from dyflow import voiceover
    from dyflow.utils import load_config, probe_duration

    cfg_path = Path(os.path.expandvars(r"%LOCALAPPDATA%\VideosCreator\config.yaml"))
    if not cfg_path.exists():
        cfg_path = ROOT / "config.yaml"
    cfg = load_config(cfg_path)
    vo = dict((cfg.get("voiceover") or {}))
    vo["audio_mode"] = "dub"
    segments = json.loads(segs_path.read_text(encoding="utf-8"))
    raw_dir = work / "raw"
    raw = None
    if raw_dir.exists():
        cands = sorted(
            (p for p in raw_dir.rglob("*") if p.is_file()),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        for p in cands:
            if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} and p.stat().st_size > 1000:
                raw = p
                break
    video_dur = float(probe_duration(raw)) if raw else float(
        max(float(s.get("end") or 0) for s in segments)
    )

    results = []
    for n in range(1, int(args.runs) + 1):
        # Phai dung ten dung "seg_tts" de job_work = parent (work).
        seg_tts = work / "seg_tts"
        if seg_tts.exists():
            shutil.rmtree(seg_tts, ignore_errors=True)
        seg_tts.mkdir(parents=True, exist_ok=True)
        voice_out = work / f"voice_soak_{n}.mp3"
        # Xoa lifecycle/mix cu de dem dung tung lan.
        for name in ("mix_recovery.json", "tts_lifecycle.jsonl"):
            p = work / name
            p.unlink(missing_ok=True)
        t0 = time.perf_counter()
        err = None
        try:
            voiceover.synthesize_segments(
                segments, seg_tts, voice_out, vo,
                video_dur=video_dur, source_video=raw,
            )
        except Exception as e:  # noqa: BLE001
            err = str(e)
        dur = time.perf_counter() - t0
        mix = {}
        mix_path = work / "mix_recovery.json"
        if mix_path.exists():
            try:
                mix = json.loads(mix_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                mix = {}
        qa_path = work / "segments_qa.json"
        race_flags = 0
        if qa_path.exists():
            rows = json.loads(qa_path.read_text(encoding="utf-8"))
            race_flags = sum(
                1 for r in rows
                if "tts_recovered_at_mix" in (r.get("flags") or [])
                or "tts_silence_fallback" in (r.get("flags") or [])
            )
        row = {
            "run": n,
            "wall_sec": round(dur, 1),
            "error": err,
            "mix_recovered_total": int(mix.get("mix_recovered_total") or 0),
            "mix_silence_fallback": int(mix.get("mix_silence_fallback") or 0),
            "qa_race_flags": race_flags,
            "race_occurred": bool(
                (err and "mix" in (err or "").lower())
                or int(mix.get("mix_recovered_total") or 0)
                or race_flags
            ),
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"work": str(work), "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    raced = sum(1 for r in results if r["race_occurred"])
    print(
        f"SOAK DONE runs={len(results)} race_runs={raced} "
        f"rate={raced / max(1, len(results)):.0%}"
    )


if __name__ == "__main__":
    main()
