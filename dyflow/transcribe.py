"""script=AUTO: phu de Douyin / Whisper (timestamp) -> dich tung doan."""
from __future__ import annotations
import json
import re
import threading
import time
from pathlib import Path

from .dub_constants import (
    DUB_GROUP_MAX_CHARS,
    DUB_GROUP_MAX_DUR_SEC,
    DUB_GROUP_MAX_GAP_SEC,
    TRANSLATE_MAX_RETRIES,
    TRANSLATE_MIN_CHAR_RATIO,
    TRANSLATE_RETRY_SLEEP_SEC,
    WHISPER_VAD_DEFAULT,
    WHISPER_VAD_MIN_SILENCE_MS,
    WHISPER_VAD_SPEECH_PAD_MS,
    WHISPER_WORD_TIMESTAMPS,
)
from .dub_report import write_transcribe_report, write_translate_csv
from .utils import log, run, probe_duration

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

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


def _loop_ratio(text: str) -> float:
    """0 = khong lap, ~1 = ao giac lap nang (so voi ban da collapse)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) < 8:
        return 0.0
    collapsed = collapse_loop_text(text, max_repeat=1)
    if not collapsed:
        return 1.0
    return max(0.0, 1.0 - (len(collapsed) / max(1, len(text))))


def segments_look_loopy(segments: list[dict], *, ratio: float = 0.35) -> bool:
    """True neu transcript co dau hieu Whisper lap chuoi."""
    if not segments:
        return False
    joined = " ".join(str(s.get("text") or "") for s in segments)
    if _loop_ratio(joined) >= ratio:
        return True
    for s in segments:
        t = str(s.get("text") or "")
        if len(t) >= 12 and _loop_ratio(t) >= 0.45:
            return True
    return False


def _norm_cmp(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _collapse_token_loops(tokens: list[str], max_repeat: int = 1) -> list[str]:
    """Gop n-gram lap: [A,B,A,B,A,B] -> [A,B] (toi da max_repeat lan)."""
    if len(tokens) < 2:
        return tokens
    out = tokens
    for _ in range(12):
        changed = False
        max_n = max(1, len(out) // 2)
        for n in range(1, max_n + 1):
            i = 0
            new: list[str] = []
            while i < len(out):
                gram = out[i : i + n]
                if len(gram) < n:
                    new.extend(out[i:])
                    break
                count = 1
                while (
                    i + (count + 1) * n <= len(out)
                    and out[i + count * n : i + (count + 1) * n] == gram
                ):
                    count += 1
                keep = min(count, max_repeat)
                for _k in range(keep):
                    new.extend(gram)
                if count > max_repeat:
                    changed = True
                i += count * n
            if changed:
                out = new
                break
            out = new
        if not changed:
            break
    return out


def _collapse_cjk_loops(text: str, max_repeat: int = 1) -> str:
    """Gop chuoi CJK lap: 小六小六小六 -> 小六 (toi da max_repeat)."""
    s = re.sub(r"\s+", "", text)
    for _ in range(8):
        prev = s
        for n in range(1, min(12, max(1, len(s) // 2)) + 1):
            s = re.sub(
                rf"(.{{{n}}})\1{{{max_repeat},}}",
                lambda m, mr=max_repeat: m.group(1) * mr,
                s,
            )
        if s == prev:
            break
    return s


def collapse_loop_text(text: str, max_repeat: int = 1) -> str:
    """Chat chuoi/ngram lap do Whisper ao giac (小六小六 / Tiểu Lục Tiểu Lục...)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return ""
    if " " in text:
        text = " ".join(_collapse_token_loops(text.split(" "), max_repeat=max_repeat))
    if re.search(r"[\u4e00-\u9fff]", text):
        collapsed = _collapse_cjk_loops(text, max_repeat=max_repeat)
        if " " not in text:
            text = collapsed
        else:
            parts = []
            for tok in text.split(" "):
                if re.search(r"[\u4e00-\u9fff]", tok) and len(tok) >= 4:
                    parts.append(_collapse_cjk_loops(tok, max_repeat=max_repeat))
                else:
                    parts.append(tok)
            text = " ".join(_collapse_token_loops(parts, max_repeat=max_repeat))
    return re.sub(r"\s+", " ", text).strip()

