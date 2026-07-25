"""API goi pipeline tu URL + giong (dung cho bot Telegram)."""
from __future__ import annotations
import re
import time
import uuid
from pathlib import Path

from .main import process_job
from .utils import load_config, log, run
from .voices import DEFAULT_LANG, resolve_preset


def normalize_douyin_url(text: str) -> str:
    """Chuan hoa link Douyin (ca link search co modal_id)."""
    text = text.strip()
    m = re.search(r"https?://[^\s<>\"']+", text)
    url = m.group(0) if m else text
    # bo ky tu thua cuoi (dau cau khi copy)
    url = url.rstrip(".,;:!?)】」』\"'")
    modal = re.search(r"modal_id=(\d+)", url)
    if modal:
        return f"https://www.douyin.com/video/{modal.group(1)}"
    vid = re.search(r"douyin\.com/video/(\d+)", url)
    if vid:
        return f"https://www.douyin.com/video/{vid.group(1)}"
    if re.search(r"douyin\.com", url, re.I):
        return url
    raise ValueError("Khong nhan dien link Douyin. Gui link dang: https://www.douyin.com/video/...")


def extract_douyin_urls(text: str, *, limit: int = 20) -> list[str]:
    """Lay tat ca link Douyin tu van ban (nhieu dong / nhieu share text).

    Giu thu tu, loai trung. Toi da `limit` link.
    """
    found: list[str] = []
    seen: set[str] = set()
    # Tim moi URL http(s); moi dong khong co URL thi thu parse ca dong
    candidates = re.findall(r"https?://[^\s<>\"']+", text or "")
    if not candidates:
        chunk = (text or "").strip()
        if chunk:
            candidates = [chunk]
    for raw in candidates:
        try:
            url = normalize_douyin_url(raw)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        found.append(url)
        if len(found) >= limit:
            break
    return found


def build_job(
    url: str,
    voice: str = "male",
    job_id: str | None = None,
    lang: str = DEFAULT_LANG,
) -> dict:
    preset = resolve_preset(lang, voice)
    jid = job_id or f"tg_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    return {
        "id": jid,
        "source": normalize_douyin_url(url),
        "script": "AUTO",
        # Bot: clip dai tu dong cat ~2 phut dau de xu ly + gui Telegram
        "max_duration_sec": 120,
        "auto_trim": True,
        "lang": preset["lang"],
        "voiceover": {
            "audio_mode": "dub",
            "voice": preset["voice"],
            "rate": preset["rate"],
            "pitch": preset["pitch"],
            "volume": preset["volume"],
            "refresh_tts": True,
            "skip_failed_segments": True,
            "tts_segment_gap": 1.2,
            "auto": {
                "translate_to": preset["translate_to"],
                "translate_from": "zh-CN",
                "source_lang": "zh",
            },
        },
        "caption": {
            "chars_per_line": preset["chars_per_line"],
        },
        "export": {"fit": "source", "formats": ["source"]},
        "transform": {
            "fit": "source",
            "segments": 1,
            "zoom": 1.0,
            "zoom_step": 0.0,
            "contrast": 1.0,
            "saturation": 1.0,
            "gamma": 1.0,
        },
    }


def build_web_job(
    url: str,
    voice: str = "male",
    *,
    lang: str = DEFAULT_LANG,
    max_duration_sec: int = 120,
    auto_trim: bool = True,
) -> dict:
    """Job cho web app: dung UUID rieng (khong prefix tg_)."""
    job = build_job(url, voice=voice, lang=lang, job_id=f"web_{uuid.uuid4().hex}")
    job["max_duration_sec"] = max_duration_sec
    job["auto_trim"] = auto_trim
    return job


def run_from_url(
    url: str,
    voice: str = "male",
    config_path: str | Path = "config.yaml",
    job_id: str | None = None,
    lang: str = DEFAULT_LANG,
) -> Path:
    """Tai + xu ly 1 clip Douyin, tra ve duong dan video thanh pham."""
    cfg = load_config(config_path)
    job = build_job(url, voice=voice, lang=lang, job_id=job_id)
    log.info("Pipeline: %s | lang=%s giong=%s", job["source"], lang, voice)
    outputs = process_job(job, cfg)
    if not outputs:
        raise RuntimeError("Pipeline khong tao duoc file output")
    return outputs[0]


def compress_for_telegram(path: Path, max_mb: float = 48.0) -> Path:
    """Nen nhe neu file > gioi han gui Telegram (~50MB)."""
    path = Path(path)
    limit = int(max_mb * 1024 * 1024)
    if path.stat().st_size <= limit:
        return path
    out = path.with_name(f"{path.stem}_tg.mp4")
    log.info("Nen video cho Telegram: %s", out.name)
    run([
        "ffmpeg", "-y", "-i", str(path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "28",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ])
    return out
