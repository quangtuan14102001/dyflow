"""Buoc 4: Ghep tat ca + export da ty le.

Nhan video da transform + audio long tieng + (caption) + (nhac nen),
xuat ra cac ty le yeu cau (9:16, 1:1, 16:9).
"""
from __future__ import annotations
import re
import textwrap
from pathlib import Path

from .utils import log, run, probe_duration, probe_video_size, verify_output_mp4
from . import audio_fx
from .export_style import (
    apply_video_speed,
    dialogue_override,
    logo_overlay_xy,
    resolve_ass_style,
    style_line,
)


ASPECT = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


def _ffmpeg_filter_path(p: str | Path) -> str:
    """FFmpeg filter path: forward slashes, escape drive colon (Windows)."""
    return Path(p).resolve().as_posix().replace(":", r"\:")


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
    # Chong chong timestamp (prosody co the day start/end lech)
    normed: list[dict] = []
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg["start"])
        end = float(seg["end"])
        if i + 1 < len(segments):
            end = min(end, float(segments[i + 1]["start"]) - 0.02)
        end = max(start + 0.18, end)
        normed.append({**seg, "text": text, "start": start, "end": end})

    cues: list[dict] = []
    for seg in normed:
        text = seg["text"]
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
    st = resolve_ass_style(
        cfg_cap, font_path, play_w, play_h,
        font_ass_name=_font_ass_name(font_path),
    )
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line(st)}

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
    ov = dialogue_override(cfg_cap, play_w, play_h)
    lines = []
    for cue in cues:
        display = _wrap_lines(cue["text"], chars_per_line, max_lines)
        lines.append(
            f"Dialogue: 0,{_ts(cue['start'])},{_ts(cue['end'])},"
            f"Default,,0,0,0,,{ov}{display}"
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
    ov = dialogue_override(cfg_cap, play_w, play_h)

    lines = []
    for i, chunk in enumerate(chunks):
        display = _wrap_lines(chunk, chars_per_line, max_lines)
        lines.append(
            f"Dialogue: 0,{_ts(i * per)},{_ts((i + 1) * per)},"
            f"Default,,0,0,0,,{ov}{display}"
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
        if seg.get("tts_silence_fallback") or "tts_silence_fallback" in (seg.get("qa_flags") or []):
            continue
        try:
            s = max(0.0, float(seg["start"]) - pad_b)
            e = float(seg.get("display_end") or seg["end"]) + pad_a
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
            sync_to_video: bool = False,
            ambient_bed: Path | None = None) -> list[Path]:
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

    # Tach giong goc (Demucs): INPUT la video goc (raw/edited), KHONG dung voice.mp3.
    # voice chi la dieu kien "co ban dub thi moi tach nen".
    strip_vocals = bool(cfg_vo.get("strip_original_vocals", True))
    if voice and not cfg_vo.get("mute_original", True) and strip_vocals:
        if ambient_bed is None:
            work_audio = Path(video).resolve().parent / "audio_fx"
            from .job_timing import step as timing_step
            with timing_step("Demucs"):
                ambient_bed = audio_fx.make_ambient_bed(
                    video, work_audio,
                    enabled=True,
                    model=str(cfg_vo.get("demucs_model", "htdemucs")),
                )
        else:
            from .job_timing import note as timing_note
            timing_note("Demucs reused (chay song song sau Download)", path=str(ambient_bed))

    # Che mo chu phu de goc cua clip:
    # Neu anti_copyright bat: lay theo ac_cfg.get("blur_sub", True) (mac dinh True).
    ac_cfg = cfg_exp.get("anti_copyright") or {}
    ac_enabled = bool(ac_cfg.get("enabled", False))

    if ac_enabled:
        blur_sub_on = bool(ac_cfg.get("blur_sub", True))
        blur_sub_mode = str(ac_cfg.get("blur_sub_mode") or "auto_ai").lower()
    elif "blur_sub" in ac_cfg:
        blur_sub_on = bool(ac_cfg.get("blur_sub"))
        blur_sub_mode = str(ac_cfg.get("blur_sub_mode") or "auto_ai").lower()
    else:
        blur_sub_on = bool(
            cfg.get("caption", {}).get(
                "blur_burned_subs",
                cfg.get("transform", {}).get("blur_burned_subs", {}).get("enabled", True),
            )
        )
        blur_sub_mode = str(
            cfg.get("caption", {}).get("blur_sub_mode")
            or cfg.get("transform", {}).get("blur_burned_subs", {}).get("mode")
            or "auto_ai"
        ).lower()

    active_video = video
    ocr_done = False
    ocr_meta: dict = {}
    if blur_sub_on and blur_sub_mode in ("ocr", "auto_ai", "ai"):
        from .hardsub import blur_hardsubs_ocr
        from .job_timing import step as timing_step
        ocr_out = Path(video).resolve().parent / f"{Path(video).stem}_ocrblur.mp4"
        try:
            with timing_step("OCR Blur"):
                ocr_cfg = dict(cfg.get("transform", {}).get("blur_burned_subs", {}) or {})
                active_video = blur_hardsubs_ocr(video, ocr_out, ocr_cfg, meta_out=ocr_meta)
                ocr_done = True
                log.info(
                    "AI OCR da tu dong tim va che mo phu de: %s (y_center=%s, margin_v=%s)",
                    active_video.name, ocr_meta.get("y_center"), ocr_meta.get("margin_v"),
                )
        except Exception as e:
            log.warning("AI OCR che phu de gap loi (%s), fallback ve che dai duoi day: %s", blur_sub_mode, e)
            blur_sub_mode = "bottom"

    # Xac dinh do phan giai canvas cho caption ASS
    if export_fit == "source":
        cap_w, cap_h = src_w, src_h
    else:
        cap_w, cap_h = ASPECT.get(cfg_exp.get("formats", ["9:16"])[0], (1080, 1920))

    # Neu AI OCR phat hien dong chu phu de goc -> can vi tri Sub tieng Viet vao dung tam vet mo!
    if ocr_done and ocr_meta.get("detected") and ocr_meta.get("y_center"):
        scale_y = cap_h / max(1, src_h)
        scaled_y_center = int(ocr_meta["y_center"] * scale_y)
        # ASS Alignment=2: MarginV tinh tu day len mep duoi dong chu.
        # De tam dong chu trung voi tam vet mo:
        fs_est = int(cfg_cap.get("fontsize") or round(cap_h * 0.028))
        target_mv = (cap_h - scaled_y_center) - int(fs_est * 0.60)
        cfg_cap["margin_v"] = max(35, min(int(cap_h * 0.85), target_mv))
        log.info(
            "AI OCR can toa do Sub tieng Viet: MarginV=%d (y_center=%d scaled=%d canvas=%dx%d fs=%d)",
            cfg_cap["margin_v"], ocr_meta.get("y_center", 0), scaled_y_center, cap_w, cap_h, fs_est,
        )

    # tao caption ASS 1 lan (PlayRes khop khung hinh xuat)
    ass_path = None
    if cfg_cap.get("enabled") and script and script != "AUTO":
        ass_path = out_dir / f"{base_name}.ass"
        chars_per_line, max_lines = _caption_layout(cfg_cap)
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

        # Anti-copyright: bien doi hinh anh goc (truoc khi burn-in sub/logo)
        if ac_enabled:
            if ac_cfg.get("mirror"):
                vfilters.append(f"{last_v}hflip[vac_flip]")
                last_v = "[vac_flip]"
            if ac_cfg.get("micro_zoom"):
                vfilters.append(
                    f"{last_v}crop=w=iw/1.04:h=ih/1.04:x=(iw-ow)/2:y=(ih-oh)/2,"
                    f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[vac_zoom]"
                )
                last_v = "[vac_zoom]"

        # 1. Che mo chu phu de goc cua clip (boxblur + nhe mau toi de chu Trung khong bi lo)
        if blur_sub_on:
            if ocr_done:
                # Da duoc AI OCR phat hien va che mo dung toa do tren active_video
                pass
            elif blur_sub_mode == "center":
                # Che mo vung chu o giua man hinh (gon vung chu, khong keo dai tu trai sang phai)
                is_landscape = W >= H
                w_ratio = 0.55 if is_landscape else 0.70
                vfilters.append(
                    f"{last_v}split=2[vac_main][vac_crop];"
                    f"[vac_crop]crop=w=iw*{w_ratio:.3f}:h=ih*0.11:x=(iw-ow)/2:y=ih*0.43,gblur=sigma=12,drawbox=y=0:h=ih:color=black@0.25:t=fill[vac_blur];"
                    f"[vac_main][vac_blur]overlay=(W-w)/2:H*0.43[vac_sub_blurred]"
                )
                last_v = "[vac_sub_blurred]"
            else:
                # Che mo vung phu de o day clip:
                is_landscape = W >= H
                if blur_sub_mode in ("bottom_full", "full"):
                    # Dai ngang toan man hinh (tu trai sang phai)
                    w_ratio = 1.0
                    h_ratio = 0.085 if is_landscape else 0.095
                    y_ratio = 0.875 if is_landscape else 0.815
                elif blur_sub_mode in ("bottom_large", "large"):
                    w_ratio = 0.65 if is_landscape else 0.80
                    h_ratio = 0.14 if is_landscape else 0.15
                    y_ratio = 0.81 if is_landscape else 0.78
                elif blur_sub_mode in ("bottom_medium", "medium"):
                    w_ratio = 0.58 if is_landscape else 0.72
                    h_ratio = 0.11 if is_landscape else 0.12
                    y_ratio = 0.84 if is_landscape else 0.80
                else:
                    # Mac dinh (bottom): Chi che dung vung chu phu de o giua, khong che dai tu mep trai sang mep phai
                    w_ratio = 0.52 if is_landscape else 0.65
                    h_ratio = 0.085 if is_landscape else 0.095
                    y_ratio = 0.875 if is_landscape else 0.815

                if w_ratio >= 0.999:
                    crop_flt = f"crop=iw:ih*{h_ratio:.3f}:0:ih*{y_ratio:.3f}"
                    over_flt = f"overlay=0:H*{y_ratio:.3f}"
                else:
                    crop_flt = f"crop=w=iw*{w_ratio:.3f}:h=ih*{h_ratio:.3f}:x=(iw-ow)/2:y=ih*{y_ratio:.3f}"
                    over_flt = f"overlay=(W-w)/2:H*{y_ratio:.3f}"

                vfilters.append(
                    f"{last_v}split=2[vac_main][vac_crop];"
                    f"[vac_crop]{crop_flt},gblur=sigma=12,drawbox=y=0:h=ih:color=black@0.25:t=fill[vac_blur];"
                    f"[vac_main][vac_blur]{over_flt}[vac_sub_blurred]"
                )
                last_v = "[vac_sub_blurred]"

        if ac_enabled:
            if ac_cfg.get("color_shift"):
                vfilters.append(f"{last_v}eq=contrast=1.04:saturation=1.06:gamma=0.98[vac_col]")
                last_v = "[vac_col]"
            if ac_cfg.get("vignette"):
                vfilters.append(f"{last_v}vignette=PI/4[vac_vig]")
                last_v = "[vac_vig]"

        # 2. Sau khi che mo chu phu de goc xong -> moi them phu de tieng cua tool vao de tren
        if ass_path:
            esc = _ffmpeg_filter_path(ass_path)
            font_dir = str(Path(font_path).resolve().parent) if font_path else ""
            if font_dir:
                fdir = _ffmpeg_filter_path(font_dir)
                vfilters.append(f"{last_v}ass=filename='{esc}':fontsdir='{fdir}'[vsub]")
            else:
                vfilters.append(f"{last_v}ass=filename='{esc}'[vsub]")
            last_v = "[vsub]"

        cmd = ["ffmpeg", "-y"]
        # mode lien tuc: loop video cho du narration; mode segment: giu video goc
        if voice and not sync_to_video:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", str(active_video)]
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

        logo_idx = None
        logo_raw = str(cfg_exp.get("logo_path") or "").strip()
        logo_file = Path(logo_raw).expanduser() if logo_raw else None
        if logo_file is not None and logo_file.is_file():
            cmd += ["-i", str(logo_file)]
            logo_idx = ai
            ai += 1
            pct = float(cfg_exp.get("logo_size_percent") or 10.0)
            pct = max(2.0, min(40.0, pct))
            lw = max(8, int(round(W * pct / 100.0)))
            xy = logo_overlay_xy(str(cfg_exp.get("logo_position") or "top_right"))
            vfilters.append(f"[{logo_idx}:v]scale={lw}:-1,format=rgba[lg]")
            vfilters.append(f"{last_v}[lg]overlay={xy}:format=auto[vlogo]")
            last_v = "[vlogo]"
            log.info("Logo overlay %s size=%dpx pos=%s", logo_file.name, lw, xy)

        # Anti-copyright: phu hat film grain sau cung (doi 100% hash toan bo khung hinh)
        if ac_enabled and ac_cfg.get("noise"):
            vfilters.append(f"{last_v}noise=alls=2:allf=t+u[vac_noise]")
            last_v = "[vac_noise]"

        # --- audio mix ---
        # UU TIEN: ambient_bed (da bo giong Trung). Fallback: gate theo segment.
        afilters = []
        dub_vol = float(cfg_vo.get("dub_volume", 5.0))
        ambient = float(cfg_vo.get("ambient_volume", cfg_vo.get("original_volume", 0.38)))
        speech_gate = float(cfg_vo.get("speech_gate_volume", 0.0))
        pad_before = float(cfg_vo.get("speech_gate_pad_before", cfg_vo.get("speech_gate_pad_sec", 0.40)))
        pad_after = float(cfg_vo.get("speech_gate_pad_after", 0.18))
        mute_speech = bool(cfg_vo.get("mute_original_speech", cfg_vo.get("preserve_fx", True)))
        has_orig = not cfg_vo.get("mute_original", True)
        audio_out = None

        bed_label = f"[{ambient_idx}:a]" if ambient_idx is not None else "[0:a]"

        # Anti-copyright audio: dich pitch +5.0% + stereo widening + EQ de pha vo acoustic fingerprint
        if ac_enabled and ac_cfg.get("pitch_shift") and has_orig:
            ambient = min(ambient, 0.30)
            pitch_flt = (
                "aformat=channel_layouts=stereo,"
                "asetrate=44100*1.050,aresample=44100,atempo=0.952380952,"
                "stereowiden=delay=20:feedback=0.3:crossfeed=0.3:drymix=0.8,"
                "highpass=f=48,lowpass=f=15500,equalizer=f=3150:t=q:w=1.6:g=-2.5"
            )
            afilters.append(f"{bed_label}{pitch_flt}[abed_pitch]")
            bed_label = "[abed_pitch]"
            log.info("Anti-copyright audio: Pitch Shift +5.0%%, Stereo Widening & Notch EQ da kich hoat")

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
                afilters.append(f"{bed_label}volume='{vol_expr}':eval=frame[ao]")
                log.info(
                    "Fallback gate thoai (vol nen=%.2f, pad -%.2f/+%.2fs) %d khung",
                    ambient, pad_before, pad_after, len(segment_cues),
                )
            else:
                afilters.append(f"{bed_label}volume={ambient}[ao]")
            # Tang ro giong noi + nhe limit de khong bi BGM Douyin at
            afilters.append(
                f"[{voice_idx}:a]volume={dub_vol},alimiter=limit=0.95:attack=5:release=50[vo]"
            )
            afilters.append(
                "[ao][vo]amix=inputs=2:duration=first:dropout_transition=0:"
                "normalize=0:weights=1 2.4[amixed]"
            )
            # Loudness on dinh giua cac clip (tranh clip nay to clip kia be)
            afilters.append(
                "[amixed]loudnorm=I=-16:TP=-1.5:LRA=11:linear=true[aout]"
            )
            audio_out = "[aout]"
        elif voice_idx is not None:
            afilters.append(
                f"[{voice_idx}:a]volume={dub_vol},alimiter=limit=0.95:attack=5:release=50,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11:linear=true[vo]"
            )
            audio_out = "[vo]"
        elif has_orig:
            src = bed_label
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
        from .hardware import probe_best_h264_encoder, get_cpu_fallback_encoder_args

        cpu_preset = str(cfg_exp.get("preset", "medium"))
        cpu_crf = int(cfg_exp.get("crf", 20))
        cpu_args = get_cpu_fallback_encoder_args(preset=cpu_preset, crf=cpu_crf)

        enc_name, enc_args = probe_best_h264_encoder()
        base_cmd = list(cmd)
        clean_meta_args = [
            "-map_metadata", "-1",
            "-metadata", "title=Video",
            "-metadata", "comment=Dyflow Video Engine",
            "-movflags", "+faststart",
        ]
        cmd_run = base_cmd + enc_args + clean_meta_args + [
            "-c:a", "aac", "-b:a", "160k",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        log.info(
            "Compose+export START [%s fit=%s %dx%d encoder=%s] -> %s",
            fmt, fit, W, H, enc_name, out_path.resolve(),
        )
        try:
            run(cmd_run)
        except Exception as e:
            if enc_name != "libx264":
                log.warning(
                    "Compose encoder %s that bai: %s -> tu dong fallback ve CPU libx264",
                    enc_name, e,
                )
                out_path.unlink(missing_ok=True)
                cmd_cpu = base_cmd + cpu_args + clean_meta_args + [
                    "-c:a", "aac", "-b:a", "160k",
                    "-pix_fmt", "yuv420p",
                    str(out_path),
                ]
                run(cmd_cpu)
            else:
                raise
        verify_output_mp4(out_path, label=f"Compose ffmpeg [{fmt}]")
        # video_speed: pass 2 SAU mix/QA/burn-in. Khong dung atempo per-segment.
        try:
            spd = float(cfg_exp.get("video_speed") or 1.0)
        except (TypeError, ValueError):
            spd = 1.0
        if abs(spd - 1.0) > 1e-3:
            apply_video_speed(out_path, out_path, spd)
            verify_output_mp4(out_path, label=f"Compose video_speed [{fmt}]")
        log.info(
            "Compose+export OK [%s, fit=%s, %dx%d]: %s (%d byte)",
            fmt, fit, W, H, out_path.name, out_path.stat().st_size,
        )
        outputs.append(out_path)

    if not outputs:
        raise RuntimeError("Compose+export: khong tao duoc file output nao")
    return outputs