def _is_junk_segment(text: str) -> bool:
    """Watermark / noise cuoi clip (by xxx, bwd6...)."""
    t = text.strip()
    if not t:
        return True
    low = t.casefold()
    if re.fullmatch(r"(?:by|bởi|via)\s+[\w.\-]{1,20}", low):
        return True
    if re.fullmatch(r"[a-z0-9_\-.]{2,12}", low) and not re.search(r"[\u4e00-\u9fff]", t):
        # chuoi ngan toan ascii (vd bwd6) — thuong la watermark
        if re.search(r"\d", low) or len(low) <= 6:
            return True
    return False


def _clean_text(text: str) -> str:
    text = collapse_loop_text(re.sub(r"\s+", " ", text).strip(), max_repeat=1)
    # chi bo spam 1 ky tu lap: "我我我" / "我 我 我" — khong xoa ten ngan nhu 小六
    compact = re.sub(r"\s+", "", text)
    if (
        compact
        and len(compact) <= 4
        and len(set(compact)) == 1
        and "\u4e00" <= compact[0] <= "\u9fff"
    ):
        return ""
    if _is_junk_segment(text):
        return ""
    return text


def dedupe_segments(segments: list[dict]) -> list[dict]:
    """Bo doan trung / chong thoi gian; chat text lap trong moi doan."""
    out: list[dict] = []
    for seg in segments:
        text = _clean_text(seg.get("text", ""))
        if not text:
            continue
        start, end = float(seg["start"]), float(seg["end"])
        if end <= start:
            continue
        src = (seg.get("source_text") or "").strip()

        def _merge_src(prev: dict, extra: str) -> None:
            extra = extra.strip()
            if not extra:
                return
            old = (prev.get("source_text") or "").strip()
            prev["source_text"] = f"{old} {extra}".strip() if old else extra

        if out:
            prev = out[-1]
            same = _norm_cmp(prev["text"]) == _norm_cmp(text)
            # cung noi dung trong ~4s (Whisper lap cuoi clip) -> 1 doan
            if same and start <= prev["end"] + 4.0:
                prev["end"] = max(prev["end"], end)
                _merge_src(prev, src)
                continue
            if start < prev["end"]:
                if same or _norm_cmp(text) in _norm_cmp(prev["text"]):
                    prev["end"] = max(prev["end"], end)
                    _merge_src(prev, src)
                    continue
                start = prev["end"]
                if end - start < 0.12:
                    continue
        row: dict = {"start": start, "end": end, "text": text}
        if src:
            row["source_text"] = src
        out.append(row)
    return out


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
        src = (seg.get("source_text") or "").strip()
        if merged and start - merged[-1]["end"] <= max_gap:
            # khong gop neu chi la lap lai cung 1 cum tu
            if _norm_cmp(merged[-1]["text"]) == _norm_cmp(text):
                merged[-1]["end"] = max(merged[-1]["end"], end)
                continue
            if _norm_cmp(text) in _norm_cmp(merged[-1]["text"]):
                merged[-1]["end"] = max(merged[-1]["end"], end)
                continue
            trial = collapse_loop_text(f"{merged[-1]['text']} {text}".strip(), max_repeat=1)
            span = end - merged[-1]["start"]
            if span <= max_dur and len(trial) <= max_chars:
                merged[-1]["end"] = end
                merged[-1]["text"] = trial
                if src:
                    prev_src = (merged[-1].get("source_text") or "").strip()
                    merged[-1]["source_text"] = f"{prev_src} {src}".strip() if prev_src else src
                continue
        row = {"start": start, "end": end, "text": text}
        if src:
            row["source_text"] = src
        merged.append(row)
    return merged


