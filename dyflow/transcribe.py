"""script=AUTO: phu de Douyin / Whisper (timestamp) -> dich tung doan."""
from __future__ import annotations
import json
import re
from pathlib import Path

from .utils import log, run, probe_duration

_MODEL_CACHE: dict = {}


def extract_audio(video, out_wav):
    out_wav = Path(out_wav)
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ar", "16000",
         "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)])
    return out_wav


def _parse_srt_time(ts: str) -> float:
    h, m, rest = ts.strip().split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _parse_vtt_time(ts: str) -> float:
    ts = ts.strip()
    if ts.count(":") == 2:
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    m, s = ts.split(":")
    return int(m) * 60 + float(s)


def parse_subtitle_file(path: Path) -> list[dict]:
    """Doc SRT/VTT -> [{start, end, text}, ...]."""
    text = path.read_text(encoding="utf-8", errors="replace")
    segments: list[dict] = []

    if path.suffix.lower() == ".vtt":
        blocks = re.split(r"\n\n+", text.replace("\r", ""))
        for block in blocks:
            lines = [ln for ln in block.split("\n") if ln.strip() and not ln.startswith("WEBVTT")]
            if len(lines) < 2:
                continue
            timing = lines[0]
            if "-->" not in timing:
                continue
            a, b = timing.split("-->")
            body = " ".join(lines[1:]).strip()
            body = re.sub(r"<[^>]+>", "", body)
            if body:
                segments.append({
                    "start": _parse_vtt_time(a),
                    "end": _parse_vtt_time(b),
                    "text": body,
                })
        return segments

    # SRT
    blocks = re.split(r"\n\n+", text.replace("\r", ""))
    for block in blocks:
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        timing_idx = 1 if lines[0].isdigit() else 0
        if timing_idx >= len(lines) or "-->" not in lines[timing_idx]:
            continue
        a, b = lines[timing_idx].split("-->")
        body = " ".join(lines[timing_idx + 1:]).strip()
        if body:
            segments.append({
                "start": _parse_srt_time(a),
                "end": _parse_srt_time(b),
                "text": body,
            })
    return segments


def _speech_coverage(segments: list[dict], video_dur: float) -> float:
    """Ty le thoi luong co loi thoai / tong video."""
    if not segments or video_dur <= 0:
        return 0.0
    spoken = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)
    return spoken / video_dur


def try_load_platform_subs(work_dir: Path, video_dur: float = 0.0,
                           min_coverage: float = 0.35) -> list[dict] | None:
    """Phu de Douyin neu du day; neu thieu thi bo qua de Whisper nghe lai."""
    for pattern in ("*.srt", "*.vtt", "*.ass"):
        for path in sorted(work_dir.glob(pattern)):
            segs = parse_subtitle_file(path)
            if not segs:
                continue
            cov = _speech_coverage(segs, video_dur) if video_dur > 0 else 1.0
            log.info("Phu de %s: %d doan, coverage %.0f%%", path.name, len(segs), cov * 100)
            if video_dur > 0 and cov < min_coverage:
                log.warning(
                    "Phu de qua ngan (%.0f%% < %.0f%%), chuyen sang Whisper",
                    cov * 100, min_coverage * 100,
                )
                return None
            log.info("Dung phu de san co: %s", path.name)
            return segs
    return None


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # bo doan lap vo nghia: "我 我 我"
    if re.fullmatch(r"(?:[\u4e00-\u9fff]\s*){1,3}", text) and len(set(text.split())) == 1:
        return ""
    return text


def merge_segments(
    segments: list[dict],
    max_gap: float = 0.5,
    min_dur: float = 0.25,
    max_dur: float = 8.0,
    max_chars: int = 180,
) -> list[dict]:
    """Gop cau ngan lien ke; khong gop thanh 1 block dai (gay TTS cham + dich loi)."""
    merged: list[dict] = []
    for seg in segments:
        text = _clean_text(seg.get("text", ""))
        if not text:
            continue
        start, end = float(seg["start"]), float(seg["end"])
        if end - start < min_dur:
            end = start + min_dur
        if merged and start - merged[-1]["end"] <= max_gap:
            trial = f"{merged[-1]['text']} {text}".strip()
            span = end - merged[-1]["start"]
            if span <= max_dur and len(trial) <= max_chars:
                merged[-1]["end"] = end
                merged[-1]["text"] = trial
                continue
        merged.append({"start": start, "end": end, "text": text})
    return merged


