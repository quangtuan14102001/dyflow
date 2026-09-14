"""API goi pipeline tu URL + giong (dung cho bot Telegram)."""
from __future__ import annotations
import re
import time
import uuid
from pathlib import Path

from .main import process_job
from .utils import load_config, log, run
from .voices import DEFAULT_LANG, resolve_preset

_VIDEO_ID_RE = re.compile(r"douyin\.com/(?:share/)?video/(\d{10,})", re.I)
_MODAL_ID_RE = re.compile(r"modal_id=(\d{10,})", re.I)
_VID_QUERY_RE = re.compile(r"[?&]vid=(\d{10,})", re.I)
_SHORT_RE = re.compile(r"v\.douyin\.com/", re.I)
_CHANNEL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"douyin\.com/(?:user|channel|share/user)/"
    r"|iesdouyin\.com/share/user/"
    r")[^\s/?#]+",
    re.I,
)
_NOT_VIDEO_RE = re.compile(
    r"douyin\.com/(search|discover|follow|live|hot|root)(?:/|\?|$)",
    re.I,
)

_NOT_VIDEO_MSG = (
    "Day la trang tim kiem/trang Douyin, khong phai link 1 video. "
    "Mo video -> chia se -> sao chep link. Link dung co dang "
    "https://www.douyin.com/video/1234567890 hoac https://v.douyin.com/xxxxx/"
)


def _is_douyin_non_video(url: str) -> bool:
    return bool(_NOT_VIDEO_RE.search(url or ""))


def is_channel_url(url: str) -> bool:
    """Link user/kênh Douyin — expand thanh danh sach video, khong tai 1 clip."""
    return bool(_CHANNEL_RE.search(url or ""))


def embedded_video_urls(text: str) -> list[str]:
    """Video id di kem link kenh (vid= / modal_id= / /video/).

    Tren link /user/...&vid= thi vid chi la modal dang mo — khong phai
    "chi 1 video cua kenh". Bo qua de expand_sources liet ke ca kenh.
    """
    if is_channel_url(text or ""):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for rx in (_VIDEO_ID_RE, _MODAL_ID_RE, _VID_QUERY_RE):
        for m in rx.finditer(text or ""):
            href = f"https://www.douyin.com/video/{m.group(1)}"
            if href in seen:
                continue
            seen.add(href)
            out.append(href)
    return out


def normalize_douyin_url(text: str) -> str:
    """Chuan hoa link Douyin (ca link search co modal_id, ca user/kênh)."""
    text = text.strip()
    m = re.search(r"https?://[^\s<>\"']+", text)
    url = m.group(0) if m else text
    # bo ky tu thua cuoi (dau cau khi copy)
    url = url.rstrip(".,;:!?)】」』\"'")
    modal = _MODAL_ID_RE.search(url)
    if modal:
        return f"https://www.douyin.com/video/{modal.group(1)}"
    vid = _VIDEO_ID_RE.search(url)
    if vid:
        return f"https://www.douyin.com/video/{vid.group(1)}"
    ch = _CHANNEL_RE.search(url)
    if ch:
        return ch.group(0).rstrip("/")
    if _is_douyin_non_video(url):
        raise ValueError(_NOT_VIDEO_MSG)
    if _SHORT_RE.search(url):
        return url
    if re.search(r"douyin\.com", url, re.I):
        return url
    raise ValueError(
        "Khong nhan dien link Douyin. Gui link dang: https://www.douyin.com/video/..."
    )


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
            if _is_douyin_non_video(raw):
                raise
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
            "voice_id": preset["id"],
            "rate": preset["rate"],
            "pitch": preset["pitch"],
            "volume": preset["volume"],
            "tts_engine": "edge",
            "openai_model": "tts-1",
            "openai_voice": preset.get("openai_voice") or "",
            "openai_voice_male": "onyx",
            "openai_voice_female": "nova",
            "refresh_tts": True,
            "skip_failed_segments": True,
            "tts_segment_gap": 0.4,
            "match_original_prosody": True,
            "max_atempo_stretch": 1.08,
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
