# -*- coding: utf-8 -*-
"""Client tich hop T-Voice AI (tvoiceai.com) cho VideosCreator / Dyflow.

Ho tro hon 240+ giong doc AI (Vbee, ElevenLabs, Review Phim, Giong Clone).
API REST v2.0 bat dong bo 2 buoc: POST /api/tts -> GET /api/status/{jobId}.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

from .user_tvoice import has_tvoice_key, load_tvoice_key
from .tts_file_io import write_bytes_atomic

log = logging.getLogger("dyflow.tvoice")

BASE_URL = "https://tvoiceai.com"
TTS_ENDPOINT = f"{BASE_URL}/api/tts"
STATUS_ENDPOINT = f"{BASE_URL}/api/status"

_rate_limit_lock = threading.Lock()
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.55  # T-Voice rate limit: 0.5s / lenh

# Map preset do tuoi/gioi tinh mac dinh sang T-Voice Voice ID
TVOICE_DEFAULT_PRESETS: dict[str, str] = {
    "male_child": "VIENEU_CLONE_1787477477754",   # Bé Minh Anh
    "male_young": "VIENEU_CLONE_1787469982602",   # Quang Review
    "male_adult": "Minh Đức",                     # Minh Đức
    "male_mature": "VIENEU_CLONE_1787124911734",  # Giọng Trầm
    "female_child": "VIENEU_CLONE_1787470643348", # An Nhiên
    "female_young": "Ngọc Huyền",                 # Ngọc Huyền 5.0
    "female_adult": "VIENEU_CLONE_1787020235902", # Huyền Trang
    "female_mature": "VIENEU_CLONE_1787470345139",# Trùng Dương (nữ đọc thơ)
}

# Danh sach cac giong T-Voice noi bat theo tung the loai
TVOICE_FEATURED_VOICES: list[dict[str, Any]] = [
    # 1. Review Phim & Tin Tức (Hot MMO)
    {"id": "Ngọc Huyền", "name": "Ngọc Huyền 5.0 (Cực hay)", "category": "review", "gender": "female"},
    {"id": "VIENEU_CLONE_1787360104935", "name": "Thư Review (Review Phim Nữ)", "category": "review", "gender": "female"},
    {"id": "VIENEU_CLONE_1787469982602", "name": "Quang Review (Review Phim Nam)", "category": "review", "gender": "male"},
    {"id": "VIENEU_CLONE_1787390814669", "name": "Lồng tiếng phim (Nam kịch tính)", "category": "review", "gender": "male"},
    {"id": "VIENEU_CLONE_1787469097568", "name": "Trinh Review", "category": "review", "gender": "female"},
    {"id": "VIENEU_CLONE_1787469314027", "name": "Chinh Review", "category": "review", "gender": "female"},
    {"id": "VIENEU_CLONE_1787469913302", "name": "Hà Review", "category": "review", "gender": "female"},
    {"id": "VIENEU_CLONE_1787468949540", "name": "Dung Lồng Tiếng", "category": "review", "gender": "female"},
    {"id": "VIENEU_CLONE_1787468550722", "name": "Vy Tin Tức", "category": "review", "gender": "female"},
    {"id": "VIENEU_CLONE_1787034153779", "name": "Anh Khôi (Nam trẻ truyền cảm)", "category": "review", "gender": "male"},
    {"id": "VIENEU_CLONE_1787020235902", "name": "Huyền Trang (Nữ tin tức/review)", "category": "review", "gender": "female"},

    # 2. Studio Vbee
    {"id": "VIENEU_CLONE_1787109250959", "name": "Bảo Trung (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1787109089696", "name": "Bảo Trung Tin Tức (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1787108387104", "name": "Tuệ Giác (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1787109396559", "name": "Nguyệt Nga (Vbee)", "category": "vbee", "gender": "female"},
    {"id": "VIENEU_CLONE_1789542423709", "name": "Mai Phương (Vbee)", "category": "vbee", "gender": "female"},
    {"id": "VIENEU_CLONE_1788772014283", "name": "Mạnh Dũng (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1788488177285", "name": "Minh Quân Pro (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1787035373337", "name": "Ngạn Kể Chuyện (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1787107256853", "name": "Thiện Tâm (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1788860912470", "name": "Thắng Chuyên Nghiệp (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1788861213289", "name": "Ngọc Ánh (Vbee)", "category": "vbee", "gender": "female"},
    {"id": "VIENEU_CLONE_1789368452459", "name": "Cuongphimx (Vbee)", "category": "vbee", "gender": "male"},
    {"id": "VIENEU_CLONE_1792294233907", "name": "Ngọc Lan (Vbee)", "category": "vbee", "gender": "female"},
    {"id": "VIENEU_CLONE_1792893989306", "name": "Giọng nam trầm hay (Vbee)", "category": "vbee", "gender": "male"},

    # 3. ElevenLabs
    {"id": "VIENEU_CLONE_1787126397339", "name": "Quỳnh Anh (ElevenLabs)", "category": "elevenlabs", "gender": "female"},
    {"id": "VIENEU_CLONE_1787126648256", "name": "Giang kể truyện (ElevenLabs)", "category": "elevenlabs", "gender": "male"},
    {"id": "VIENEU_CLONE_1787035722863", "name": "Nhật Phong (ElevenLabs)", "category": "elevenlabs", "gender": "male"},
    {"id": "VIENEU_CLONE_1787127353537", "name": "Ninh Đôn (ElevenLabs)", "category": "elevenlabs", "gender": "male"},
    {"id": "VIENEU_CLONE_1787127757952", "name": "Thảo (ElevenLabs)", "category": "elevenlabs", "gender": "female"},
    {"id": "VIENEU_CLONE_1787479116744", "name": "Vũ (ElevenLabs)", "category": "elevenlabs", "gender": "male"},
    {"id": "VIENEU_CLONE_1787650427377", "name": "Oanh (ElevenLabs)", "category": "elevenlabs", "gender": "female"},
    {"id": "VIENEU_CLONE_1787968800612", "name": "Hoàng Bảo (ElevenLabs)", "category": "elevenlabs", "gender": "male"},
    {"id": "VIENEU_CLONE_1787969676313", "name": "Minh giọng nữ (ElevenLabs)", "category": "elevenlabs", "gender": "female"},
    {"id": "VIENEU_CLONE_1787969921569", "name": "Minh giọng nam (ElevenLabs)", "category": "elevenlabs", "gender": "male"},
    {"id": "VIENEU_CLONE_1787991691857", "name": "Tuấn Anh (ElevenLabs)", "category": "elevenlabs", "gender": "male"},

    # 4. Kể chuyện, Đạo lý & Lịch sử
    {"id": "VIENEU_CLONE_1787125634768", "name": "Giọng Truyện Ma (Kinh dị/Hồi hộp)", "category": "story", "gender": "male"},
    {"id": "VIENEU_CLONE_1787125886979", "name": "Giọng Đạo Lý (Trầm tư)", "category": "story", "gender": "male"},
    {"id": "VIENEU_CLONE_1787124911734", "name": "Giọng Trầm (Ấm áp)", "category": "story", "gender": "male"},
    {"id": "VIENEU_CLONE_1787899636409", "name": "Giọng nam kể truyện", "category": "story", "gender": "male"},
    {"id": "VIENEU_CLONE_1787105257331", "name": "Tùng Dương", "category": "story", "gender": "male"},
    {"id": "VIENEU_CLONE_1789630637370", "name": "Kể truyện tuổi già", "category": "story", "gender": "male"},
    {"id": "VIENEU_CLONE_1787105725793", "name": "Tâm sự tuổi già (Nữ)", "category": "story", "gender": "female"},
    {"id": "VIENEU_CLONE_1790136243010", "name": "Giọng lịch sử", "category": "story", "gender": "male"},
    {"id": "VIENEU_CLONE_1790209583841", "name": "Giọng lịch sử (Nữ)", "category": "story", "gender": "female"},
    {"id": "VIENEU_CLONE_1790821028465", "name": "Lê Chức (Hùng hồn, tài liệu)", "category": "story", "gender": "male"},
    {"id": "VIENEU_CLONE_1791971679047", "name": "Giọng Tào Tháo", "category": "story", "gender": "male"},

    # 5. Cơ bản & Chuẩn
    {"id": "Minh Đức", "name": "Minh Đức (Nam chuẩn)", "category": "standard", "gender": "male"},
    {"id": "Thái Sơn", "name": "Thái Sơn (Nam đĩnh đạc)", "category": "standard", "gender": "male"},
    {"id": "Ngọc Linh", "name": "Ngọc Linh (Nữ nhẹ nhàng)", "category": "standard", "gender": "female"},
    {"id": "Mai Anh", "name": "Mai Anh (Nữ trong trẻo)", "category": "standard", "gender": "female"},
    {"id": "Thùy Dung", "name": "Thùy Dung", "category": "standard", "gender": "female"},
    {"id": "Quang Sơn", "name": "Quang Sơn", "category": "standard", "gender": "male"},
    {"id": "Mỹ Duyên", "name": "Mỹ Duyên", "category": "standard", "gender": "female"},
    {"id": "Quỳnh Anh", "name": "Quỳnh Anh", "category": "standard", "gender": "female"},
    {"id": "Đức Trí", "name": "Đức Trí", "category": "standard", "gender": "male"},
    {"id": "Kim Thanh", "name": "Kim Thanh", "category": "standard", "gender": "female"},
]


def list_tvoice_voices() -> list[dict[str, Any]]:
    """Tra ve danh sach giong T-Voice phan loai cho UI."""
    return [dict(v) for v in TVOICE_FEATURED_VOICES]


def resolve_tvoice_voice_id(voice_id_or_preset: str) -> str:
    """Chuyen doi preset (vi du 'male_adult' hoac 'Ngọc Huyền') thanh voice_id T-Voice chuan."""
    raw = (voice_id_or_preset or "").strip()
    if not raw:
        return "Ngọc Huyền"
    if raw in TVOICE_DEFAULT_PRESETS:
        return TVOICE_DEFAULT_PRESETS[raw]
    low = raw.lower()
    for alias, vid in TVOICE_DEFAULT_PRESETS.items():
        if alias == low:
            return vid
    # Neu la ma Edge TTS
    if "namminh" in low:
        return "Minh Đức"
    if "hoaimy" in low:
        return "Ngọc Huyền"
    # Tra cuu theo ID hoac ten trong thu vien giong
    for v in TVOICE_FEATURED_VOICES:
        if v["id"] == raw:
            return v["id"]
        if low in v["name"].lower():
            return v["id"]
    return raw


def _wait_rate_limit() -> None:
    global _last_request_time
    with _rate_limit_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()


def parse_speed_to_float(speed_val: Any) -> float:
    """Chuyen doi rate '+5%', '-10%', hoac float thanh he so toc do 0.5 - 2.0."""
    if speed_val is None:
        return 1.0
    if isinstance(speed_val, (int, float)):
        return max(0.5, min(2.0, float(speed_val)))
    s = str(speed_val).strip()
    if s.endswith("%"):
        try:
            pct = float(s[:-1])
            return max(0.5, min(2.0, round(1.0 + pct / 100.0, 2)))
        except ValueError:
            return 1.0
    try:
        return max(0.5, min(2.0, float(s)))
    except ValueError:
        return 1.0


def synthesize_tvoice(
    text: str,
    out_path: str | Path,
    *,
    voice: str = "Ngọc Huyền",
    speed: float | str = 1.0,
    api_key: str | None = None,
    timeout: float = 60.0,
) -> Path:
    """Tao file am thanh MP3 tu van ban su dung API T-Voice AI.
    
    Quy trinh:
    1. POST /api/tts tao job
    2. Poll /api/status/{jobId} den khi done
    3. Download audioUrl vao out_path
    """
    out_path = Path(out_path)
    text = (text or "").strip()
    if not text:
        raise RuntimeError("Van ban trong, khong the tao giong noi T-Voice")

    key = (api_key or load_tvoice_key()).strip()
    if not key:
        raise RuntimeError(
            "Chua co API Key T-Voice. Vui long nhap key sk_tvoice_... cua ban trong app."
        )

    voice_id = resolve_tvoice_voice_id(voice)
    speed_float = parse_speed_to_float(speed)

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "voice_id": voice_id,
        "speed": speed_float,
        "format": "mp3",
        "generate_srt": False,
    }

    _wait_rate_limit()

    # 1. Khoi tao Job
    try:
        res = requests.post(TTS_ENDPOINT, headers=headers, json=payload, timeout=25.0)
    except Exception as e:
        raise RuntimeError(f"Khong the ket noi den T-Voice API ({TTS_ENDPOINT}): {e}") from e

    if res.status_code != 200:
        raise RuntimeError(f"T-Voice API tra loi loi HTTP {res.status_code}: {res.text}")

    try:
        init_data = res.json()
    except Exception as e:
        raise RuntimeError(f"T-Voice API khong tra ve JSON hop le: {res.text}") from e

    job_id = init_data.get("jobId")
    if not job_id:
        err = init_data.get("error") or init_data.get("message") or res.text
        raise RuntimeError(f"T-Voice API khong tra ve jobId: {err}")

    log.info("T-Voice tao job thanh cong: %s (voice=%s, chars=%d)", job_id, voice_id, len(text))

    # 2. Cho ket qua (Polling)
    poll_headers = {"Authorization": f"Bearer {key}"}
    status_url = f"{STATUS_ENDPOINT}/{job_id}"
    start_time = time.time()
    audio_url = ""

    while time.time() - start_time < timeout:
        time.sleep(1.5)
        _wait_rate_limit()
        try:
            poll_res = requests.get(status_url, headers=poll_headers, timeout=15.0)
            if poll_res.status_code != 200:
                log.warning("Poll status HTTP %d: %s", poll_res.status_code, poll_res.text)
                continue
            status_data = poll_res.json()
        except Exception as e:
            log.warning("Loi poll status T-Voice: %s", e)
            continue

        status = str(status_data.get("status") or "").lower()
        if status == "done":
            audio_url = status_data.get("audioUrl") or ""
            break
        if status in ("error", "failed"):
            err_msg = status_data.get("error") or status_data.get("message") or "Unknown error"
            raise RuntimeError(f"T-Voice tao audio that bai: {err_msg}")

    if not audio_url:
        raise RuntimeError(f"T-Voice timeout sau {timeout}s (jobId: {job_id})")

    # 3. Tai file audio ve out_path
    try:
        dl_res = requests.get(audio_url, timeout=30.0, stream=True)
        if dl_res.status_code != 200:
            raise RuntimeError(f"Khong the tai audio tu {audio_url}: HTTP {dl_res.status_code}")
        data = dl_res.content
    except Exception as e:
        raise RuntimeError(f"Loi tai file audio T-Voice tu {audio_url}: {e}") from e

    if len(data) < 100:
        raise RuntimeError(f"File audio T-Voice qua nho ({len(data)} bytes)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(out_path, data)
    log.info("T-Voice TTS hoan tat: %s (size=%d bytes)", out_path.name, len(data))
    return out_path
