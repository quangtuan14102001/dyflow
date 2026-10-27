#!/usr/bin/env python3
"""Tao mau TTS de so sanh (cung 1 doan loi)."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "voice_compare"
MODELS = ROOT / "work" / "tts_models"
TEXT = (
    "Xin chào. Đây là mẫu thử giọng VideosCreator. "
    "Hôm nay trời đẹp, mình đi dạo công viên một chút rồi về nhà pha trà."
)

PIPER_ONNX = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium.onnx"
)
PIPER_JSON = PIPER_ONNX + ".json"


def wav_to_mp3(wav: Path, mp3: Path) -> None:
    subprocess.check_call([
        "ffmpeg", "-y", "-i", str(wav),
        "-c:a", "libmp3lame", "-b:a", "192k", str(mp3),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_note(name: str, body: str) -> None:
    (OUT / name).write_text(body.strip() + "\n", encoding="utf-8")


async def edge(voice: str, dest: Path, rate: str = "+10%") -> None:
    import edge_tts
    last = None
    for kwargs in (
        {"rate": rate},
        {"rate": "+0%"},
        {"rate": "+0%", "pitch": "+0Hz"},
    ):
        try:
            tmp = dest.with_suffix(".tmp.mp3")
            c = edge_tts.Communicate(TEXT, voice, **kwargs)
            await asyncio.wait_for(c.save(str(tmp)), timeout=28)
            if tmp.exists() and tmp.stat().st_size > 800:
                tmp.replace(dest)
                return
        except Exception as e:
            last = e
            await asyncio.sleep(1.2)
    raise RuntimeError(last)


def gtts(dest: Path) -> None:
    from gtts import gTTS
    gTTS(TEXT, lang="vi").save(str(dest))


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        return
    print("download", dest.name)
    urllib.request.urlretrieve(url, dest)


def piper(dest: Path) -> None:
    import wave

    from piper.voice import PiperVoice

    onnx = MODELS / "vi_VN-vais1000-medium.onnx"
    cfg = MODELS / "vi_VN-vais1000-medium.onnx.json"
    download(PIPER_ONNX, onnx)
    download(PIPER_JSON, cfg)
    voice = PiperVoice.load(str(onnx), config_path=str(cfg))
    wav = dest.with_suffix(".wav")
    chunks = list(voice.synthesize(TEXT))
    if not chunks:
        raise RuntimeError("piper khong tao audio")
    c0 = chunks[0]
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(c0.sample_channels)
        wf.setsampwidth(c0.sample_width)
        wf.setframerate(c0.sample_rate)
        for ch in chunks:
            wf.writeframes(ch.audio_int16_bytes)
    wav_to_mp3(wav, dest)
    wav.unlink(missing_ok=True)


def try_openai(dest: Path) -> str | None:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
    if not key:
        return "thieu OPENAI_API_KEY"
    from openai import OpenAI
    client = OpenAI(api_key=key)
    # alloy ~ nu, onyx ~ nam
    for voice, name in (("onyx", dest.with_name("07_openai_onyx_nam.mp3")),
                        ("nova", dest.with_name("08_openai_nova_nu.mp3"))):
        r = client.audio.speech.create(model="gpt-4o-mini-tts", voice=voice, input=TEXT)
        name.write_bytes(r.content)
    return None


def try_eleven(dest: Path) -> str | None:
    key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY")
    if not key:
        return "thieu ELEVENLABS_API_KEY"
    import urllib.request
    import json
    # default multilingual voices
    voices = [
        ("21m00Tcm4TlvDq8ikWAM", "09_elevenlabs_rachel_nu.mp3"),
        ("pNInz6obpgDQGcFmaJgB", "10_elevenlabs_adam_nam.mp3"),
    ]
    for vid, fname in voices:
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
            data=json.dumps({"text": TEXT, "model_id": "eleven_multilingual_v2"}).encode(),
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            (OUT / fname).write_bytes(resp.read())
    return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "_loi_mau.txt").write_text(TEXT + "\n", encoding="utf-8")
    (OUT / "_loi_mau.txt").write_text(TEXT + "\n", encoding="utf-8")

    jobs = [
        ("01_edge_nam_minh.mp3", lambda: asyncio.run(edge("vi-VN-NamMinhNeural", OUT / "01_edge_nam_minh.mp3"))),
        ("02_edge_hoai_my.mp3", lambda: asyncio.run(edge("vi-VN-HoaiMyNeural", OUT / "02_edge_hoai_my.mp3"))),
        ("03_gtts_google_mien_phi.mp3", lambda: gtts(OUT / "03_gtts_google_mien_phi.mp3")),
        ("04_piper_vais1000_local.mp3", lambda: piper(OUT / "04_piper_vais1000_local.mp3")),
    ]
    for name, fn in jobs:
        print("run", name)
        try:
            fn()
            print("ok", name)
        except Exception as e:
            write_note(name.replace(".mp3", "_FAIL.txt"), str(e))
            print("fail", name, e)

    err = try_openai(OUT / "07_openai.mp3")
    write_note(
        "07_08_openai.txt",
        err or "Da tao file OpenAI.",
    )
    err = try_eleven(OUT / "09_eleven.mp3")
    write_note(
        "09_10_elevenlabs.txt",
        err or "Da tao file ElevenLabs.",
    )
    write_note(
        "11_google_cloud.txt",
        "Can GOOGLE_APPLICATION_CREDENTIALS (file JSON service account). "
        "Khong co key trong .env — chua chay duoc. Ban mien phi gan nhat la 03_gtts.",
    )
    write_note(
        "README.txt",
        "Cung mot doan loi (xem _loi_mau.txt). Nghe lan luot file mp3 so 01-04.\n"
        "OpenAI / ElevenLabs / Google Cloud: chua co API key nen chua xuat audio.",
    )
    print("DONE", OUT)


if __name__ == "__main__":
    main()
