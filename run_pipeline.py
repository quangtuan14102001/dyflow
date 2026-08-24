#!/usr/bin/env python3
"""Mot lenh chay toan pipeline dub: dich LLM + TTS + mix test + report.

Vi du:
  python run_pipeline.py https://www.douyin.com/video/... --profile casual_story
  python run_pipeline.py ./clip.mp4 --profile health_product_review --lang vi
  python run_pipeline.py ./clip.mp4 --auto-profile -y
  python run_pipeline.py ./clip.mp4 --lang en --profile casual_story
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Patch stdlib truoc moi import OpenAI / edge-tts / yt-dlp
try:
    from dyflow.stdlib_fix import ensure_thread_stdlib
    ensure_thread_stdlib()
except Exception:
    pass

from dyflow.main import process_job  # noqa: E402
from dyflow.pipeline import normalize_douyin_url  # noqa: E402
from dyflow.profiles import (  # noqa: E402
    apply_profile,
    glossary_note,
    list_profiles,
    load_profile,
)
from dyflow.utils import deep_merge, load_config, setup_logging  # noqa: E402
from dyflow.voices import DEFAULT_LANG, resolve_preset  # noqa: E402


def _ask(prompt: str, *, default: str = "") -> str:
    tip = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{tip}: ").strip()
    except EOFError:
        return default
    return val or default


def main() -> int:
    p = argparse.ArgumentParser(description="dyflow: 1 lenh pipeline + pipeline_report.json")
    p.add_argument("source", nargs="?", help="URL Douyin hoac file video local")
    p.add_argument("--profile", default="", help="Ten profile trong profiles/")
    p.add_argument("--auto-profile", action="store_true", help="Tu doan profile sau Whisper")
    p.add_argument("--lang", default=DEFAULT_LANG, help="Ngon ngu dich (vi, en, ja, ...)")
    p.add_argument("--translate-to", default="", help="Override translate_to")
    p.add_argument("--voice", default="male", choices=["male", "female"])
    p.add_argument("--tts-voice", default="", help="Override Edge voice id")
    p.add_argument("--job-id", default="")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--max-duration", type=float, default=0, help="Cat N giay dau (0=full)")
    p.add_argument("--yes", "-y", action="store_true", help="Khong hoi editorial")
    p.add_argument(
        "--skip-transform",
        action="store_true",
        help="Bo OCR/blur/border — edited=raw (khong doi timing audio)",
    )
    p.add_argument(
        "--editorial-only-stop",
        action="store_true",
        help="Dung sau dich+review de ra tay (chua TTS)",
    )
    p.add_argument("--list-profiles", action="store_true")
    args = p.parse_args()

    if args.list_profiles:
        for name in list_profiles():
            print(name)
        return 0

    if not args.source:
        p.error("Can source (URL hoac file)")

    setup_logging()
    cfg = load_config(args.config)
    preset = resolve_preset(args.lang, args.voice)

    source = args.source
    try:
        if "douyin.com" in source or (
            source.startswith("http") and "douyin" in source.lower()
        ):
            source = normalize_douyin_url(source)
    except ValueError:
        pass

    profile_name = (args.profile or "").strip() or None
    auto_detect = bool(args.auto_profile) or profile_name is None
    if profile_name is None and sys.stdin.isatty() and not args.yes:
        known = list_profiles()
        print("Profiles:", ", ".join(known))
        picked = _ask("Chon profile (Enter = tu doan sau Whisper)", default="")
        if picked:
            profile_name = picked
            auto_detect = False

    if profile_name:
        profile = load_profile(profile_name)
        cfg = apply_profile(cfg, profile)
        note = glossary_note(profile)
        if note:
            cfg.setdefault("voiceover", {}).setdefault("auto", {})["glossary_note"] = note

    job_id = args.job_id or f"pipe_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    translate_to = args.translate_to or preset["translate_to"]

    job = {
        "id": job_id,
        "source": source,
        "script": "AUTO",
        "lang": preset["lang"],
        "voiceover": {
            "audio_mode": "dub",
            "voice": args.tts_voice or preset["voice"],
            "rate": preset["rate"],
            "pitch": preset["pitch"],
            "volume": preset["volume"],
            "tts_engine": "edge",
            "refresh_tts": True,
            "skip_failed_segments": True,
            "match_original_prosody": True,
            "max_atempo_stretch": 1.08,
            "mix_test_sec": 15,
            "auto": {
                "translate_to": translate_to,
                "translate_from": "zh-CN",
                "source_lang": "zh",
                "translate_llm": "budgeted",
                "energy_check": True,
                "content_review": True,
                "auto_detect_profile": auto_detect and not profile_name,
            },
        },
        "caption": {
            "chars_per_line": preset.get("chars_per_line", 32),
            "max_lines": 3,
        },
        "export": {"fit": "source", "formats": ["source"]},
    }
    if profile_name:
        prof = load_profile(profile_name)
        for key in ("voiceover", "caption"):
            if key in prof:
                job[key] = deep_merge(job.get(key, {}), prof[key])
        job["voiceover"].setdefault("auto", {})["auto_detect_profile"] = False

    if args.max_duration and args.max_duration > 0:
        job["max_duration_sec"] = args.max_duration
        job["auto_trim"] = True

    if args.skip_transform:
        job["transform"] = {"enabled": False}

    cfg["_pipeline_cli"] = {
        "interactive": sys.stdin.isatty() and not args.yes,
        "auto_accept": bool(args.yes),
        "editorial_only_stop": bool(args.editorial_only_stop),
        "profile": profile_name,
    }

    outputs = process_job(job, cfg)
    work = Path(cfg["paths"]["work_dir"]) / job_id
    report = work / "pipeline_report.json"
    print(f"\nDone. Report: {report}")
    for o in outputs:
        print(f"  output: {o}")
    if report.exists():
        import json
        data = json.loads(report.read_text(encoding="utf-8"))
        for w in data.get("warnings") or []:
            print(f"  warn: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
