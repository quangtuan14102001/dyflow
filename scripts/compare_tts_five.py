#!/usr/bin/env python3
"""5 mau TTS cung 1 doan loi: EL Flash, EL Multi, OpenAI tts-1, tts-1-hd, gpt-4o-mini-tts + Edge."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "voice_compare" / "five"

TEXT = (
    "Cục trĩ lòi ra, nhét mãi không vào. Bạn ngồi lâu, mê đồ cay — "
    "đừng chịu đau nữa. Gel Hoa Hướng Dương này thấm nhanh, không nhầy. "
    "Bôi mỏng buổi sáng tối là được."
)

# Rachel — nu, multilingual (goi tra phi). Free tier se tu chon voice hop le.
EL_VOICE = "21m00Tcm4TlvDq8ikWAM"


def _el_key() -> str:
    return os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY") or ""


def _el_pick_voice() -> str:
    """Uu tien Rachel; neu 402 library voice thi lay voice dau tien tai khoan duoc dung."""
    key = _el_key()
    if not key:
        return EL_VOICE
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            voices = json.loads(resp.read().decode()).get("voices") or []
            print("eleven voices available", len(voices))
            for v in voices[:8]:
                print(" ", v.get("category"), v.get("name"), v.get("voice_id"))
    except Exception as e:  # noqa: BLE001
        print("eleven list voices fail", type(e).__name__, e)
        return EL_VOICE
    if not voices:
        return EL_VOICE
    for v in voices:
        if v.get("voice_id") == EL_VOICE:
            return EL_VOICE
    # free: thuong chi dung duoc default / cloned, khong dung library
    for cat in ("cloned", "generated", "professional", "premade", "default"):
        for v in voices:
            if (v.get("category") or "").lower() == cat:
                print("eleven voice", v.get("name"), v.get("voice_id"), v.get("category"))
                return v["voice_id"]
    print("eleven voice", voices[0].get("name"), voices[0].get("voice_id"), voices[0].get("category"))
    return voices[0]["voice_id"]


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _write(name: str, data: bytes) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_bytes(data)
    print("ok", name, len(data))
    return p


def _note(name: str, msg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(msg.strip() + "\n", encoding="utf-8")
    print("fail", name, msg[:120])


async def edge() -> None:
    import edge_tts

    dest = OUT / "00_edge_hoaimy.mp3"
    try:
        c = edge_tts.Communicate(TEXT, "vi-VN-HoaiMyNeural", rate="+0%", pitch="+1Hz")
        await asyncio.wait_for(c.save(str(dest)), timeout=40)
        print("ok", dest.name, dest.stat().st_size)
    except Exception as e:  # noqa: BLE001
        _note("00_edge.txt", str(e))


def eleven(model_id: str, fname: str, voice_id: str) -> None:
    key = _el_key()
    if not key:
        _note(fname.replace(".mp3", ".txt"), "thieu ELEVENLABS_API_KEY")
        return
    body = json.dumps({
        "text": TEXT,
        "model_id": model_id,
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.75, "style": 0.45, "use_speaker_boost": True},
    }).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        data=body,
        headers={
            "xi-api-key": key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            _write(fname, resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:400]
        _note(fname.replace(".mp3", ".txt"), f"HTTP {e.code}: {err}")
    except Exception as e:  # noqa: BLE001
        _note(fname.replace(".mp3", ".txt"), str(e))


def openai_tts(model: str, fname: str, *, instructions: str | None = None) -> None:
    key = os.environ.get("OPENAI_API_KEY") or ""
    if not key:
        _note(fname.replace(".mp3", ".txt"), "thieu OPENAI_API_KEY")
        return
    from openai import OpenAI

    client = OpenAI(api_key=key)
    kwargs: dict = {"model": model, "voice": "nova", "input": TEXT, "response_format": "mp3"}
    if instructions:
        kwargs["instructions"] = instructions
    try:
        r = client.audio.speech.create(**kwargs)
        data = r.content if hasattr(r, "content") else bytes(r)
        _write(fname, data)
    except Exception as e:  # noqa: BLE001
        _note(fname.replace(".mp3", ".txt"), str(e))


def write_index() -> None:
    files = [
        ("00_edge_hoaimy.mp3", "0. Edge TTS Hoài My (hiện tại web)"),
        ("01_eleven_flash.mp3", "1. ElevenLabs Flash"),
        ("02_eleven_multilingual.mp3", "2. ElevenLabs Multilingual v2"),
        ("03_openai_tts1.mp3", "3. OpenAI tts-1"),
        ("04_openai_tts1_hd.mp3", "4. OpenAI tts-1-hd"),
        ("05_openai_gpt4o_mini.mp3", "5. OpenAI gpt-4o-mini-tts"),
    ]
    rows = []
    for fname, label in files:
        p = OUT / fname
        note = OUT / fname.replace(".mp3", ".txt")
        if p.exists() and p.stat().st_size > 200:
            rows.append(
                f"<section><h2>{label}</h2><audio controls src='{fname}'></audio></section>"
            )
        else:
            msg = note.read_text(encoding="utf-8") if note.exists() else "chua tao"
            rows.append(f"<section><h2>{label}</h2><p class='err'>{msg}</p></section>")
    html = f"""<!doctype html>
<meta charset="utf-8">
<title>So sanh 5 TTS</title>
<style>
body{{font-family:sans-serif;max-width:720px;margin:24px auto;padding:0 16px;background:#111;color:#eee}}
h1{{font-size:20px}} p{{color:#bbb}}
section{{margin:18px 0;padding:12px;background:#1c1c1c;border-radius:10px}}
audio{{width:100%}} .err{{color:#f88;white-space:pre-wrap}}
</style>
<h1>Cùng 1 đoạn lời — nghe từng giọng</h1>
<p>{TEXT}</p>
{''.join(rows)}
"""
    (OUT / "index.html").write_text(html, encoding="utf-8")
    print("index", OUT / "index.html")


def main() -> None:
    _load_env()
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(edge())
    el_voice = _el_pick_voice()
    print("using eleven voice", el_voice)
    eleven("eleven_flash_v2_5", "01_eleven_flash.mp3", el_voice)
    eleven("eleven_multilingual_v2", "02_eleven_multilingual.mp3", el_voice)
    openai_tts("tts-1", "03_openai_tts1.mp3")
    openai_tts("tts-1-hd", "04_openai_tts1_hd.mp3")
    openai_tts(
        "gpt-4o-mini-tts",
        "05_openai_gpt4o_mini.mp3",
        instructions=(
            "Speak Vietnamese. Energetic female sales voice: urgent, caring, "
            "emphasize pain and the product name. Not monotone."
        ),
    )
    write_index()


if __name__ == "__main__":
    main()