def transcribe_segments(audio, model_size="small", source_lang="zh",
                        compute_type="int8", vad: bool | None = None,
                        repetition_penalty: float = 1.2,
                        no_repeat_ngram_size: int = 3,
                        compression_ratio_threshold: float = 2.0,
                        progress=None,
                        audio_dur: float | None = None,
                        ) -> tuple[list[dict], bool]:
    """Whisper -> (segments, had_raw_loops)."""
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
            if progress:
                progress("transcribing", 35, f"Dang tai Whisper {model_size}...")
            _MODEL_CACHE[key] = WhisperModel(model_size, device="cpu", compute_type=compute_type)
        else:
            log.info(
                "Whisper model cache HIT %s/%s — khong load lai (cung process desktop.py)",
                model_size, compute_type,
            )
        model = _MODEL_CACHE[key]
        from .job_timing import note as timing_note, step as timing_step
        timing_note(
            "Whisper",
            model=model_size,
            device="cpu",
            compute_type=compute_type,
        )
        use_vad = WHISPER_VAD_DEFAULT if vad is None else bool(vad)
        kwargs: dict = {
            "language": source_lang or None,
            "word_timestamps": WHISPER_WORD_TIMESTAMPS,
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.45,
            "repetition_penalty": float(repetition_penalty),
            "no_repeat_ngram_size": int(no_repeat_ngram_size),
            "compression_ratio_threshold": float(compression_ratio_threshold),
        }
        if use_vad:
            kwargs["vad_filter"] = True
            kwargs["vad_parameters"] = dict(
                min_silence_duration_ms=WHISPER_VAD_MIN_SILENCE_MS,
                speech_pad_ms=WHISPER_VAD_SPEECH_PAD_MS,
                threshold=0.35,
            )
            log.info("Whisper VAD: ON (threshold=0.35, min_silence=%dms)", WHISPER_VAD_MIN_SILENCE_MS)
        else:
            log.info("Whisper VAD: OFF (nghe toan bo audio — phu hop clip nhac nen)")
        cancelled = threading.Event()
        t0 = time.monotonic()
        last_pct = 35

        def _emit(pct: int, msg: str) -> None:
            nonlocal last_pct
            if progress is None:
                return
            last_pct = max(last_pct, int(pct))
            try:
                progress("transcribing", last_pct, msg)
            except Exception:  # noqa: BLE001
                cancelled.set()

        def _beat() -> None:
            while not cancelled.wait(6):
                elapsed = int(time.monotonic() - t0)
                _emit(last_pct, f"Whisper dang nghe... {elapsed}s")

        beat = threading.Thread(target=_beat, name="whisper-beat", daemon=True)
        _emit(35, f"Whisper {model_size} bat dau...")
        beat.start()
        raw_list: list[dict] = []
        try:
            with timing_step("Whisper"):
                raw_segments, info = model.transcribe(str(audio), **kwargs)
                log.info("Whisper detect ngon ngu: %s (p=%.2f)", info.language, info.language_probability)

                dur = float(audio_dur or 0) or None
                for seg in raw_segments:
                    if cancelled.is_set():
                        from .main import JobCancelled
                        raise JobCancelled("Da huy")
                    text = re.sub(r"\s+", " ", (seg.text or "")).strip()
                    if not text:
                        continue
                    raw_list.append({"start": float(seg.start), "end": float(seg.end), "text": text})
                    end = float(seg.end)
                    if dur and dur > 0:
                        pct = 36 + int(18 * min(1.0, end / dur))
                        _emit(pct, f"Whisper {end:.0f}/{dur:.0f}s")
                    else:
                        _emit(38, f"Whisper {len(raw_list)} doan...")
        finally:
            cancelled.set()
        had_loops = segments_look_loopy(raw_list)
        if had_loops:
            log.warning(
                "Phat hien ao giac lap trong Whisper raw (%d doan) — se chat/retry",
                len(raw_list),
            )

        segments: list[dict] = []
        for seg in raw_list:
            text = _clean_text(seg["text"])
            if not text:
                continue
            segments.append({"start": seg["start"], "end": seg["end"], "text": text})
        log.info("Transcribe xong: %d doan (truoc khi gop)", len(segments))
        return segments, had_loops
    finally:
        if WHISPER_LOCK is not None:
            WHISPER_LOCK.release()


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


class _OpenAILineTranslator:
    """Dich 1 cau bang OpenAI — Google hay fail voi cum ngan (没有, 走...)."""

    def __init__(self, target: str):
        self._target = target or "vi"
        self._client = None

    def _lang(self) -> str:
        names = {
            "vi": "Vietnamese",
            "en": "English",
            "ja": "Japanese",
            "pt": "Brazilian Portuguese",
            "hi": "Hindi",
            "de": "German",
            "id": "Indonesian",
            "tl": "Filipino",
        }
        return names.get(self._target.lower(), self._target)

    def translate(self, text: str) -> str:
        from .preflight import hydrate_openai_key
        key = hydrate_openai_key()
        if not key:
            raise RuntimeError("thieu OPENAI_API_KEY")
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=key)
        import os
        r = self._client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATE_MODEL") or "gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Translate Chinese dialogue into spoken {self._lang()}. "
                        "Return ONLY the translation, no quotes, no explanation. "
                        "Short interjections (没有, 走, 好) must still be translated."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
        out = (r.choices[0].message.content or "").strip().strip("\"'")
        if not out:
            raise RuntimeError("openai empty translation")
        return out


