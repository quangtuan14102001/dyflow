"""Tuy chinh output o Compose+export: preset ASS, video_speed, logo, vi tri sub.

Khong dung cho Whisper / dich / TTS / QA / atempo per-segment.
Tat ca tham so optional — thieu thi giu dung hanh vi cu.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import log, probe_has_audio, run

# ASS colour: &HAABBGGRR (AA=00 dam, FF trong; BGR khong phai RGB)
PRESET_DEFAULT = "classic"

# Classic khop _ass_header cu (cfg overlay fontsize/outline/margin/color).
PRESETS: dict[str, dict[str, Any]] = {
    "classic": {
        "fontname": "",
        "fontsize": None,  # dung cfg_cap.font_size (mac dinh 56)
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "back_color": "&H66000000",
        "outline": None,  # cfg_cap.outline mac dinh 3
        "shadow": 1,
        "border_style": 1,
        "bold": 1,
    },
    "neon": {
        "fontname": "",
        "fontsize": 36,
        "primary_color": "&H0000FFCC",
        "outline_color": "&H00FF00AA",
        "back_color": "&H80000000",
        "outline": 4,
        "shadow": 0,
        "border_style": 1,
        "bold": 1,
    },
    "minimal": {
        "fontname": "",
        "fontsize": 32,
        "primary_color": "&H00F2F2F2",
        "outline_color": "&H66000000",
        "back_color": "&H00000000",
        "outline": 1,
        "shadow": 0,
        "border_style": 1,
        "bold": 0,
    },
    "bold": {
        "fontname": "",
        "fontsize": 38,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "back_color": "&H90000000",
        "outline": 5,
        "shadow": 2,
        "border_style": 1,
        "bold": 1,
    },
    "mint": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00CCFFCC",
        "outline_color": "&H00335533",
        "back_color": "&H66003322",
        "outline": 3,
        "shadow": 1,
        "border_style": 1,
        "bold": 1,
    },
    "rose": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00E8D0FF",
        "outline_color": "&H00440055",
        "back_color": "&H66002244",
        "outline": 3,
        "shadow": 1,
        "border_style": 1,
        "bold": 1,
    },
    "sky": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00FFE8AA",
        "outline_color": "&H00884400",
        "back_color": "&H66221100",
        "outline": 3,
        "shadow": 1,
        "border_style": 1,
        "bold": 1,
    },
    "sunset": {
        "fontname": "",
        "fontsize": 36,
        "primary_color": "&H0000E0FF",
        "outline_color": "&H00003388",
        "back_color": "&H66001144",
        "outline": 3,
        "shadow": 1,
        "border_style": 1,
        "bold": 1,
    },
    "lavender": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00FFD0E8",
        "outline_color": "&H00662255",
        "back_color": "&H66331144",
        "outline": 3,
        "shadow": 1,
        "border_style": 1,
        "bold": 1,
    },
    "hop_den": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "back_color": "&H00000000",
        "outline": 8,
        "shadow": 0,
        "border_style": 3,
        "bold": 1,
    },
    "hop_trang": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00000000",
        "outline_color": "&H00FFFFFF",
        "back_color": "&H00FFFFFF",
        "outline": 8,
        "shadow": 0,
        "border_style": 3,
        "bold": 1,
    },
    "hop_vang": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00000000",
        "outline_color": "&H0000E8FF",
        "back_color": "&H0000E8FF",
        "outline": 8,
        "shadow": 0,
        "border_style": 3,
        "bold": 1,
    },
    "hop_tim": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00882266",
        "back_color": "&H00882266",
        "outline": 8,
        "shadow": 0,
        "border_style": 3,
        "bold": 1,
    },
    "hop_xam": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00333333",
        "back_color": "&H00333333",
        "outline": 8,
        "shadow": 0,
        "border_style": 3,
        "bold": 1,
    },
    "hop_hong": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00000000",
        "outline_color": "&H00CC88FF",
        "back_color": "&H00CC88FF",
        "outline": 8,
        "shadow": 0,
        "border_style": 3,
        "bold": 1,
    },
    "khong_vien": {
        "fontname": "",
        "fontsize": 34,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "back_color": "&H00000000",
        "outline": 0,
        "shadow": 0,
        "border_style": 1,
        "bold": 1,
    },
}

LOGO_POSITIONS = {
    "top_left": "20:20",
    "top_right": "W-w-20:20",
    "bottom_left": "20:H-h-20",
    "bottom_right": "W-w-20:H-h-20",
}


def preset_names() -> list[str]:
    return list(PRESETS.keys())


def normalize_preset_name(name: Any) -> str:
    key = str(name or "").strip().lower()
    if key in PRESETS:
        return key
    return PRESET_DEFAULT


def get_preset(name: Any) -> dict[str, Any]:
    return dict(PRESETS[normalize_preset_name(name)])


def _opt_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


ANTI_COPYRIGHT_MODES = ("off", "standard", "advanced", "custom")

BLUR_SUB_MODES = (
    "auto_ai",
    "smart",
    "bottom",
    "bottom_medium",
    "bottom_large",
    "bottom_full",
    "full",
    "center",
    "legacy_ai",
    "ocr",
    "off",
)

ANTI_COPYRIGHT_PRESETS: dict[str, dict[str, Any]] = {
    "off": {
        "enabled": False,
        "mode": "off",
        "mirror": False,
        "noise": False,
        "color_shift": False,
        "vignette": False,
        "blur_sub": False,
        "blur_sub_mode": "auto_ai",
        "pitch_shift": False,
        "micro_zoom": False,
        "speed": 1.0,
    },
    "standard": {
        "enabled": True,
        "mode": "standard",
        "mirror": False,
        "noise": True,         # Phu hat film grain sieu min 2/100 doi pHash/MD5
        "color_shift": True,   # Tinh chinh contrast=1.04, saturation=1.06, gamma=0.98
        "vignette": False,
        "blur_sub": True,      # Che mo chu phu de goc (AI Fast Scan 1s do dung vi tri tung clip)
        "blur_sub_mode": "auto_ai",
        "pitch_shift": True,   # Dich tone am thanh nen +2.5% tranh acoustic fingerprint
        "micro_zoom": False,
        "speed": 1.03,         # Tang toc nhe 1.03x
    },
    "advanced": {
        "enabled": True,
        "mode": "advanced",
        "mirror": True,        # Lat guong video
        "noise": True,         # Phu hat film grain
        "color_shift": True,   # Chinh mau vi mo
        "vignette": True,      # Vien toi 4 goc
        "blur_sub": True,      # Che mo dai phu de goc o chan video
        "blur_sub_mode": "auto_ai",
        "pitch_shift": True,   # Dich tone am thanh nen
        "micro_zoom": True,    # Micro zoom 1.04x
        "speed": 1.04,         # Tang toc 1.04x
    },
}


def normalize_output_opts(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    speed = _opt_float(raw.get("video_speed"))
    if speed is None:
        speed = 1.0
    speed = max(0.5, min(1.5, speed))
    size = _opt_float(raw.get("logo_size_percent"))
    if size is None:
        size = 10.0
    size = max(2.0, min(40.0, size))
    pos = str(raw.get("logo_position") or "top_right").strip().lower()
    if pos not in LOGO_POSITIONS:
        pos = "top_right"
    sx = _opt_float(raw.get("sub_pos_x"))
    sy = _opt_float(raw.get("sub_pos_y"))
    ss = _opt_float(raw.get("sub_scale"))
    if sx is not None:
        sx = max(0.0, min(1.0, sx))
    if sy is not None:
        sy = max(0.0, min(1.0, sy))
    if ss is not None:
        ss = max(0.01, min(0.4, ss))

    # Anti-copyright mode normalization: mac dinh standard (chong ban quyen + che mo sub goc)
    ac_mode = str(raw.get("anti_copyright_mode") or "standard").strip().lower()
    if ac_mode not in ANTI_COPYRIGHT_MODES:
        ac_mode = "standard"

    # blur_sub_mode: auto_ai (AI Fast Scan 1s - dung vi tri moi clip) | bottom | bottom_medium | center | bottom_full | off
    bs_mode = str(raw.get("ac_blur_sub_mode") or raw.get("blur_sub_mode") or "auto_ai").strip().lower()
    if bs_mode not in BLUR_SUB_MODES:
        bs_mode = "auto_ai"

    if "ac_blur_sub" in raw and raw.get("ac_blur_sub") is not None and str(raw.get("ac_blur_sub")).strip() != "":
        blur_sub_explicit = _opt_bool(raw.get("ac_blur_sub"))
    else:
        blur_sub_explicit = None

    if ac_mode in ANTI_COPYRIGHT_PRESETS:
        ac_cfg = dict(ANTI_COPYRIGHT_PRESETS[ac_mode])
        if blur_sub_explicit is not None:
            ac_cfg["blur_sub"] = blur_sub_explicit
        ac_cfg["blur_sub_mode"] = bs_mode
    else:  # custom
        ac_speed = _opt_float(raw.get("ac_speed"))
        if ac_speed is None:
            ac_speed = 1.03
        ac_speed = max(0.5, min(1.5, ac_speed))
        ac_cfg = {
            "enabled": True,
            "mode": "custom",
            "mirror": _opt_bool(raw.get("ac_mirror")),
            "noise": _opt_bool(raw.get("ac_noise")),
            "color_shift": _opt_bool(raw.get("ac_color")),
            "vignette": _opt_bool(raw.get("ac_vignette")),
            "blur_sub": bool(blur_sub_explicit if blur_sub_explicit is not None else True),
            "blur_sub_mode": bs_mode,
            "pitch_shift": _opt_bool(raw.get("ac_pitch")),
            "micro_zoom": _opt_bool(raw.get("ac_zoom")),
            "speed": ac_speed,
        }

    return {
        "preset_name": normalize_preset_name(raw.get("preset_name")),
        "video_speed": round(speed, 4),
        "logo_path": str(raw.get("logo_path") or "").strip(),
        "logo_position": pos,
        "logo_size_percent": round(size, 2),
        "sub_pos_x": sx,
        "sub_pos_y": sy,
        "sub_scale": ss,
        "translate_engine": (
            te_raw
            if (
                te_raw := str(raw.get("translate_engine") or "").strip().lower()
            ) in ("openai", "deepseek", "qwen", "google")
            else ""  # de trong → resolve sau khi co key
        ),
        "genre_id": str(raw.get("genre_id") or "").strip()[:64],
        "genre_prompt": str(raw.get("genre_prompt") or "").strip()[:12000],
        "voice_mode": (
            vm
            if (vm := str(raw.get("voice_mode") or "single").strip().lower()) in ("single", "multi")
            else "single"
        ),
        "anti_copyright": ac_cfg,
        "export_dir": str(raw.get("export_dir") or "").strip(),
    }


def apply_opts_to_job(job: dict, opts: dict[str, Any] | None) -> dict:
    """Ghi caption/export tren job dict — process_job merge vao cfg."""
    # Giu chrome_fallback / translate_engine tu opts goc (normalize co translate).
    raw = opts or {}
    n = normalize_output_opts(raw)
    cap = job.setdefault("caption", {})
    exp = job.setdefault("export", {})
    vo = job.setdefault("voiceover", {})
    auto = vo.setdefault("auto", {})
    cap["preset_name"] = n["preset_name"]
    if n["sub_pos_x"] is not None and n["sub_pos_y"] is not None:
        cap["sub_pos_x"] = n["sub_pos_x"]
        cap["sub_pos_y"] = n["sub_pos_y"]
        if n["sub_scale"] is not None:
            cap["sub_scale"] = n["sub_scale"]
    exp["video_speed"] = n["video_speed"]
    exp["logo_path"] = n["logo_path"]
    exp["logo_position"] = n["logo_position"]
    exp["logo_size_percent"] = n["logo_size_percent"]
    if n.get("export_dir"):
        exp["export_dir"] = n["export_dir"]

    # Gan anti_copyright
    ac = n.get("anti_copyright")
    if ac:
        exp["anti_copyright"] = ac
        if "blur_sub" in ac:
            cap["blur_sub"] = ac["blur_sub"]
        if "blur_sub_mode" in ac:
            cap["blur_sub_mode"] = ac["blur_sub_mode"]
        if ac.get("enabled"):
            # Neu nguoi dung chua tu tay doi toc do (van de 1.0), ap dung toc do chong ban quyen
            if abs(float(exp.get("video_speed") or 1.0) - 1.0) <= 1e-3:
                ac_spd = float(ac.get("speed") or 1.0)
                if abs(ac_spd - 1.0) > 1e-3:
                    exp["video_speed"] = ac_spd

    te = n.get("translate_engine") or ""
    if te not in ("google", "openai", "deepseek", "qwen"):
        from .translate_llm import resolve_translate_engine

        te = resolve_translate_engine(te)
    auto["translate_engine"] = te
    if te in ("openai", "deepseek", "qwen"):
        auto["translate_llm"] = te
    else:
        auto["translate_llm"] = "off"
    from .genre_profiles import resolve_genre_prompt

    gid, gprompt = resolve_genre_prompt(
        n.get("genre_id") or raw.get("genre_id"),
        n.get("genre_prompt") if "genre_prompt" in n else raw.get("genre_prompt"),
    )
    auto["genre_id"] = gid
    auto["genre_prompt"] = gprompt
    auto["topic_context"] = gprompt
    vm = n.get("voice_mode") or "single"
    cast_on = (vm == "multi")
    auto["voice_mode"] = vm
    auto["cast_speakers"] = cast_on
    vo["voice_mode"] = vm
    vo["cast_speakers"] = cast_on
    return n


def resolve_ass_style(
    cfg_cap: dict,
    font_path: str,
    play_w: int = 1080,
    play_h: int = 1920,
    *,
    font_ass_name: str = "",
) -> dict[str, Any]:
    """classic = hanh vi cu. Preset khac = bang mau, khong lay color tu config.yaml."""
    name = normalize_preset_name(cfg_cap.get("preset_name"))
    preset = get_preset(name)
    font_name = (
        cfg_cap.get("font_name")
        or preset.get("fontname")
        or font_ass_name
        or "Arial"
    )
    custom_pos = (
        cfg_cap.get("sub_pos_x") is not None
        and cfg_cap.get("sub_pos_y") is not None
    )
    if name == "classic":
        fs = int(cfg_cap.get("font_size") or 36)
        outline = int(cfg_cap.get("outline", 3))
        primary = str(cfg_cap.get("primary_color", "&H00FFFFFF"))
        outline_c = str(cfg_cap.get("outline_color", "&H00000000"))
        back = str(cfg_cap.get("back_color", "&H66000000"))
        shadow = int(cfg_cap.get("shadow", 1))
        border = int(cfg_cap.get("border_style", 1))
        bold = int(cfg_cap.get("bold", 1))
    else:
        fs = int(preset.get("fontsize") or cfg_cap.get("font_size") or 36)
        outline = int(preset.get("outline") if preset.get("outline") is not None else 3)
        primary = str(preset["primary_color"])
        outline_c = str(preset["outline_color"])
        back = str(preset["back_color"])
        shadow = int(preset.get("shadow") or 0)
        border = int(preset.get("border_style") or 1)
        bold = int(preset.get("bold") if preset.get("bold") is not None else 1)

    scale = cfg_cap.get("sub_scale")
    if custom_pos and scale is not None:
        try:
            fs = max(12, int(round(float(scale) * float(play_h))))
        except (TypeError, ValueError):
            pass

    align = 7 if custom_pos else 2
    if custom_pos:
        margin_v = 0
    elif "margin_v" in cfg_cap and cfg_cap.get("margin_v") is not None and str(cfg_cap.get("margin_v")).strip() != "":
        margin_v = int(cfg_cap.get("margin_v"))
    elif play_w >= play_h:
        # Video ngang (16:9): Tam vet mo o y=91.75% -> MarginV can giua khoang 6.5% chieu cao
        margin_v = max(35, int(round(float(play_h) * 0.065)))
    else:
        # Video doc (9:16): Tam vet mo o y=86.25% -> MarginV can giua khoang 12.0% chieu cao
        # (vi du: 1080x1920 -> MarginV=230px, 720x1280 -> MarginV=154px)
        margin_v = max(45, int(round(float(play_h) * 0.120)))
    return {
        "preset_name": name,
        "fontname": font_name,
        "fontsize": fs,
        "primary_color": primary,
        "outline_color": outline_c,
        "back_color": back,
        "outline": outline,
        "shadow": shadow,
        "border_style": border,
        "bold": bold,
        "alignment": align,
        "margin_v": margin_v,
        "play_w": int(play_w),
        "play_h": int(play_h),
    }


def style_line(st: dict[str, Any]) -> str:
    return (
        f"Style: Default,{st['fontname']},{st['fontsize']},"
        f"{st['primary_color']},{st['outline_color']},{st['back_color']},"
        f"{st['bold']},0,{st['border_style']},{st['outline']},{st['shadow']},"
        f"{st['alignment']},40,40,{st['margin_v']},1"
    )


def dialogue_override(cfg_cap: dict, play_w: int, play_h: int) -> str:
    """\\pos(x,y) neu user truyen sub_pos_*. Rong = giu MarginV cu."""
    sx = cfg_cap.get("sub_pos_x")
    sy = cfg_cap.get("sub_pos_y")
    if sx is None or sy is None:
        return ""
    try:
        x = float(sx) * float(play_w)
        y = float(sy) * float(play_h)
    except (TypeError, ValueError):
        return ""
    return f"{{\\pos({x:.1f},{y:.1f})}}"


def logo_overlay_xy(position: str) -> str:
    return LOGO_POSITIONS.get(str(position).lower(), LOGO_POSITIONS["top_right"])


def atempo_filters(speed: float) -> str:
    """Chuoi atempo (moi khau 0.5-2.0) cho video_speed tong the."""
    s = float(speed)
    if abs(s - 1.0) <= 1e-3:
        return "anull"
    parts: list[str] = []
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}".rstrip("0").rstrip("."))
    return ",".join(parts)


def apply_video_speed(src: Path, dst: Path, speed: float) -> Path:
    """Pass 2: setpts + atempo. speed=1.0 -> copy, khong encode lai."""
    src, dst = Path(src), Path(dst)
    s = max(0.5, min(1.5, float(speed)))
    if abs(s - 1.0) <= 1e-3:
        if src.resolve() != dst.resolve():
            import shutil
            shutil.copy2(src, dst)
        return dst
    a = atempo_filters(s)
    has_a = probe_has_audio(src)
    tmp = dst.with_suffix(".spd_tmp.mp4")
    if has_a:
        fc = f"[0:v]setpts=PTS/{s:.6f}[v];[0:a]{a}[a]"
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-filter_complex", fc,
            "-map", "[v]", "-map", "[a]",
            "-map_metadata", "-1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            str(tmp),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-filter:v", f"setpts=PTS/{s:.6f}",
            "-an",
            "-map_metadata", "-1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-movflags", "+faststart",
            str(tmp),
        ]
    log.info("Export video_speed=%.3f: %s", s, dst.name)
    run(cmd)
    if dst.exists() and dst.resolve() != tmp.resolve():
        dst.unlink()
    tmp.replace(dst)
    return dst
