#!/usr/bin/env python3
"""Tao mau audio 1.00 / 1.15 / 1.18 / 1.20x de chot TTS_ATEMPO_SEVERE_MAX.

  python scripts/make_atempo_ear_samples.py
  python scripts/make_atempo_ear_samples.py --speakers boy aunt
  python scripts/make_atempo_ear_samples.py --speakers dad boy aunt
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_tts import apply_atempo_file  # noqa: E402
from dyflow.utils import probe_duration  # noqa: E402
from dyflow.voices import tts_fields  # noqa: E402
from dyflow.voiceover import _synthesize_save  # noqa: E402

RATES = (1.15, 1.18, 1.20)
OUT_ROOT = ROOT / "work" / "atempo_ear_check"

# Dump web_49e073e3 khong luu tren may nay — dung cau overflow cung tong
# (i=3 cha da nghe; i=4 boy / i=14 aunt theo chi so user). Do dai ~6-9s TTS.
SPEAKERS: dict[str, dict] = {
    "dad": {
        "voice_id": "male_mature",
        "job_i": 3,
        "subdir": "",
        "text": (
            "Cha rất đau lòng khi thấy con như vậy. Không phải cha không yêu con, "
            "mà cha lo con sẽ khổ cả đời nếu không chịu thay đổi cách sống."
        ),
    },
    "boy": {
        "voice_id": "male_child",
        "job_i": 4,
        "subdir": "boy",
        "text": (
            "Con không cố ý làm cha buồn. Con chỉ sợ bạn bè cười con, sợ không ai "
            "chơi với con nữa, nên con mới nói dối và giấu chuyện đó từ hôm qua đến giờ."
        ),
    },
    "aunt": {
        "voice_id": "female_adult",
        "job_i": 14,
        "subdir": "aunt",
        "text": (
            "Cháu nghe cô nói này. Không phải cô không thương cháu, nhưng nếu cháu "
            "cứ tiếp tục như vậy thì cha cháu đã đau lòng lắm rồi, cả nhà cũng mệt."
        ),
    },
}


async def _tts(text: str, path: Path, cfg: dict) -> None:
    await _synthesize_save(text, path, cfg)


def _one_speaker(name: str) -> Path:
    spec = SPEAKERS[name]
    fields = tts_fields("vi", spec["voice_id"])
    cfg = {
        "voice": fields["tts_voice"],
        "rate": fields["speaker_rate"],
        "pitch": fields["speaker_pitch"],
        "volume": fields["speaker_volume"],
    }
    out_dir = OUT_ROOT / spec["subdir"] if spec["subdir"] else OUT_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    text = spec["text"]
    goc = out_dir / "01_atempo_1.00x_goc.mp3"
    print(
        f"[{name} i={spec['job_i']}] {fields['tts_voice']} "
        f"pitch={cfg['pitch']} rate={cfg['rate']}"
    )
    asyncio.run(_tts(text, goc, cfg))
    if not goc.exists() or goc.stat().st_size < 200:
        raise SystemExit(f"FAIL TTS {name}: khong tao file goc")
    goc_dur = probe_duration(goc)
    notes = [
        f"Giong {name} (job web_49e073e3 i={spec['job_i']}, voice_id={spec['voice_id']}).",
        f"Cau: {text}",
        f"Edge: {fields['tts_voice']} pitch={cfg['pitch']} rate={cfg['rate']}",
        f"Goc 1.00x duration={goc_dur:.3f}s",
        "Tran he thong: TTS_ATEMPO_SEVERE_MAX = 1.20 (khong tach theo giong, tru khi mau nay vo ly).",
        "",
        f"01_atempo_1.00x_goc.mp3  dur={goc_dur:.3f}s",
    ]
    print(f"  goc {goc.name} {goc_dur:.3f}s")
    for i, rate in enumerate(RATES, start=2):
        dest = out_dir / f"{i:02d}_atempo_{rate:.2f}x.mp3"
        shutil.copy2(goc, dest)
        apply_atempo_file(dest, rate)
        dur = probe_duration(dest)
        notes.append(
            f"{dest.name}  dur={dur:.3f}s  (expect ~{goc_dur / rate:.3f}s)"
        )
        print(f"  {dest.name} {dur:.3f}s")
    (out_dir / "LISTEN.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    return out_dir


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--speakers",
        nargs="+",
        default=["boy", "aunt"],
        choices=list(SPEAKERS),
    )
    args = p.parse_args()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for name in args.speakers:
        _one_speaker(name)
    print(f"Xong: {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