def _build_translators(source: str, target: str) -> list[tuple[str, object]]:
    """Google -> MyMemory -> OpenAI. Khong giu nguyen tieng Trung khi 1 API loi."""
    from deep_translator import GoogleTranslator

    src = source or "auto"
    gsrc = "zh-CN" if src in ("auto", "zh", "zh-CN", "chinese") else src
    out: list[tuple[str, object]] = [
        ("google", GoogleTranslator(source=gsrc, target=target)),
    ]
    try:
        from deep_translator import MyMemoryTranslator

        mm_src = "zh-CN" if gsrc in ("zh-CN", "zh", "auto") else gsrc
        mm_tgt = {
            "vi": "vi-VN",
            "en": "en-US",
            "ja": "ja-JP",
            "pt": "pt-BR",
            "hi": "hi-IN",
            "de": "de-DE",
            "id": "id-ID",
            "tl": "tl-PH",
        }.get((target or "").lower(), target)
        out.append(("mymemory", MyMemoryTranslator(source=mm_src, target=mm_tgt)))
    except Exception as e:  # noqa: BLE001
        log.warning("MyMemoryTranslator khong dung duoc: %s", e)
    try:
        from .preflight import hydrate_openai_key
        if hydrate_openai_key():
            out.append(("openai", _OpenAILineTranslator(target)))
            log.info("Dich: co OpenAI fallback cho doan Google loi")
    except Exception as e:  # noqa: BLE001
        log.warning("Khong gan OpenAI translator: %s", e)
    return out


def _translate_one(text: str, tr) -> str:
    if not text.strip():
        return ""
    try:
        out = (tr.translate(text) or "").strip()
    except Exception:
        # Google hay fail cum 1-4 chu: them dau cau roi thu lai
        if len(text.strip()) <= 12 and not text.strip().endswith("。"):
            out = (tr.translate(text.strip() + "。") or "").strip().rstrip("。.")
        else:
            raise
    if not out:
        raise RuntimeError("empty translation")
    return out


def _translate_cjk_leftovers(text: str, tr) -> str:
    """Neu con chu Han/Trung trong ban dich, goi lai tung cum. Khong tra ve chu Han."""
    if not _has_cjk(text):
        return text

    def _one(m: re.Match) -> str:
        chunk = m.group(0)
        try:
            out = (_translate_one(chunk, tr) or "").strip()
        except Exception as e:  # noqa: BLE001
            log.warning("Dich cum Han loi: %s | %s", e, chunk[:40])
            return ""
        if not out or _has_cjk(out):
            log.warning("Con chu chua dich sau retry: %s", chunk)
            return ""
        return out

    return _CJK_RE.sub(_one, text)


def _min_translate_ratio(target: str) -> float:
    return float(TRANSLATE_MIN_CHAR_RATIO.get(target, TRANSLATE_MIN_CHAR_RATIO["default"]))


