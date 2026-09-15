#!/usr/bin/env python3
"""Kiem tra editor layout + ffmpeg filter graph (khong can video).

  python scripts/test_video_editor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.video_editor import (  # noqa: E402
    _esc_drawtext,
    _iou_norm,
    build_filter_complex,
    empty_layout,
    normalize_layout,
    normalize_region,
)


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK  {name}")
        return
    print(f"  FAIL {name} {detail}")
    raise SystemExit(1)


def test_normalize_region() -> None:
    r = normalize_region({"kind": "overlay", "x": -0.2, "y": 0.9, "w": 0.8, "h": 0.5, "t1": 1, "t0": 5})
    _ok("clamp xy", 0.0 <= r["x"] <= 1 and 0.0 <= r["y"] <= 1)
    _ok("box in frame", r["x"] + r["w"] <= 1.001 and r["y"] + r["h"] <= 1.001)
    _ok("bad t1 dropped", r["t1"] is None)
    r2 = normalize_region({"kind": "nope"})
    _ok("unknown kind -> overlay", r2["kind"] == "overlay")


def test_layout_defaults() -> None:
    raw = {"transform": {"hflip": 1, "rotate": 45, "zoom": 9}, "regions": [{"kind": "blur", "w": 0.2, "h": 0.1}]}
    lay = normalize_layout(raw, video_w=1080, video_h=1920, duration=10)
    _ok("rotate snap 0", lay["transform"]["rotate"] == 0)
    _ok("zoom cap", lay["transform"]["zoom"] <= 1.4)
    _ok("hflip", lay["transform"]["hflip"] is True)
    _ok("1 region", len(lay["regions"]) == 1)


def test_filter_blur_timed() -> None:
    lay = empty_layout(video_w=1080, video_h=1920, duration=20)
    lay["regions"] = [
        normalize_region({
            "kind": "overlay", "x": 0.1, "y": 0.7, "w": 0.8, "h": 0.12,
            "t0": 2, "t1": 8, "text": "Hello: there",
        }, duration=20),
        normalize_region({
            "kind": "blur", "x": 0.8, "y": 0.02, "w": 0.16, "h": 0.08,
            "t0": 0, "t1": None, "blur": "heavy",
        }, duration=20),
        normalize_region({"kind": "sub", "x": 0.1, "y": 0.85, "w": 0.8, "h": 0.1}, duration=20),
    ]
    fc, label = build_filter_complex(lay, duration=20.0, has_logo=False)
    _ok("vout label", label == "[vout]")
    _ok("gblur", "gblur=" in fc)
    _ok("no boxblur", "boxblur=" not in fc)
    _ok("timed overlay", "between(t,2.000,8.000)" in fc)
    _ok("drawtext escaped colon", r"Hello\: there" in fc or "Hello\\: there" in fc)
    _ok("sub not blurred extra", fc.count("gblur=") == 2)
    _ok("esc helper", ":" not in _esc_drawtext("a:b") or r"\:" in _esc_drawtext("a:b"))


def test_filter_logo_bounce() -> None:
    lay = empty_layout()
    lay["logo"]["enabled"] = True
    lay["logo"]["bounce"] = True
    fc, _ = build_filter_complex(lay, duration=5.0, has_logo=True)
    _ok("logo overlay", "[lg]overlay" in fc)
    _ok("bounce expr", "mod(t,8)" in fc)


def test_filter_hflip_zoom() -> None:
    lay = empty_layout(video_w=720, video_h=1280, duration=3)
    lay["transform"]["hflip"] = True
    lay["transform"]["zoom"] = 1.1
    fc, _ = build_filter_complex(lay, duration=3.0, has_logo=False)
    _ok("hflip", "hflip" in fc)
    _ok("zoom scale", "scale=" in fc and "crop=" in fc)


def test_iou() -> None:
    a = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
    b = {"x": 0.15, "y": 0.15, "w": 0.2, "h": 0.2}
    c = {"x": 0.8, "y": 0.8, "w": 0.1, "h": 0.1}
    _ok("overlap iou", _iou_norm(a, b) > 0.2)
    _ok("far iou 0", _iou_norm(a, c) == 0.0)


def test_apply_ffmpeg() -> None:
    import shutil
    import subprocess
    import tempfile

    from dyflow.video_editor import apply_layout, _crop_box

    box = _crop_box(0.2372, 0.6451, 0.55, 0.12, 960, 544)
    _ok("crop exists", box is not None)
    x, y, w, h = box
    _ok("crop even", x % 2 == 0 and y % 2 == 0 and w % 2 == 0 and h % 2 == 0)
    _ok("crop in frame", x + w <= 960 and y + h <= 544)

    if not shutil.which("ffmpeg"):
        print("  SKIP apply ffmpeg (no binary)")
        return
    td = Path(tempfile.mkdtemp())
    src = td / "in.mp4"
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=960x544:rate=25:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    lay = empty_layout(video_w=960, video_h=544, duration=1.0)
    lay["regions"] = [
        normalize_region({
            "kind": "overlay", "x": 0.2372, "y": 0.6451, "w": 0.55, "h": 0.12,
            "text": "The script is complete for now, hello",
            "blur": "medium",
        }, duration=1.0),
        normalize_region({
            "kind": "overlay", "x": 0.2, "y": 0.7, "w": 0.4, "h": 0.04,
            "text": "tiny", "blur": "heavy",
        }, duration=1.0),
    ]
    dst = td / "out.mp4"
    apply_layout(src, dst, lay)
    _ok("apply wrote file", dst.is_file() and dst.stat().st_size > 1000)


def main() -> None:
    print("test_video_editor")
    test_normalize_region()
    test_layout_defaults()
    test_filter_blur_timed()
    test_filter_logo_bounce()
    test_filter_hflip_zoom()
    test_iou()
    test_apply_ffmpeg()
    print("ALL OK")


if __name__ == "__main__":
    main()
