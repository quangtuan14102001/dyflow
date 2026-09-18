#!/usr/bin/env python3
"""Kiem tra 4 speedup bang source (khong import torch/whisper/numba).

  python scripts/test_pipeline_speedups.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK  {name}")
        return
    print(f"  FAIL {name} {detail}")
    raise SystemExit(1)


def main() -> int:
    main_py = (ROOT / "dyflow/main.py").read_text()
    compose = (ROOT / "dyflow/compose.py").read_text()
    afx = (ROOT / "dyflow/audio_fx.py").read_text()
    vo = (ROOT / "dyflow/voiceover.py").read_text()
    tr = (ROOT / "dyflow/transcribe.py").read_text()
    spk = (ROOT / "dyflow/speakers.py").read_text()
    wk = (ROOT / "dyflow/web/worker.py").read_text()

    print("=== 1. Demucs input ===")
    _ok("make_ambient_bed trich audio tu video", "extract_wav(video" in afx)
    _ok("compose Demucs nhan video, khong voice.mp3", "video, work_audio" in compose)
    _ok("voice chi la dieu kien co dub", "if voice and not cfg_vo.get" in compose)
    _ok("main start Demucs sau Download", "Demucs started (parallel)" in main_py)
    _ok("join truoc mix", 'timing_record("Demucs"' in main_py)
    _ok("truyen ambient_bed", "ambient_bed=ambient_bed" in main_py)

    print("=== 2. pyin warmup ===")
    _ok("warmup_pyin defined", "def warmup_pyin" in spk)
    _ok("librosa.pyin tren audio gia", "librosa.pyin(y" in spk)
    _ok("worker goi truoc job dau", "warmup_pyin()" in wk)

    print("=== 3. Whisper cache ===")
    _ok("cache HIT log", "Whisper model cache HIT" in tr)
    _ok("chi load khi miss", 'if key not in _MODEL_CACHE' in tr)
    _ok("khong clear cache giua job", "_MODEL_CACHE.clear" not in tr)

    print("=== 4+5. TTS prefetch + rate limit ===")
    _ok("prefetch truoc overflow loop", "_prefetch_tts_clips" in vo)
    _ok("concurrency default 4", "_TTS_PREFETCH_CONCURRENCY = 4" in vo)
    _ok("cap max 5", "min(n, 5)" in vo)
    _ok("retry delay 3.5s", "_TTS_RETRY_DELAY = 3.5" in vo)
    _ok("openai chars lock", "_openai_chars_lock" in vo)

    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