def _translate_validated_one(
    src: str,
    translators: list[tuple[str, object]],
    *,
    target: str,
    min_ratio: float,
) -> tuple[str, int, str]:
    """Dich 1 doan. Retry toi da 2 lan, xoay API. Khong bao gio tra ve chu Han goc."""
    import time

    src = (src or "").strip()
    if not src:
        return "", 0, "empty_src"
    if not translators:
        return "", 0, "fail"
    src_len = len(re.sub(r"\s+", "", src))
    best = ""
    status = "fail"
    retries = 0
    queue = list(translators)
    extra = (TRANSLATE_MAX_RETRIES + 1) - len(queue)
    if extra > 0 and queue:
        queue = queue + [queue[-1]] * extra
    n_try = max(1, len(queue))
    for attempt, (name, tr) in enumerate(queue):
        try:
            vi = _clean_text(_translate_one(src, tr))
            if _has_cjk(vi):
                vi = _clean_text(_translate_cjk_leftovers(vi, tr))
        except Exception as e:  # noqa: BLE001
            log.warning(
                "Dich loi via %s (retry %d/%d): %s | %s",
                name, attempt + 1, n_try, e, src[:50],
            )
            retries = attempt
            if attempt + 1 < n_try:
                from .job_timing import timed_sleep
                timed_sleep(TRANSLATE_RETRY_SLEEP_SEC, "google_translate_retry")
            continue
        dst_len = len(re.sub(r"\s+", "", vi))
        ratio = (dst_len / src_len) if src_len else 1.0
        if _has_cjk(vi):
            status = "cjk"
            best = vi or best
            log.warning(
                "Dich van con chu Han via %s (retry %d/%d): %s",
                name, attempt + 1, n_try, src[:50],
            )
        else:
            ok = bool(vi.strip()) and (src_len < 4 or ratio >= min_ratio)
            if ok:
                if name != "google":
                    log.info("Dich xong via %s: %s -> %s", name, src[:30], vi[:40])
                return vi, attempt, "ok"
            best = vi or best
            status = "short" if vi.strip() else "fail"
            log.warning(
                "Dich doan ngan/rong via %s (ratio %.2f, retry %d/%d): %s",
                name, ratio, attempt + 1, n_try, src[:50],
            )
        retries = attempt
        if attempt + 1 < n_try:
            from .job_timing import timed_sleep
            timed_sleep(TRANSLATE_RETRY_SLEEP_SEC, "google_translate_retry")
    if _has_cjk(best):
        return "", retries, "cjk"
    return best, retries, status


def translate_segments(
    segments: list[dict],
    target="vi",
    source="auto",
    work_dir: Path | str | None = None,
) -> list[dict]:
    import time
    try:
        translators = _build_translators(source, target)
    except ImportError as e:
        raise RuntimeError("Chua cai deep-translator. pip install deep-translator") from e
    min_ratio = _min_translate_ratio(target)
    out: list[dict] = []
    audit_rows: list[dict] = []
    n = len(segments)
    leftover = 0
    fail_n = 0
    for i, seg in enumerate(segments, 1):
        src = seg.get("text", "")
        src_len = len(re.sub(r"\s+", "", src))
        t_one = time.perf_counter()
        vi, retries, status = _translate_validated_one(
            src, translators, target=target, min_ratio=min_ratio,
        )
        from .job_timing import add_bucket
        add_bucket(
            "google_translate_one",
            time.perf_counter() - t_one,
            log_line=False,
            retries=retries,
            status=status,
        )
        if _has_cjk(vi) or status == "cjk":
            leftover += 1
            vi = ""
            status = "cjk"
        if status == "fail":
            fail_n += 1
        dst_len = len(re.sub(r"\s+", "", vi))
        audit_rows.append({
            "i": i - 1,
            "start": round(float(seg.get("start", 0)), 3),
            "end": round(float(seg.get("end", 0)), 3),
            "src_chars": src_len,
            "dst_chars": dst_len,
            "char_ratio": round(dst_len / src_len, 3) if src_len else 0,
            "retries": retries,
            "status": status,
            "source_text": src,
            "translated_text": vi,
        })
        if vi.strip() and not _has_cjk(vi):
            row = {**seg, "text": vi, "source_text": seg.get("source_text") or src}
            out.append(row)
        if i % 50 == 0 or i == n:
            log.info("Dang dich: %d/%d doan...", i, n)
        if i % 20 == 0:
            from .job_timing import timed_sleep
            timed_sleep(0.8, "google_translate_every20")
    if work_dir:
        write_translate_csv(Path(work_dir), audit_rows)
    short_n = sum(1 for r in audit_rows if r["status"] == "short")
    if short_n:
        log.warning("%d doan dich ngan bat thuong sau retry — xem translate_report.csv", short_n)
    dropped = sum(1 for r in audit_rows if r["status"] not in ("ok", "short", "empty_src"))
    if leftover or fail_n or dropped:
        from .preflight import alert_ops
        bad = [r for r in audit_rows if r["status"] in ("fail", "cjk")]
        sample = " | ".join((r.get("source_text") or "")[:40] for r in bad[:5]) or "(khong ro)"
        msg = (
            f"Dich that bai: {leftover} doan con chu Trung/Han, {fail_n} doan khong dich duoc, "
            f"loai {dropped}/{n} doan. Vi du: {sample}"
        )
        alert_ops("translate_fail", msg)
        raise RuntimeError(msg)
    log.info("Dich xong: %d doan tieng Viet", len(out))
    return out


