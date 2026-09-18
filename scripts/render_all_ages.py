#!/usr/bin/env python3
"""Xuat 1 clip Douyin: nhan dien nhan vat, gan giong dung gioi tinh/tuoi.

Tai + Whisper + dich 1 lan; TTS mot lan voi nhieu giong trong CUNG file.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.stdlib_fix import ensure_thread_stdlib  # noqa: E402

ensure_thread_stdlib()

from dyflow import compose, downloader, speakers, transform, voiceover  # noqa: E402
from dyflow.pipeline import normalize_douyin_url  # noqa: E402
from dyflow.utils import (  # noqa: E402
    deep_merge,
    ensure_dirs,
    load_config,
    log,
    probe_duration,
    probe_has_audio,
    setup_logging,
)
from dyflow.voices import resolve_preset  # noqa: E402

URL = (
    "https://www.douyin.com/search/AI?aid=a3d64435-bf5f-4912-b32e-5e0ac228ff4d"
    "&modal_id=7668684275889360178&type=general"
)
JOB = "ai_vi_ages"
OUT_NAME = "ai_douyin_vi"


def _vo_cfg(cfg: dict, preset: dict) -> dict:
    vo = dict(cfg.get("voiceover") or {})
    vo.update({
        "audio_mode": "dub",
        "voice": preset["voice"],
        "voice_id": preset["id"],
        "rate": preset["rate"],
        "pitch": preset["pitch"],
        "volume": preset["volume"],
        "openai_voice": preset.get("openai_voice") or "",
        "tts_engine": "edge",
        "refresh_tts": True,
        "skip_failed_segments": True,
        "match_original_prosody": True,
        "max_atempo_stretch": 1.08,
        "mute_original": False,
        "mute_original_speech": True,
        "strip_original_vocals": True,
        "preserve_fx": True,
        "dub_qa_fail_closed": False,
    })
    auto = dict(vo.get("auto") or {})
    auto.update({
        "translate_to": preset["translate_to"],
        "translate_from": "zh-CN",
        "source_lang": "zh",
        "translate_llm": "budgeted",
        "energy_check": True,
        "content_review": False,
        "cast_speakers": True,
    })
    vo["auto"] = auto
    return vo


def main() -> int:
    setup_logging()
    cfg = load_config(ROOT / "config.yaml")
    source = normalize_douyin_url(URL)
    paths = cfg["paths"]
    work = Path(paths["work_dir"]) / JOB
    raw_dir = work / "raw"
    ensure_dirs(work, raw_dir, paths["output_dir"])
    log.info("Clip: %s", source)

    raw = raw_dir / "source.mp4"
    if not (raw.exists() and probe_has_audio(raw)):
        raw = downloader.download_one(source, raw_dir, dict(cfg.get("download") or {}))
    if not probe_has_audio(raw):
        raise RuntimeError("Video khong co audio")
    video_dur = probe_duration(raw)

    edited = work / "edited.mp4"
    if cfg.get("transform", {}).get("enabled", True):
        if not (edited.exists() and edited.stat().st_size > 10_000):
            transform.transform(raw, edited, cfg["transform"])
    else:
        edited = raw

    segs_path = work / "segments_vi.json"
    script_path = work / "script_auto_vi.txt"
    csv_path = work / "translate_report.csv"
    preset = resolve_preset("vi", "male_adult")
    vo = _vo_cfg(cfg, preset)
    if segs_path.exists() and script_path.exists():
        segments = json.loads(segs_path.read_text(encoding="utf-8"))
        script = script_path.read_text(encoding="utf-8")
        log.info("Tai su dung %d doan da dich", len(segments))
    elif csv_path.exists():
        from dyflow.transcribe import _has_cjk, dedupe_segments, merge_segments
        from dyflow.translate_llm import maybe_rewrite
        import csv

        rows = []
        with csv_path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({
                    "start": float(r["start"]),
                    "end": float(r["end"]),
                    "text": r.get("translated_text") or "",
                    "source_text": r.get("source_text") or "",
                })
        segments = merge_segments(
            dedupe_segments(rows),
            max_gap=0.35, max_dur=3.2, max_chars=52,
        )
        segments = maybe_rewrite(segments, vo.get("auto", {}))
        for s in segments:
            if not _has_cjk(s.get("text") or ""):
                continue
            src = (s.get("source_text") or s.get("text") or "").strip()
            try:
                from deep_translator import GoogleTranslator
                s["text"] = GoogleTranslator(source="zh-CN", target="vi").translate(src)
            except Exception as e:  # noqa: BLE001
                log.warning("Leftover Google fail: %s", e)
        segments = [
            s for s in segments
            if (s.get("text") or "").strip() and not _has_cjk(s.get("text") or "")
        ]
        script = " ".join(s["text"] for s in segments)
        segs_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        script_path.write_text(script, encoding="utf-8")
        log.info("Khoi phuc %d doan tu translate_report.csv", len(segments))
    else:
        script, segments = voiceover.auto_script(raw, work, vo.get("auto", {}))
        log.info("Dich xong %d doan", len(segments))

    segments = speakers.assign_cast(
        segments,
        source_video=raw,
        work_dir=work,
        lang="vi",
        fallback_voice="male_adult",
        fallback_cfg=vo,
    )

    voice_dir = work / "cast"
    if voice_dir.exists():
        shutil.rmtree(voice_dir)
    voice_dir.mkdir(parents=True)
    voice_mp3 = voice_dir / "voice.mp3"
    voice_file, cues = voiceover.synthesize_segments(
        segments,
        voice_dir / "seg_tts",
        voice_mp3,
        vo,
        video_dur=video_dur,
        source_video=raw,
    )
    jcfg = deep_merge(cfg, {"voiceover": vo, "caption": {
        **cfg.get("caption", {}),
        "chars_per_line": preset["chars_per_line"],
    }})
    jcfg["paths"] = paths
    outputs = compose.compose(
        video=edited,
        voice=voice_file,
        script=script,
        out_dir=paths["output_dir"],
        base_name=OUT_NAME,
        cfg=jcfg,
        music=None,
        segment_cues=cues,
        sync_to_video=True,
    )
    print("\nDone.")
    for p in outputs:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
