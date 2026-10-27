"""Stress: chung _tts_parts vs unique — do race khi 4 luong ghi song song.

Chay:
  .\\.venv\\Scripts\\python.exe scripts\\stress_tts_temp_race.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _write_fake_mp3(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Minimal-ish MP3 frame padding so size > 100
    path.write_bytes(b"\xff\xfb\x90\x00" + payload + b"\x00" * 200)


def race_shared_parts(rounds: int = 40, workers: int = 4) -> dict:
    """Mo phong bug cu: moi luong ghi cung _tts_parts/part_000.mp3."""
    bad = 0
    with tempfile.TemporaryDirectory(prefix="tts_shared_") as td:
        base = Path(td)
        shared = base / "_tts_parts"
        shared.mkdir()

        def one(i: int) -> bool:
            # Ghi shared part roi "doc" thanh out — neu bi de, payload lech.
            marker = f"SEG{i:04d}-{time.time_ns()}".encode()
            part = shared / "part_000.mp3"
            out = base / f"seg_{i:04d}.mp3"
            # Truncate + write (khong atomic) — dung pattern bug.
            with open(part, "wb") as f:
                f.write(b"\xff\xfb\x90\x00" + marker + b"\x00" * 200)
                f.flush()
            time.sleep(0.002)  # cua so race
            data = part.read_bytes()
            out.write_bytes(data)
            return marker in data

        for _ in range(rounds):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(one, i) for i in range(workers * 3)]
                for fut in as_completed(futs):
                    if not fut.result():
                        bad += 1
    return {"mode": "shared_parts", "rounds": rounds, "bad": bad, "ok": rounds * workers * 3 - bad}


def race_unique_parts(rounds: int = 40, workers: int = 4) -> dict:
    from dyflow.tts_file_io import atomic_replace, unique_tmp

    bad = 0
    with tempfile.TemporaryDirectory(prefix="tts_unique_") as td:
        base = Path(td)

        def one(i: int) -> bool:
            marker = f"SEG{i:04d}-{time.time_ns()}".encode()
            out = base / f"seg_{i:04d}.mp3"
            # Moi luong/doan: thu muc part rieng (nhu fix _synthesize_split).
            parts = base / f"_tts_parts_seg_{i:04d}_{os.getpid()}_{i}"
            parts.mkdir(exist_ok=True)
            part = parts / "part_000.mp3"
            tmp = unique_tmp(out, tag="stress")
            try:
                _write_fake_mp3(part, marker)
                time.sleep(0.002)
                data = part.read_bytes()
                tmp.write_bytes(data)
                atomic_replace(tmp, out)
                return marker in out.read_bytes()
            finally:
                tmp.unlink(missing_ok=True)
                shutil.rmtree(parts, ignore_errors=True)

        for _ in range(rounds):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(one, i) for i in range(workers * 3)]
                for fut in as_completed(futs):
                    if not fut.result():
                        bad += 1
    total = rounds * workers * 3
    return {"mode": "unique_parts", "rounds": rounds, "bad": bad, "ok": total - bad}


def main() -> None:
    print("=== TTS temp race stress ===")
    shared = race_shared_parts()
    unique = race_unique_parts()
    print(shared)
    print(unique)
    rate_shared = shared["bad"] / max(1, shared["bad"] + shared["ok"])
    rate_unique = unique["bad"] / max(1, unique["bad"] + unique["ok"])
    print(f"race_rate shared={rate_shared:.1%} unique={rate_unique:.1%}")
    if unique["bad"] > 0:
        raise SystemExit("FAIL: unique mode van race")
    if shared["bad"] == 0:
        print("WARN: shared khong bat duoc race lan nay (timing) — van la bug thiet ke")
    print("OK")


if __name__ == "__main__":
    main()