def _whisper_with_quality(audio, cfg_auto, progress=None, audio_dur=None) -> list[dict]:
    """Whisper 1 lan; neu raw bi lap thi tu chay lai (VAD + penalty chat) — user van chi bam 1 lan."""
    model_size = cfg_auto.get("whisper_model", "medium")
    source_lang = (cfg_auto.get("source_lang", "zh") or None)
    compute_type = cfg_auto.get("compute_type", "int8")
    vad = cfg_auto.get("vad", WHISPER_VAD_DEFAULT)
    if isinstance(vad, str):
        vad = vad.lower() in ("1", "true", "yes", "on")
    allow_retry = bool(cfg_auto.get("retry_on_loop", True))

    segments, had_loops = transcribe_segments(
        audio,
        model_size=model_size,
        source_lang=source_lang,
        compute_type=compute_type,
        vad=vad,
        repetition_penalty=float(cfg_auto.get("repetition_penalty", 1.2)),
        no_repeat_ngram_size=int(cfg_auto.get("no_repeat_ngram_size", 3)),
        compression_ratio_threshold=float(cfg_auto.get("compression_ratio_threshold", 2.0)),
        progress=progress,
        audio_dur=audio_dur,
    )
    if had_loops and allow_retry:
        log.warning("Tu chay lai Whisper (chat hon) de tranh video lap chu — khong can user bam lai")
        segments2, had2 = transcribe_segments(
            audio,
            model_size=model_size,
            source_lang=source_lang,
            compute_type=compute_type,
            vad=True,
            repetition_penalty=float(cfg_auto.get("retry_repetition_penalty", 1.35)),
            no_repeat_ngram_size=int(cfg_auto.get("retry_no_repeat_ngram_size", 4)),
            compression_ratio_threshold=float(cfg_auto.get("retry_compression_ratio_threshold", 1.8)),
        )
        if segments2 and (not had2 or len(segments2) >= max(1, len(segments) // 2)):
            # uu tien ban retry neu het lap, hoac van co noi dung hop ly
            if not had2 or not segments:
                log.info("Dung ban Whisper retry (had_loops=%s)", had2)
                return segments2
            # so sanh do lap sau clean
            if _loop_ratio(" ".join(s["text"] for s in segments2)) <= _loop_ratio(
                " ".join(s["text"] for s in segments)
            ):
                log.info("Dung ban Whisper retry (it lap hon)")
                return segments2
        log.info("Giu ban Whisper dau (retry khong tot hon)")
    return segments


def auto_script(video, work_dir, cfg_auto, progress=None) -> tuple[str, list[dict]]:
    """Lay script + timestamp. Uu tien phu de Douyin day du, fallback Whisper."""
    work_dir = Path(work_dir)
    video_dur = probe_duration(video)
    segments = None
    min_cov = float(cfg_auto.get("min_sub_coverage", 0.35))

    if cfg_auto.get("prefer_platform_subs", True):
        segments = try_load_platform_subs(
            work_dir / "raw", video_dur=video_dur, min_coverage=min_cov,
        )
        if segments and segments_look_loopy(segments):
            log.warning("Phu de platform bi lap — chuyen sang Whisper")
            segments = None

    whisper_source = "platform_subs"
    if not segments:
        if progress:
            progress("transcribing", 33, "Tach am thanh de Whisper...")
        audio = extract_audio(video, work_dir / "audio16k.wav")
        audio_dur = probe_duration(audio) or video_dur
        segments = _whisper_with_quality(
            audio, cfg_auto, progress=progress, audio_dur=audio_dur,
        )
        whisper_source = "whisper"

    if not segments:
        raise RuntimeError(
            "Khong nghe/doc duoc loi thoai. Clip co the chi co nhac / khong co phu de."
        )

    before = len(segments)
    segments = dedupe_segments(segments)
    if len(segments) != before:
        log.info("Dedupe goc: %d -> %d doan", before, len(segments))

    from .dub_merge import group_utterances

    grouped = group_utterances(segments)
    if len(grouped) != len(segments):
        log.info("Gop cau goc truoc khi dich: %d -> %d doan", len(segments), len(grouped))
    segments = grouped

    write_transcribe_report(work_dir, segments, video_dur, source=whisper_source)

    raw_text = " ".join(s["text"] for s in segments)
    if bool(cfg_auto.get("defer_translate")):
        vi_segments = []
        for s in segments:
            src = str(s.get("source_text") or s.get("text") or "").strip()
            vi_segments.append({**s, "text": src, "source_text": src})
        log.info(
            "Defer dich: %d doan ZH — khoa xung ho + dich budget sau assign_cast",
            len(vi_segments),
        )
    else:
        vi_segments = translate_segments(
            segments,
            target=cfg_auto.get("translate_to", "vi"),
            source=cfg_auto.get("translate_from", "zh-CN"),
            work_dir=work_dir,
        )
        vi_segments = dedupe_segments(vi_segments)
        merge_gap = DUB_GROUP_MAX_GAP_SEC
        merge_dur = DUB_GROUP_MAX_DUR_SEC
        merge_chars = DUB_GROUP_MAX_CHARS
        raw_chars = cfg_auto.get("merge_max_chars")
        raw_dur = cfg_auto.get("merge_max_dur")
        raw_gap = cfg_auto.get("merge_gap_sec")
        if raw_chars is not None and int(raw_chars) != int(merge_chars):
            log.info(
                "Bo qua merge_max_chars=%s (config cu) — dung %d",
                raw_chars, merge_chars,
            )
        if raw_dur is not None and float(raw_dur) < 5.0:
            log.info("Bo qua merge_max_dur=%s (config cu) — dung %.1f", raw_dur, merge_dur)
        if raw_gap is not None and float(raw_gap) < 0.45:
            log.info("Bo qua merge_gap_sec=%s (config cu) — dung %.2f", raw_gap, merge_gap)
        vi_segments = group_utterances(
            vi_segments,
            max_gap=merge_gap,
            max_dur=merge_dur,
            max_chars=merge_chars,
        )
        vi_segments = dedupe_segments(vi_segments)
        if not vi_segments:
            raise RuntimeError("Dich xong nhung khong co doan hop le.")

        from .translate_llm import maybe_rewrite
        vi_segments = maybe_rewrite(vi_segments, cfg_auto)

        # an toan cuoi: neu van con lap sau dich thi chat lai text (khong de xuat video loi)
        fixed = []
        for s in vi_segments:
            t = collapse_loop_text(s["text"], max_repeat=1)
            if t and not _is_junk_segment(t):
                row = {**s, "text": t}
                if s.get("source_text"):
                    row["source_text"] = s["source_text"]
                fixed.append(row)
        vi_segments = dedupe_segments(fixed) if fixed else vi_segments
        leftover_n = sum(1 for s in vi_segments if _has_cjk(s.get("text") or ""))
        if leftover_n:
            log.warning("Con %d doan sot Han — dich lai Google, bo neu van loi", leftover_n)
            try:
                from deep_translator import GoogleTranslator

                tr = GoogleTranslator(
                    source=str(cfg_auto.get("translate_from") or "zh-CN"),
                    target=str(cfg_auto.get("translate_to") or "vi"),
                )
                for s in vi_segments:
                    if not _has_cjk(s.get("text") or ""):
                        continue
                    src = (s.get("source_text") or s.get("text") or "").strip()
                    if not src:
                        continue
                    try:
                        s["text"] = tr.translate(src)
                    except Exception as e:  # noqa: BLE001
                        log.warning("Google leftover fail: %s", e)
            except Exception as e:  # noqa: BLE001
                log.warning("Khong Google duoc leftover: %s", e)
            dropped = sum(1 for s in vi_segments if _has_cjk(s.get("text") or ""))
            if dropped:
                log.warning("Bo %d doan van sot Han (khong TTS chu Trung)", dropped)
            vi_segments = [
                s for s in vi_segments
                if (s.get("text") or "").strip() and not _has_cjk(s.get("text") or "")
            ]
            if not vi_segments:
                raise RuntimeError("Dich xong nhung khong con doan Viet hop le.")
        if segments_look_loopy(vi_segments, ratio=0.5):
            log.warning("Van con dau hieu lap sau xu ly — da collapse toi da truoc khi TTS")

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
