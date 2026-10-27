#!/usr/bin/env python3
"""Sinh file nghe thu tat ca giong con goi duoc (khong can API key)."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "voice_compare" / "all"
TEXT = (
    "Xin chào. Đây là giọng thử VideosCreator. "
    "Hôm nay trời đẹp, mình đi dạo công viên một chút."
)

VIETTEL = [
    ("hn-quynhanh", "Viettel Nu Bac Quynh Anh"),
    ("hn-phuongtrang", "Viettel Nu Bac Phuong Trang"),
    ("hn-thaochi", "Viettel Nu Bac Thao Chi"),
    ("hn-thanhha", "Viettel Nu Bac Thanh Ha"),
    ("hn-thanhphuong", "Viettel Nu Bac Thanh Phuong"),
    ("hn-thanhtung", "Viettel Nam Bac Thanh Tung"),
    ("hn-tienquan", "Viettel Nam Bac Tien Quan"),
    ("hn-namkhanh", "Viettel Nam Bac Nam Khanh"),
    ("hn-leyen", "Viettel Le Yen"),
    ("hue-maingoc", "Viettel Nu Trung Mai Ngoc"),
    ("hue-baoquoc", "Viettel Nam Trung Bao Quoc"),
    ("hcm-diemmy", "Viettel Nu Nam Diem My"),
    ("hcm-thuydung", "Viettel Nu Nam Thuy Dung"),
    ("hcm-thuyduyen", "Viettel Nu Nam Thuy Duyen"),
    ("hcm-phuongly", "Viettel Nu Nam Phuong Ly"),
    ("hcm-minhquan", "Viettel Nam Nam Minh Quan"),
    ("hn-viettrinh", "Viettel Nu Bac Viet Trinh"),
    ("hn-diemmy", "Viettel hn-diemmy"),
]


def save(name: str, data: bytes) -> Path | None:
    if not data or len(data) < 800:
        return None
    p = OUT / name
    p.write_bytes(data)
    return p


def viettel(voice: str) -> bytes:
    body = json.dumps({
        "speed": 1,
        "voice": voice,
        "text": TEXT,
        "tts_return_option": 3,
        "without_filter": False,
    }).encode()
    req = urllib.request.Request(
        "https://viettelai.vn/tts/speech_synthesis",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read()


async def edge_mp3(voice: str) -> bytes:
    import edge_tts
    import tempfile
    tmp = Path(tempfile.mkstemp(suffix=".mp3")[1])
    try:
        c = edge_tts.Communicate(TEXT, voice, rate="+8%")
        await asyncio.wait_for(c.save(str(tmp)), timeout=30)
        data = tmp.read_bytes()
        return data if len(data) > 800 else b""
    finally:
        tmp.unlink(missing_ok=True)


def write_html(rows: list[tuple[str, str, str]]) -> None:
    items = []
    for fname, label, status in rows:
        if status == "ok":
            items.append(
                f"<div class='row'><b>{label}</b><br>"
                f"<audio controls src='{fname}'></audio></div>"
            )
        else:
            items.append(f"<div class='row mute'><b>{label}</b> — {status}</div>")
    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Nghe thu giong TTS</title>
<style>
body{{font-family:system-ui;max-width:720px;margin:24px auto;padding:0 16px;background:#111;color:#eee}}
.row{{margin:16px 0;padding:12px;background:#1b1b1b;border-radius:8px}}
.mute{{opacity:.6}}
audio{{width:100%;margin-top:8px}}
</style>
<h1>Nghe thu giong</h1>
<p>Cung mot doan loi. Nhan Play lan luot.</p>
{''.join(items)}
"""
    (OUT / "index.html").write_text(html, encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "_loi.txt").write_text(TEXT + "\n", encoding="utf-8")
    rows: list[tuple[str, str, str]] = []

    print("edge")
    for vid, label in (
        ("vi-VN-NamMinhNeural", "Edge Nam Minh"),
        ("vi-VN-HoaiMyNeural", "Edge Hoai My"),
    ):
        fname = f"edge_{vid}.mp3"
        try:
            data = asyncio.run(edge_mp3(vid))
            if save(fname, data):
                rows.append((fname, label, "ok"))
                print(" ok", label)
            else:
                rows.append((fname, label, "edge khong tra audio"))
                print(" fail", label)
        except Exception as e:
            rows.append((fname, label, str(e)[:80]))
            print(" fail", label, e)

    print("gtts")
    try:
        from gtts import gTTS
        p = OUT / "gtts_google_translate.mp3"
        gTTS(TEXT, lang="vi").save(str(p))
        rows.append((p.name, "gTTS Google Translate (nu)", "ok"))
        print(" ok gtts")
    except Exception as e:
        rows.append(("gtts.mp3", "gTTS", str(e)[:80]))

    print("piper")
    try:
        from piper.voice import PiperVoice
        onnx = ROOT / "work/tts_models/vi_VN-vais1000-medium.onnx"
        cfg = ROOT / "work/tts_models/vi_VN-vais1000-medium.onnx.json"
        if onnx.exists():
            voice = PiperVoice.load(str(onnx), config_path=str(cfg))
            wav = OUT / "piper_vais1000.wav"
            chunks = list(voice.synthesize(TEXT))
            c0 = chunks[0]
            with wave.open(str(wav), "wb") as wf:
                wf.setnchannels(c0.sample_channels)
                wf.setsampwidth(c0.sample_width)
                wf.setframerate(c0.sample_rate)
                for ch in chunks:
                    wf.writeframes(ch.audio_int16_bytes)
            rows.append((wav.name, "Piper vais1000 local", "ok"))
            print(" ok piper")
        else:
            rows.append(("piper.wav", "Piper", "chua co model"))
    except Exception as e:
        rows.append(("piper.wav", "Piper", str(e)[:80]))

    print("viettel")
    for code, label in VIETTEL:
        fname = f"viettel_{code}.mp3"
        try:
            data = viettel(code)
            if save(fname, data):
                rows.append((fname, label, "ok"))
                print(" ok", code, len(data))
            else:
                rows.append((fname, label, "API khong nhan voice nay"))
                print(" empty", code)
        except Exception as e:
            rows.append((fname, label, str(e)[:100]))
            print(" fail", code, e)
        time.sleep(0.45)

    write_html(rows)
    ok = sum(1 for *_, s in rows if s == "ok")
    print(f"DONE ok={ok}/{len(rows)} dir={OUT}")


if __name__ == "__main__":
    main()