def transcribe_segments(audio, model_size="small", source_lang="zh",
                        compute_type="int8", vad=False) -> list[dict]:
    """Whisper -> [{start, end, text}, ...] theo thoi gian noi goc."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "Chua cai faster-whisper. pip install faster-whisper deep-translator"
        ) from e
    try:
        from .web.locks import WHISPER_LOCK
    except Exception:  # noqa: BLE001
        WHISPER_LOCK = None

    key = (model_size, compute_type)
    if WHISPER_LOCK is not None:
        WHISPER_LOCK.acquire()
    try:
        if key not in _MODEL_CACHE:
            log.info("Tai/nap Whisper model '%s'...", model_size)
            _MODEL_CACHE[key] = WhisperModel(model_size, device="cpu", compute_type=compute_type)
        model = _MODEL_CACHE[key]
        kwargs: dict = {
            "language": source_lang or None,
            "word_timestamps": True,
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.45,
        }
        if vad:
            kwargs["vad_filter"] = True
            kwargs["vad_parameters"] = dict(min_silence_duration_ms=800)
            log.info("Whisper VAD: ON (co the bo sot thoai trong clip nhieu am thanh)")
        else:
            log.info("Whisper VAD: OFF (nghe toan bo audio)")
        raw_segments, info = model.transcribe(str(audio), **kwargs)
        log.info("Whisper detect ngon ngu: %s (p=%.2f)", info.language, info.language_probability)

        segments: list[dict] = []
        for seg in raw_segments:
            text = _clean_text(seg.text)
            if not text:
                continue
            segments.append({"start": seg.start, "end": seg.end, "text": text})
        log.info("Transcribe xong: %d doan (truoc khi gop)", len(segments))
        return segments
    finally:
        if WHISPER_LOCK is not None:
            WHISPER_LOCK.release()


def _translate_one(text: str, tr) -> str:
    if not text.strip():
        return ""
    try:
        return tr.translate(text) or text
    except Exception:
        log.warning("Dich loi doan, giu nguyen: %s...", text[:40])
        return text


def translate_segments(segments: list[dict], target="vi", source="auto") -> list[dict]:
    import time
    try:
        from deep_translator import GoogleTranslator
    except ImportError as e:
        raise RuntimeError("Chua cai deep-translator. pip install deep-translator") from e
    tr = GoogleTranslator(source=source, target=target)
    out = []
    n = len(segments)
    for i, seg in enumerate(segments, 1):
        vi = _translate_one(seg["text"], tr)
        if vi.strip():
            out.append({**seg, "text": vi})
        if i % 50 == 0 or i == n:
            log.info("Dang dich: %d/%d doan...", i, n)
        # tranh Google rate-limit khi clip dai
        if i % 20 == 0:
            time.sleep(0.8)
    log.info("Dich xong: %d doan tieng Viet", len(out))
    return out


def auto_script(video, work_dir, cfg_auto) -> tuple[str, list[dict]]:
    """Lay script + timestamp. Uu tien phu de Douyin day du, fallback Whisper."""
    work_dir = Path(work_dir)
    video_dur = probe_duration(video)
    segments = None
    min_cov = float(cfg_auto.get("min_sub_coverage", 0.35))

    if cfg_auto.get("prefer_platform_subs", True):
        segments = try_load_platform_subs(
            work_dir / "raw", video_dur=video_dur, min_coverage=min_cov,
        )

    if not segments:
        audio = extract_audio(video, work_dir / "audio16k.wav")
        segments = transcribe_segments(
            audio,
            model_size=cfg_auto.get("whisper_model", "medium"),
            source_lang=(cfg_auto.get("source_lang", "zh") or None),
            compute_type=cfg_auto.get("compute_type", "int8"),
            vad=cfg_auto.get("vad", False),
        )

    if not segments:
        raise RuntimeError(
            "Khong nghe/doc duoc loi thoai. Clip co the chi co nhac / khong co phu de."
        )

    raw_text = " ".join(s["text"] for s in segments)
    # Dich TUNG doan truoc, roi moi gop (tranh dich 1 block dai bi loi)
    vi_segments = translate_segments(
        segments,
        target=cfg_auto.get("translate_to", "vi"),
        source=cfg_auto.get("translate_from", "zh-CN"),
    )
    vi_segments = merge_segments(
        vi_segments,
        max_gap=float(cfg_auto.get("merge_gap_sec", 0.5)),
        max_dur=float(cfg_auto.get("merge_max_dur", 8.0)),
        max_chars=int(cfg_auto.get("merge_max_chars", 180)),
    )
    if not vi_segments:
        raise RuntimeError("Dich xong nhung khong co doan hop le.")

    cov = _speech_coverage(vi_segments, video_dur)
    log.info(
        "Tong %d doan thoai, phu %.0f%% video (%.0fs / %.0fs)",
        len(vi_segments), cov * 100,
        sum(s["end"] - s["start"] for s in vi_segments), video_dur,
    )
    vi_text = " ".join(s["text"] for s in vi_segments)

    (work_dir / "transcript_goc.txt").write_text(raw_text, encoding="utf-8")
    (work_dir / "script_auto_vi.txt").write_text(vi_text, encoding="utf-8")
    (work_dir / "segments_vi.json").write_text(
        json.dumps(vi_segments, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    log.info("Da luu %d doan co timestamp vao %s", len(vi_segments), work_dir)
    return vi_text, vi_segments
