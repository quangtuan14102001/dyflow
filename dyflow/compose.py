"""Buoc 4: Ghep tat ca + export da ty le.

Nhan video da transform + audio long tieng + (caption) + (nhac nen),
xuat ra cac ty le yeu cau (9:16, 1:1, 16:9).
"""
from __future__ import annotations
import re
import textwrap
from pathlib import Path

from .utils import log, run, probe_duration, probe_video_size
from . import audio_fx


ASPECT = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


def _caption_layout(cfg_cap: dict) -> tuple[int, int]:
    return (
        int(cfg_cap.get("chars_per_line", 24)),
        int(cfg_cap.get("max_lines", 2)),
    )


def _join_words(words: list[dict]) -> str:
    return " ".join(w["text"].strip() for w in words if w["text"].strip())


def _wrap_lines(text: str, chars_per_line: int, max_lines: int) -> str:
    lines = textwrap.wrap(text, width=chars_per_line) or [text]
    return "\\N".join(lines[:max_lines])


def _chunk_text(text: str, chars_per_line: int, max_lines: int) -> list[str]:
    """Gom text thanh cac khoi toi da max_lines dong (khong can dau cau)."""
    tokens = re.findall(r"\S+|\s+", text)
    chunks: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        joined = "".join(buf).strip()
        if joined:
            chunks.append(joined)
        buf.clear()

    for tok in tokens:
        trial = "".join(buf + [tok]).strip()
        if trial and len(textwrap.wrap(trial, width=chars_per_line)) > max_lines and buf:
            flush()
            buf = [tok]
        else:
            buf.append(tok)
    flush()
    return chunks or [text.strip()]


def _segments_to_ass_cues(
    segments: list[dict],
    chars_per_line: int,
    max_lines: int,
) -> list[dict]:
    """Chuyen doan Whisper (co start/end goc) thanh cue phu de 2 dong."""
    cues: list[dict] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        start, end = float(seg["start"]), float(seg["end"])
        chunks = _chunk_text(text, chars_per_line, max_lines)
        if len(chunks) == 1:
            cues.append({"text": chunks[0], "start": start, "end": end})
            continue
        slot = max(0.2, end - start)
        per = slot / len(chunks)
        for i, chunk in enumerate(chunks):
            cues.append({
                "text": chunk,
                "start": start + i * per,
                "end": start + (i + 1) * per,
            })
    return cues


def _group_words_into_cues(
    words: list[dict],
    chars_per_line: int,
    max_lines: int,
) -> list[dict]:
    """Nhom tu edge-tts thanh cue 2 dong, dong bo thoi gian noi."""
    if not words:
        return []

    cues: list[dict] = []
    buf: list[dict] = []

    def flush() -> None:
        if not buf:
            return
        cues.append({
            "text": _join_words(buf),
            "start": buf[0]["start"],
            "end": buf[-1]["end"],
        })
        buf.clear()

    for w in words:
        trial = _join_words(buf + [w])
        if (
            trial
            and len(textwrap.wrap(trial, width=chars_per_line)) > max_lines
            and buf
        ):
            flush()
            buf = [w]
        else:
            buf.append(w)
    flush()
    return cues


def _font_ass_name(font_path: str) -> str:
    if not font_path:
        return "Arial"
    name = Path(font_path).stem
    if "Arial Unicode" in name:
        return "Arial Unicode MS"
    return name


def _ass_header(cfg_cap: dict, font_path: str, play_w: int, play_h: int) -> str:
    fs = cfg_cap.get("font_size", 56)
    primary = cfg_cap.get("primary_color", "&H00FFFFFF")
    outline_c = cfg_cap.get("outline_color", "&H00000000")
    outline = cfg_cap.get("outline", 3)
    margin_v = cfg_cap.get("margin_v", 220)
    font_name = cfg_cap.get("font_name") or _font_ass_name(font_path)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{fs},{primary},{outline_c},&H66000000,1,0,1,{outline},1,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _write_ass_cues(
    cues: list[dict],
    ass_path: Path,
    cfg_cap: dict,
    font_path: str,
    play_w: int,
    play_h: int,
) -> None:
    chars_per_line, max_lines = _caption_layout(cfg_cap)
    lines = []
    for cue in cues:
        display = _wrap_lines(cue["text"], chars_per_line, max_lines)
        lines.append(
            f"Dialogue: 0,{_ts(cue['start'])},{_ts(cue['end'])},"
            f"Default,,0,0,0,,{display}"
        )
    ass_path.write_text(
        _ass_header(cfg_cap, font_path, play_w, play_h) + "\n".join(lines),
        encoding="utf-8",
    )


