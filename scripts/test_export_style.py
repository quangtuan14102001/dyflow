#!/usr/bin/env python3
"""Test 4 module export style (preset / speed / logo / sub pos). Khong chay pipeline.

  python scripts/test_export_style.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.compose import _ass_header, _write_ass_cues  # noqa: E402
from dyflow.export_style import (  # noqa: E402
    PRESET_DEFAULT,
    apply_opts_to_job,
    apply_video_speed,
    atempo_filters,
    dialogue_override,
    get_preset,
    logo_overlay_xy,
    normalize_output_opts,
    normalize_preset_name,
    preset_names,
    resolve_ass_style,
    style_line,
)


def _fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def _old_style_line(cfg: dict, font_name: str = "Arial") -> str:
    """Cong thuc Style: hardcoded truoc Module 1."""
    fs = cfg.get("font_size", 56)
    primary = cfg.get("primary_color", "&H00FFFFFF")
    outline_c = cfg.get("outline_color", "&H00000000")
    outline = cfg.get("outline", 3)
    margin_v = cfg.get("margin_v", 220)
    return (
        f"Style: Default,{font_name},{fs},{primary},{outline_c},&H66000000,"
        f"1,0,1,{outline},1,2,40,40,{margin_v},1"
    )


def test_module1_presets() -> None:
    names = preset_names()
    if len(names) < 12:
        _fail(f"can >=12 preset, got {len(names)}")
    for n in (
        "neon", "classic", "minimal", "bold", "mint", "rose", "sky", "sunset",
        "lavender", "hop_den", "hop_trang", "hop_vang", "hop_tim", "hop_xam",
        "hop_hong", "khong_vien",
    ):
        if n not in names:
            _fail(f"thieu preset {n}")
    if normalize_preset_name("NEON") != "neon":
        _fail("normalize case")
    if normalize_preset_name("nope") != PRESET_DEFAULT:
        _fail("unknown -> classic")

    cfg = {
        "font_size": 56,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 3,
        "margin_v": 220,
    }
    classic = _ass_header(cfg, "", 1080, 1920)
    golden = _old_style_line(cfg)
    if golden not in classic:
        _fail(f"classic khong khop Style cu:\n{classic}\nexpect {golden}")

    yaml_like = {
        "font_size": 42,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 2,
        "margin_v": 120,
    }
    if _old_style_line(yaml_like) not in _ass_header(yaml_like, "", 1080, 1920):
        _fail("classic + config.yaml khong khop Style cu")

    neon = _ass_header({**cfg, "preset_name": "neon"}, "", 1080, 1920)
    if golden in neon:
        _fail("neon van giong classic")
    if get_preset("neon")["primary_color"] not in neon:
        _fail("neon thieu mau preset")
    hop_st = resolve_ass_style(
        {**cfg, "preset_name": "hop_den"}, "", 1080, 1920, font_ass_name="Arial",
    )
    if hop_st["border_style"] != 3:
        _fail(f"hop_den phai BorderStyle 3: {hop_st}")
    if hop_st["primary_color"] == cfg["primary_color"] and hop_st["back_color"] == "&H66000000":
        pass  # hop_den chu trang, hop den — primary van trang, back khac
    if hop_st["back_color"] != "&H00000000":
        _fail(f"hop_den back: {hop_st['back_color']}")
    print("OK M1 presets: 16 ten, classic=Style cu, neon/hop_den doi mau")


def test_module1_ass_files(tmp: Path) -> None:
    cues = [{"text": "Xin chao", "start": 0.0, "end": 2.0}]
    cfg = {
        "font_size": 42, "outline": 2, "margin_v": 120,
        "chars_per_line": 28, "max_lines": 2,
    }
    paths: dict[str, str] = {}
    for name in ("classic", "neon", "hop_vang"):
        p = tmp / f"{name}.ass"
        _write_ass_cues(cues, p, {**cfg, "preset_name": name}, "", 720, 1280)
        txt = p.read_text(encoding="utf-8")
        paths[name] = txt
        if "Style: Default," not in txt or "Xin chao" not in txt:
            _fail(f"{name} ass hong")
        print(f"  demo {p.name}: { [ln for ln in txt.splitlines() if ln.startswith('Style:')][0] }")
    if paths["classic"] == paths["neon"]:
        _fail("classic/neon ass giong nhau")
    hop_st = [ln for ln in paths["hop_vang"].splitlines() if ln.startswith("Style:")][0]
    # Bold,Italic,BorderStyle,... -> 1,0,3,
    if ",1,0,3," not in hop_st:
        _fail(f"hop_vang can border_style 3: {hop_st}")
    print("OK M1 demo ASS: classic / neon / hop_vang ghi file khac nhau")


def test_module2_speed() -> None:
    if atempo_filters(1.0) != "anull":
        _fail("speed 1.0 phai anull (khong encode)")
    a = atempo_filters(0.85)
    if "0.85" not in a:
        _fail(f"0.85 atempo: {a}")
    if "," in atempo_filters(0.85) or "," in atempo_filters(1.5):
        _fail("0.5-1.5 khong can chain")
    chained = atempo_filters(3.0)
    if chained != "atempo=2.0,atempo=1.5":
        _fail(f"chain 3.0: {chained}")
    slow = atempo_filters(0.25)
    if "atempo=0.5" not in slow or slow.count("atempo") < 2:
        _fail(f"chain 0.25: {slow}")
    opts = normalize_output_opts({"video_speed": "9"})
    if opts["video_speed"] != 1.5:
        _fail(f"clamp 1.5: {opts}")
    opts2 = normalize_output_opts({})
    if opts2["video_speed"] != 1.0:
        _fail("default speed 1.0")
    print("OK M2 video_speed: default 1.0, clamp 0.5-1.5, atempo 0.85 mot khau, chain an toan")


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_clip(path: Path, seconds: float = 2.0) -> None:
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _duration(path: Path) -> float:
    from dyflow.utils import probe_duration
    return probe_duration(path)


def test_module2_ffmpeg(tmp: Path) -> None:
    if not _have_ffmpeg():
        print("SKIP M2 ffmpeg demo (thieu ffmpeg/ffprobe)")
        return
    src = tmp / "src.mp4"
    _make_clip(src, 2.0)
    d0 = _duration(src)
    same = tmp / "spd1.mp4"
    apply_video_speed(src, same, 1.0)
    if abs(_duration(same) - d0) > 0.08:
        _fail("speed 1.0 phai giu duration")
    slow = tmp / "spd085.mp4"
    apply_video_speed(src, slow, 0.85)
    d1 = _duration(slow)
    expect = d0 / 0.85
    if abs(d1 - expect) > 0.15:
        _fail(f"speed 0.85 duration {d1:.3f} vs expect {expect:.3f} (src {d0:.3f})")
    print(f"OK M2 ffmpeg: src={d0:.3f}s speed=1.0 giu nguyen, speed=0.85 -> {d1:.3f}s (~{expect:.3f})")


def test_module3_logo() -> None:
    if logo_overlay_xy("top_left") != "20:20":
        _fail("top_left")
    if logo_overlay_xy("top_right") != "W-w-20:20":
        _fail("top_right")
    if logo_overlay_xy("bottom_left") != "20:H-h-20":
        _fail("bottom_left")
    if logo_overlay_xy("bottom_right") != "W-w-20:H-h-20":
        _fail("bottom_right")
    o = normalize_output_opts({"logo_position": "nope"})
    if o["logo_position"] != "top_right":
        _fail("logo pos default")
    print("OK M3 logo: 4 goc ffmpeg overlay")


def test_module3_ffmpeg(tmp: Path) -> None:
    if not _have_ffmpeg():
        print("SKIP M3 ffmpeg demo (thieu ffmpeg/ffprobe)")
        return
    src = tmp / "base.mp4"
    _make_clip(src, 1.0)
    # PNG 64x64 do, alpha 0 o vien (khong nen den)
    png = tmp / "logo.png"
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "color=c=0xFF2244@1.0:s=48x48,format=rgba",
            "-frames:v", "1", str(png),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for pos, xy in (
        ("top_left", "20:20"),
        ("top_right", "W-w-20:20"),
        ("bottom_left", "20:H-h-20"),
        ("bottom_right", "W-w-20:H-h-20"),
    ):
        out = tmp / f"logo_{pos}.mp4"
        lw = max(8, int(round(320 * 10 / 100.0)))
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-i", str(src), "-i", str(png),
                "-filter_complex",
                f"[1:v]scale={lw}:-1,format=rgba[lg];[0:v][lg]overlay={xy}:format=auto[v]",
                "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-c:a", "copy",
                "-t", "0.5", str(out),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not out.is_file() or out.stat().st_size < 1000:
            _fail(f"logo {pos} khong xuat duoc")
    print("OK M3 ffmpeg: overlay 4 goc (10% width, format=auto)")


def test_module4_sub_pos(tmp: Path) -> None:
    cfg = {"font_size": 42, "margin_v": 120, "chars_per_line": 28, "max_lines": 2}
    if dialogue_override(cfg, 1080, 1920) != "":
        _fail("thieu sub_pos phai rong (hanh vi cu)")
    ov = dialogue_override({**cfg, "sub_pos_x": 0.1, "sub_pos_y": 0.8}, 1080, 1920)
    if ov != "{\\pos(108.0,1536.0)}":
        _fail(f"pos: {ov}")
    st = resolve_ass_style(
        {**cfg, "sub_pos_x": 0.1, "sub_pos_y": 0.8, "sub_scale": 0.05},
        "", 1080, 1920, font_ass_name="Arial",
    )
    if st["alignment"] != 7:
        _fail("custom pos Alignment 7")
    if st["fontsize"] != 96:  # 0.05 * 1920
        _fail(f"fontsize tu sub_scale: {st['fontsize']}")
    if st["margin_v"] != 0:
        _fail("margin_v 0 khi co pos")
    job: dict = {}
    n = apply_opts_to_job(job, {})
    if n["preset_name"] != "classic":
        _fail("opts rong = classic")
    if job["caption"]["preset_name"] != "classic":
        _fail("job.caption.preset_name")
    p = tmp / "pos.ass"
    _write_ass_cues(
        [{"text": "Test pos", "start": 0.0, "end": 1.0}],
        p,
        {**cfg, "sub_pos_x": 0.1, "sub_pos_y": 0.8, "sub_scale": 0.05},
        "", 1080, 1920,
    )
    txt = p.read_text(encoding="utf-8")
    if "{\\pos(108.0,1536.0)}" not in txt:
        _fail("ASS thieu \\pos")
    if ",96," not in [ln for ln in txt.splitlines() if ln.startswith("Style:")][0]:
        _fail("ASS thieu fontsize 96")
    print("OK M4 sub_pos: mac dinh khong \\pos; 0.1/0.8/0.05 -> pos(108,1536) fontsize 96")


def test_regression_empty_opts() -> None:
    n = normalize_output_opts(None)
    if n["preset_name"] != "classic" or n["video_speed"] != 1.0:
        _fail(f"empty opts: {n}")
    if n["logo_path"] or n["sub_pos_x"] is not None:
        _fail("empty khong duoc bat logo/pos")
    cfg = {
        "font_size": 42, "outline": 2, "margin_v": 120,
        "primary_color": "&H00FFFFFF", "outline_color": "&H00000000",
    }
    a = style_line(resolve_ass_style(cfg, "", 1080, 1920, font_ass_name="Arial"))
    b = style_line(resolve_ass_style(
        {**cfg, "preset_name": "classic"}, "", 1080, 1920, font_ass_name="Arial",
    ))
    if a != b:
        _fail("thieu preset_name phai = classic")
    if a != _old_style_line(cfg):
        _fail(f"regression Style lech:\n{a}\n{_old_style_line(cfg)}")
    print("OK regression: khong truyen tham so = output classic/speed 1.0")


def test_anti_copyright() -> None:
    # 1. Default -> Standard (mac dinh bat chong ban quyen + che mo sub goc)
    n0 = normalize_output_opts({})
    ac0 = n0.get("anti_copyright", {})
    if not ac0.get("enabled") or ac0.get("mode") != "standard":
        _fail(f"Default anti-copyright phai la standard: {ac0}")
    if not ac0.get("blur_sub"):
        _fail("Standard mac dinh phai che mo sub goc (blur_sub True)")

    # 2. Explicit Off
    n_off = normalize_output_opts({"anti_copyright_mode": "off"})
    ac_off = n_off.get("anti_copyright", {})
    if ac_off.get("enabled") or ac_off.get("mode") != "off":
        _fail(f"Off mode phai disabled: {ac_off}")

    # 3. Standard preset filters
    n_std = normalize_output_opts({"anti_copyright_mode": "standard"})
    ac_std = n_std.get("anti_copyright", {})
    if not ac_std.get("noise") or not ac_std.get("color_shift") or not ac_std.get("pitch_shift") or not ac_std.get("blur_sub"):
        _fail(f"Standard thieu filter: {ac_std}")
    if ac_std.get("mirror"):
        _fail("Standard khong duoc lat guong")

    # 4. Advanced preset
    n_adv = normalize_output_opts({"anti_copyright_mode": "advanced"})
    ac_adv = n_adv.get("anti_copyright", {})
    if not ac_adv.get("enabled") or not ac_adv.get("mirror") or not ac_adv.get("vignette") or not ac_adv.get("blur_sub") or not ac_adv.get("micro_zoom"):
        _fail(f"Advanced thieu filter: {ac_adv}")

    # 4. Job merge & speed auto-apply
    job = {}
    apply_opts_to_job(job, {"anti_copyright_mode": "standard"})
    exp = job.get("export", {})
    if not exp.get("anti_copyright", {}).get("enabled"):
        _fail(f"Job export thieu anti_copyright: {exp}")
    if abs(float(exp.get("video_speed") or 1.0) - 1.03) > 1e-3:
        _fail(f"Speed phai tu dong = 1.03x cho standard: {exp}")

    # 5. User custom speed overrides anti-copyright default speed
    job2 = {}
    apply_opts_to_job(job2, {"anti_copyright_mode": "standard", "video_speed": "1.10"})
    if abs(float(job2.get("export", {}).get("video_speed") or 1.0) - 1.10) > 1e-3:
        _fail("User override speed bi de boi anti-copyright")

    # 6. Blur sub mode options: bottom, center, auto_ai
    n_bot = normalize_output_opts({"ac_blur_sub_mode": "bottom"})
    if n_bot["anti_copyright"]["blur_sub_mode"] != "bottom":
        _fail(f"bottom mode: {n_bot}")

    n_cen = normalize_output_opts({"ac_blur_sub_mode": "center"})
    if n_cen["anti_copyright"]["blur_sub_mode"] != "center":
        _fail(f"center mode: {n_cen}")

    n_ai = normalize_output_opts({"ac_blur_sub_mode": "auto_ai"})
    if n_ai["anti_copyright"]["blur_sub_mode"] != "auto_ai":
        _fail(f"auto_ai mode: {n_ai}")

    print("OK anti-copyright: off, standard (1.03x + noise + color + pitch), advanced (mirror + vignette + blur_sub + zoom), speed override, blur_sub_mode (bottom/center/auto_ai)")


def test_compose_anti_copyright(tmp: Path) -> None:
    if not _have_ffmpeg():
        return
    from dyflow.compose import compose
    src = tmp / "comp_src.mp4"
    _make_clip(src, 1.5)

    cfg = {
        "paths": {"work_dir": str(tmp / "work"), "output_dir": str(tmp / "out"), "font_path": ""},
        "caption": {"enabled": True, "font_size": 36, "margin_v": 160},
        "voiceover": {"enabled": False, "mute_original": False},
        "export": {
            "formats": ["9:16"],
            "preset": "ultrafast",
            "anti_copyright": {
                "enabled": True,
                "mirror": True,
                "noise": True,
                "color_shift": True,
                "vignette": True,
                "blur_sub": True,
                "blur_sub_mode": "center",
                "pitch_shift": True,
                "micro_zoom": True,
            },
        },
    }
    outs = compose(
        video=src,
        voice=None,
        script="Day la phu de thu nghiem cua tool",
        out_dir=tmp / "out",
        base_name="test_ac_center",
        cfg=cfg,
        music=None,
    )
    if not outs or not outs[0].is_file() or outs[0].stat().st_size < 1000:
        _fail("Compose anti-copyright center that bai")
    print(f"OK compose anti-copyright center + sub: {outs[0].name} ({outs[0].stat().st_size} bytes)")

    # Test auto_ai compose
    cfg["export"]["anti_copyright"]["blur_sub_mode"] = "auto_ai"
    outs_ai = compose(
        video=src,
        voice=None,
        script="Day la phu de thu nghiem auto AI",
        out_dir=tmp / "out",
        base_name="test_ac_ai",
        cfg=cfg,
        music=None,
    )
    if not outs_ai or not outs_ai[0].is_file() or outs_ai[0].stat().st_size < 1000:
        _fail("Compose anti-copyright auto_ai that bai")
    print(f"OK compose anti-copyright auto_ai + sub: {outs_ai[0].name} ({outs_ai[0].stat().st_size} bytes)")


def main() -> None:
    print("test_export_style")
    test_module1_presets()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_module1_ass_files(tmp)
        test_module2_speed()
        test_module2_ffmpeg(tmp)
        test_module3_logo()
        test_module3_ffmpeg(tmp)
        test_module4_sub_pos(tmp)
        test_regression_empty_opts()
        test_anti_copyright()
        test_compose_anti_copyright(tmp)
    print("ALL OK")


if __name__ == "__main__":
    main()