def _write_ass(script: str, ass_path: Path, cfg_cap: dict, font_path: str,
               video_dur: float, play_w: int, play_h: int) -> None:
    """Fallback: chia script thanh khoi 2 dong, rai deu theo thoi luong."""
    chars_per_line, max_lines = _caption_layout(cfg_cap)
    chunks = _chunk_text(script, chars_per_line, max_lines)
    per = video_dur / len(chunks)

    lines = []
    for i, chunk in enumerate(chunks):
        display = _wrap_lines(chunk, chars_per_line, max_lines)
        lines.append(
            f"Dialogue: 0,{_ts(i * per)},{_ts((i + 1) * per)},"
            f"Default,,0,0,0,,{display}"
        )
    ass_path.write_text(
        _ass_header(cfg_cap, font_path, play_w, play_h) + "\n".join(lines),
        encoding="utf-8",
    )


def _speech_gate_volume_expr(
    segments: list[dict] | None,
    *,
    ambient: float = 0.85,
    during_speech: float = 0.0,
    pad_before: float = 0.35,
    pad_after: float = 0.15,
) -> str:
    """Giam am goc CHI trong khung thoai; giua cac cau giu am thanh xung quanh.

    pad_before lon hon de kat het phan loi Trung o dau cau.
    """
    ambient = max(0.0, min(2.0, float(ambient)))
    during = max(0.0, min(1.0, float(during_speech)))
    pad_b = max(0.0, float(pad_before))
    pad_a = max(0.0, float(pad_after))
    if not segments:
        return f"{ambient:.4f}"

    windows: list[tuple[float, float]] = []
    for seg in segments:
        try:
            s = max(0.0, float(seg["start"]) - pad_b)
            e = float(seg["end"]) + pad_a
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            windows.append((s, e))
    if not windows:
        return f"{ambient:.4f}"

    windows.sort()
    merged = [list(windows[0])]
    for s, e in windows[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    expr = f"{ambient:.4f}"
    for s, e in reversed(merged):
        expr = f"if(between(t\\,{s:.3f}\\,{e:.3f})\\,{during:.4f}\\,{expr})"
    return expr


def _video_fit_filter(fit: str, W: int, H: int) -> str:
    """fit: source | contain | cover. source = khong scale, giu dung pixel goc."""
    if fit == "source":
        return "[0:v]null[base]"
    if fit == "contain":
        return (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[base]"
        )
    return (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1[base]"
    )


def _ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def compose(video: str | Path, voice: str | Path | None, script: str,
            out_dir: str | Path, base_name: str, cfg: dict,
            music: str | None,
            word_timings: list[dict] | None = None,
            segment_cues: list[dict] | None = None,
            sync_to_video: bool = False) -> list[Path]:
    video, out_dir = Path(video), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    cfg_vo = cfg.get("voiceover", {})
    cfg_cap = cfg.get("caption", {})
    cfg_mus = cfg.get("music", {})
    cfg_exp = cfg.get("export", {})
    font_path = cfg.get("paths", {}).get("font_path", "")
    export_fit = str(cfg_exp.get("fit", "cover")).lower()
    src_w, src_h = probe_video_size(video)

    video_dur = probe_duration(video)
    dur = video_dur if sync_to_video else (probe_duration(voice) if voice else video_dur)

    # Tach giong goc (Demucs) -> chi giu nhac/FX
    ambient_bed = None
    strip_vocals = bool(cfg_vo.get("strip_original_vocals", True))
    if voice and not cfg_vo.get("mute_original", True) and strip_vocals:
        work_audio = Path(video).resolve().parent / "audio_fx"
        ambient_bed = audio_fx.make_ambient_bed(
            video, work_audio,
            enabled=True,
            model=str(cfg_vo.get("demucs_model", "htdemucs")),
        )

    # tao caption ASS 1 lan (PlayRes khop khung hinh xuat)
    ass_path = None
    if cfg_cap.get("enabled") and script and script != "AUTO":
        ass_path = out_dir / f"{base_name}.ass"
        chars_per_line, max_lines = _caption_layout(cfg_cap)
        if export_fit == "source":
            cap_w, cap_h = src_w, src_h
        else:
            cap_w, cap_h = ASPECT.get(cfg_exp.get("formats", ["9:16"])[0], (1080, 1920))
        if segment_cues:
            cues = _segments_to_ass_cues(segment_cues, chars_per_line, max_lines)
            _write_ass_cues(cues, ass_path, cfg_cap, font_path, cap_w, cap_h)
            log.info("Caption dong bo tieng goc: %d cue (%d doan)", len(cues), len(segment_cues))
        elif word_timings:
            cues = _group_words_into_cues(word_timings, chars_per_line, max_lines)
            _write_ass_cues(cues, ass_path, cfg_cap, font_path, cap_w, cap_h)
            log.info("Caption dong bo TTS: %d cue (toi da %d dong/cue)", len(cues), max_lines)
        else:
            _write_ass(script, ass_path, cfg_cap, font_path, dur, cap_w, cap_h)

    for fmt in cfg_exp.get("formats", ["9:16"]):
        if fmt == "source":
            W, H = src_w, src_h
            fit = "source"
            out_path = out_dir / f"{base_name}.mp4"
        else:
            W, H = ASPECT.get(fmt, (1080, 1920))
            fit = export_fit
            out_path = out_dir / f"{base_name}_{fmt.replace(':', 'x')}.mp4"

        # --- build filter_complex ---
        vfilters = [_video_fit_filter(fit, W, H)]
        last_v = "[base]"
        if ass_path:
            ass_abs = str(Path(ass_path).resolve())
            esc = ass_abs.replace("\\", "\\\\").replace(":", "\\:")
            font_dir = str(Path(font_path).resolve().parent) if font_path else ""
            if font_dir:
                fdir = font_dir.replace("\\", "\\\\").replace(":", "\\:")
                vfilters.append(f"{last_v}ass=filename={esc}:fontsdir={fdir}[vsub]")
            else:
                vfilters.append(f"{last_v}ass=filename={esc}[vsub]")
            last_v = "[vsub]"

        cmd = ["ffmpeg", "-y"]
        # mode lien tuc: loop video cho du narration; mode segment: giu video goc
        if voice and not sync_to_video:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", str(video)]
        ai = 1
        voice_idx = music_idx = ambient_idx = None
        if ambient_bed is not None:
            cmd += ["-i", str(ambient_bed)]
            ambient_idx = ai
            ai += 1
        if voice:
            cmd += ["-i", str(voice)]
            voice_idx = ai; ai += 1
        if music and cfg_mus.get("enabled"):
            cmd += ["-stream_loop", "-1", "-i", str(music)]
            music_idx = ai; ai += 1

        # --- audio mix ---
        # UU TIEN: ambient_bed (da bo giong Trung). Fallback: gate theo segment.
        afilters = []
        dub_vol = float(cfg_vo.get("dub_volume", 3.2))
        ambient = float(cfg_vo.get("ambient_volume", cfg_vo.get("original_volume", 0.9)))
        speech_gate = float(cfg_vo.get("speech_gate_volume", 0.0))
        pad_before = float(cfg_vo.get("speech_gate_pad_before", cfg_vo.get("speech_gate_pad_sec", 0.40)))
        pad_after = float(cfg_vo.get("speech_gate_pad_after", 0.18))
        mute_speech = bool(cfg_vo.get("mute_original_speech", cfg_vo.get("preserve_fx", True)))
        has_orig = not cfg_vo.get("mute_original", True)
        audio_out = None

        bed_label = f"[{ambient_idx}:a]" if ambient_idx is not None else "[0:a]"

        if voice_idx is not None and has_orig:
            if ambient_idx is not None:
                afilters.append(f"{bed_label}volume={ambient}[ao]")
                log.info("Mix: ambient KHONG giong (Demucs) vol=%.2f + dub vol=%.2f", ambient, dub_vol)
            elif mute_speech and segment_cues:
                vol_expr = _speech_gate_volume_expr(
                    segment_cues,
                    ambient=ambient,
                    during_speech=speech_gate,
                    pad_before=pad_before,
                    pad_after=pad_after,
                )
                afilters.append(f"[0:a]volume='{vol_expr}':eval=frame[ao]")
                log.info(
                    "Fallback gate thoai (vol nen=%.2f, pad -%.2f/+%.2fs) %d khung",
                    ambient, pad_before, pad_after, len(segment_cues),
                )
            else:
                afilters.append(f"[0:a]volume={ambient}[ao]")
            afilters.append(f"[{voice_idx}:a]volume={dub_vol}[vo]")
            afilters.append(
                "[ao][vo]amix=inputs=2:duration=first:dropout_transition=0:"
                "normalize=0:weights=1 1.8[aout]"
            )
            audio_out = "[aout]"
        elif voice_idx is not None:
            afilters.append(f"[{voice_idx}:a]volume={dub_vol}[vo]")
            audio_out = "[vo]"
        elif has_orig:
            src = bed_label if ambient_idx is not None else "[0:a]"
            afilters.append(f"{src}volume={ambient}[ao]")
            audio_out = "[ao]"

        if music_idx is not None and audio_out:
            mv = float(cfg_mus.get("volume", 0.12))
            afilters.append(f"[{music_idx}:a]volume={mv}[mu]")
            afilters.append(f"{audio_out}[mu]amix=inputs=2:duration=longest:normalize=0[aout2]")
            audio_out = "[aout2]"

        filter_parts = vfilters + afilters
        map_args = ["-map", last_v]
        if audio_out:
            map_args += ["-map", audio_out]

        cmd += ["-filter_complex", ";".join(filter_parts)]
        cmd += map_args
        cmd += ["-t", f"{dur:.2f}"]
        cmd += [
            "-c:v", "libx264",
            "-preset", cfg_exp.get("preset", "medium"),
            "-crf", str(cfg_exp.get("crf", 20)),
            "-c:a", "aac", "-b:a", "160k",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        run(cmd)
        log.info("Export [%s, fit=%s, %dx%d]: %s", fmt, fit, W, H, out_path.name)
        outputs.append(out_path)

    return outputs
