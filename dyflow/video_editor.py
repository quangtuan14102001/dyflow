"""Editor thu cong: vung dich / overlay / blur + OCR + xuat ffmpeg.

Toa do region la ty le 0..1 tren khung hinh nguon. Apply chi blur/che chu
va (neu co) ve chu thay the / logo — khong encode lai toan pipeline.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .utils import log, probe_duration, probe_has_audio, probe_video_size, run

LAYOUT_NAME = "editor_layout.json"
LOGO_NAME = "editor_logo.png"
KINDS = ("overlay", "blur", "sub")
BLUR_MAP = {
    "light": 8,
    "medium": 16,
    "heavy": 24,
}


def job_work_dir(job_id: str, cfg: dict) -> Path:
    try:
        from .desktop_mode import data_dir, enabled

        if enabled():
            return data_dir() / "work" / job_id
    except Exception:  # noqa: BLE001
        pass
    paths = cfg.get("paths") or {}
    return Path(str(paths.get("work_dir") or "work")).expanduser() / job_id


def job_output_dir(cfg: dict) -> Path:
    try:
        from .desktop_mode import data_dir, enabled

        if enabled():
            return data_dir() / "output"
    except Exception:  # noqa: BLE001
        pass
    paths = cfg.get("paths") or {}
    return Path(str(paths.get("output_dir") or "output")).expanduser()


def layout_path(work: Path) -> Path:
    return work / LAYOUT_NAME


def empty_layout(*, video_w: int = 1080, video_h: int = 1920, duration: float = 0.0) -> dict[str, Any]:
    return {
        "version": 1,
        "video_w": int(video_w),
        "video_h": int(video_h),
        "duration": float(duration),
        "transform": {"hflip": False, "rotate": 0, "zoom": 1.0},
        "logo": {
            "enabled": False,
            "bounce": False,
            "x": 0.04,
            "y": 0.04,
            "w": 0.14,
            "opacity": 0.9,
        },
        "marquee": {"enabled": False, "text": ""},
        "regions": [],
    }


def _clamp01(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = default
    return max(0.0, min(1.0, x))


def _opt_time(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return None


def normalize_region(raw: dict[str, Any], *, duration: float = 0.0) -> dict[str, Any]:
    kind = str(raw.get("kind") or "overlay").lower()
    if kind not in KINDS:
        kind = "overlay"
    x = _clamp01(raw.get("x"), 0.1)
    y = _clamp01(raw.get("y"), 0.7)
    w = _clamp01(raw.get("w"), 0.8)
    h = _clamp01(raw.get("h"), 0.12)
    if x + w > 1.0:
        w = max(0.02, 1.0 - x)
    if y + h > 1.0:
        h = max(0.02, 1.0 - y)
    t0 = _opt_time(raw.get("t0")) or 0.0
    t1 = _opt_time(raw.get("t1"))
    if duration > 0 and t1 is not None:
        t1 = min(t1, duration)
    if t1 is not None and t1 <= t0:
        t1 = None
    blur = str(raw.get("blur") or "medium").lower()
    if blur not in BLUR_MAP:
        blur = "medium"
    rid = str(raw.get("id") or "")[:40]
    if not rid:
        rid = "r" + uuid.uuid4().hex[:8]
    return {
        "id": rid,
        "kind": kind,
        "role": str(raw.get("role") or ("main" if kind == "sub" else kind)),
        "x": round(x, 4),
        "y": round(y, 4),
        "w": round(max(0.02, w), 4),
        "h": round(max(0.02, h), 4),
        "t0": round(t0, 3),
        "t1": round(t1, 3) if t1 is not None else None,
        "text": str(raw.get("text") or "")[:400],
        "src_text": str(raw.get("src_text") or "")[:400],
        "blur": blur,
    }


def normalize_layout(raw: dict[str, Any] | None, *, video_w: int, video_h: int, duration: float) -> dict[str, Any]:
    base = empty_layout(video_w=video_w, video_h=video_h, duration=duration)
    if not raw:
        return base
    tf = raw.get("transform") or {}
    rot = int(tf.get("rotate") or 0)
    if rot not in (0, 90, 180, 270):
        rot = 0
    try:
        zoom = float(tf.get("zoom") or 1.0)
    except (TypeError, ValueError):
        zoom = 1.0
    zoom = max(1.0, min(1.4, zoom))
    logo = raw.get("logo") or {}
    mq = raw.get("marquee") or {}
    regions = [normalize_region(r, duration=duration) for r in (raw.get("regions") or []) if isinstance(r, dict)]
    return {
        "version": 1,
        "video_w": int(video_w),
        "video_h": int(video_h),
        "duration": float(duration),
        "transform": {
            "hflip": bool(tf.get("hflip")),
            "rotate": rot,
            "zoom": round(zoom, 3),
        },
        "logo": {
            "enabled": bool(logo.get("enabled")),
            "bounce": bool(logo.get("bounce")),
            "x": round(_clamp01(logo.get("x"), 0.04), 4),
            "y": round(_clamp01(logo.get("y"), 0.04), 4),
            "w": round(max(0.04, _clamp01(logo.get("w"), 0.14)), 4),
            "opacity": round(max(0.15, min(1.0, float(logo.get("opacity") or 0.9))), 3),
        },
        "marquee": {
            "enabled": bool(mq.get("enabled")) and bool(str(mq.get("text") or "").strip()),
            "text": str(mq.get("text") or "")[:80],
        },
        "regions": regions,
    }


def load_layout(work: Path, *, video_w: int, video_h: int, duration: float) -> dict[str, Any]:
    path = layout_path(work)
    if not path.exists():
        return empty_layout(video_w=video_w, video_h=video_h, duration=duration)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = None
    return normalize_layout(raw if isinstance(raw, dict) else None, video_w=video_w, video_h=video_h, duration=duration)


def save_layout(work: Path, layout: dict[str, Any]) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    path = layout_path(work)
    path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def find_apply_source(job_id: str, job: dict, cfg: dict, preview: Path) -> Path:
    """Nguon apply: file goc (truoc editor), de xuat lai khong blur 2 lan."""
    work = job_work_dir(job_id, cfg)
    pinned = work / "editor_source.mp4"
    if pinned.is_file() and pinned.stat().st_size > 1000:
        return pinned
    out_dir = job_output_dir(cfg)
    orig = out_dir / f"{job_id}.mp4"
    if orig.is_file() and orig.resolve() != (out_dir / f"{job_id}_edit.mp4").resolve():
        return orig
    return preview


def find_preview_video(job_id: str, job: dict, cfg: dict) -> Path | None:
    """File de xem trong editor: output neu co, khong thi file work."""
    cands: list[Path] = []
    out = str(job.get("output_path") or "").strip()
    if out:
        p = Path(out).expanduser()
        if p.is_file():
            cands.append(p)
    work = job_work_dir(job_id, cfg)
    for name in ("edited.mp4", "wrapped.mp4"):
        p = work / name
        if p.is_file():
            cands.append(p)
    raw_dir = work / "raw"
    if raw_dir.is_dir():
        raws = [
            p for p in raw_dir.glob("*.mp4")
            if ".video." not in p.name and not p.name.endswith(".ocrblur.mp4")
        ]
        raws.sort(key=lambda x: x.stat().st_size, reverse=True)
        cands.extend(raws)
    out_dir = job_output_dir(cfg)
    for p in (out_dir / f"{job_id}.mp4", out_dir / f"{job_id}_edit.mp4"):
        if p.is_file():
            cands.append(p)
    seen: set[str] = set()
    for p in cands:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        if p.is_file() and p.stat().st_size > 1000:
            return p
    return None


def _px(frac: float, total: int) -> int:
    return max(0, min(total - 1, int(round(frac * total))))


def _even(n: int) -> int:
    n = max(2, int(n))
    return n if n % 2 == 0 else n - 1


def _even0(n: int) -> int:
    n = max(0, int(n))
    return n if n % 2 == 0 else n - 1


def _crop_box(xf: float, yf: float, wf: float, hf: float, aw: int, ah: int) -> tuple[int, int, int, int] | None:
    """Crop chan (yuv420) nam gon trong khung. None neu vung qua nho."""
    aw, ah = max(2, int(aw)), max(2, int(ah))
    x = _even0(_px(xf, aw))
    y = _even0(_px(yf, ah))
    w = _even(max(8, int(round(wf * aw))))
    h = _even(max(8, int(round(hf * ah))))
    if x + w > aw:
        w = _even(max(8, aw - x))
    if y + h > ah:
        h = _even(max(8, ah - y))
    if x + w > aw:
        x = _even0(max(0, aw - w))
    if y + h > ah:
        y = _even0(max(0, ah - h))
    if w < 8 or h < 8 or x + w > aw or y + h > ah:
        return None
    return x, y, w, h


def _blur_filter(w: int, h: int, sigma: int) -> str:
    """gblur khong vo chroma nhu boxblur tren vung chu thap."""
    cap = max(1.0, min(w, h) / 5.0)
    sig = max(1.0, min(float(sigma), cap))
    return f"gblur=sigma={sig:.2f}"


def _enable(t0: float, t1: float | None, duration: float) -> str:
    end = duration if t1 is None else min(t1, duration)
    return f"between(t,{t0:.3f},{max(t0 + 0.04, end):.3f})"


def _esc_drawtext(text: str) -> str:
    out = (
        text.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", "%%")
    )
    return out.replace("\n", r"\n")


def _ffmpeg_filter_path(p: str | Path) -> str:
    s = Path(p).resolve().as_posix()
    return (
        s.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(" ", r"\ ")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def _resolve_font(font_path: str = "") -> str:
    cands = [font_path]
    try:
        from .desktop_mode import default_font

        cands.append(default_font())
    except Exception:  # noqa: BLE001
        pass
    cands += [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    seen: set[str] = set()
    for raw in cands:
        p = Path(str(raw or "").strip())
        key = str(p)
        if not key or key in seen:
            continue
        seen.add(key)
        if p.is_file():
            return str(p.resolve())
    return ""


def _geom_filter(tf: dict, vw: int, vh: int) -> str | None:
    parts: list[str] = []
    if tf.get("hflip"):
        parts.append("hflip")
    rot = int(tf.get("rotate") or 0)
    if rot == 90:
        parts.append("transpose=1")
    elif rot == 180:
        parts.append("hflip,vflip")
    elif rot == 270:
        parts.append("transpose=2")
    zoom = float(tf.get("zoom") or 1.0)
    if zoom > 1.001:
        ow, oh = vw, vh
        if rot in (90, 270):
            ow, oh = vh, vw
        sw = _even(int(ow * zoom))
        sh = _even(int(oh * zoom))
        parts.append(f"scale={sw}:{sh}")
        parts.append(f"crop={_even(ow)}:{_even(oh)}:(iw-{_even(ow)})/2:(ih-{_even(oh)})/2")
    if not parts:
        return None
    return ",".join(parts)


def _blur_sigma(name: str) -> int:
    return BLUR_MAP.get(name, 16)


def build_filter_complex(
    layout: dict[str, Any],
    *,
    duration: float,
    font_path: str = "",
    has_logo: bool = False,
) -> tuple[str, str]:
    """Tra (filter_complex, output_label). Label luon [vout]."""
    vw = max(2, int(layout.get("video_w") or 1080))
    vh = max(2, int(layout.get("video_h") or 1920))
    tf = layout.get("transform") or {}
    chains: list[str] = []
    geom = _geom_filter(tf, vw, vh)
    if geom:
        chains.append(f"[0:v]{geom}[v0]")
        last = "[v0]"
    else:
        last = "[0:v]"

    rot = int(tf.get("rotate") or 0)
    aw, ah = (vh, vw) if rot in (90, 270) else (vw, vh)

    mask_regions = [
        r for r in (layout.get("regions") or [])
        if r.get("kind") in ("blur", "overlay")
        or (r.get("kind") == "sub" and str(r.get("text") or "").strip())
    ]
    for i, reg in enumerate(mask_regions):
        box = _crop_box(reg["x"], reg["y"], reg["w"], reg["h"], aw, ah)
        if box is None:
            log.warning("Editor skip region %s: crop ngoai khung", reg.get("id"))
            continue
        x, y, w, h = box
        sigma = _blur_sigma(str(reg.get("blur") or "medium"))
        src = f"s{i}"
        blur = f"b{i}"
        nxt = f"o{i}"
        enable = _enable(float(reg.get("t0") or 0), reg.get("t1"), duration)
        chains.append(f"{last}split[base{i}][{src}]")
        chains.append(
            f"[{src}]crop={w}:{h}:{x}:{y},{_blur_filter(w, h, sigma)}[{blur}]"
        )
        chains.append(
            f"[base{i}][{blur}]overlay={x}:{y}:enable='{enable}'[{nxt}]"
        )
        last = f"[{nxt}]"

    font_path = _resolve_font(font_path)
    font = ""
    if font_path:
        font = f":fontfile={_ffmpeg_filter_path(font_path)}"

    draw_i = 0
    for reg in mask_regions:
        text = str(reg.get("text") or "").strip()
        if not text:
            continue
        if not font:
            log.warning("Editor skip drawtext (khong tim thay font): %s", text[:40])
            continue
        box = _crop_box(reg["x"], reg["y"], reg["w"], reg["h"], aw, ah)
        if box is None:
            continue
        x, y, _w, box_h = box
        fs = max(14, min(72, int(box_h * 0.45)))
        enable = _enable(float(reg.get("t0") or 0), reg.get("t1"), duration)
        nxt = f"t{draw_i}"
        chains.append(
            f"{last}drawtext=text='{_esc_drawtext(text)}'"
            f"{font}:fontsize={fs}:fontcolor=white:borderw=2:bordercolor=black"
            f":x={x + 6}:y={y + max(2, box_h // 6)}:enable='{enable}'[{nxt}]"
        )
        last = f"[{nxt}]"
        draw_i += 1

    mq = layout.get("marquee") or {}
    if mq.get("enabled") and str(mq.get("text") or "").strip():
        nxt = "mq"
        chains.append(
            f"{last}drawtext=text='{_esc_drawtext(str(mq['text']).strip())}'"
            f"{font}:fontsize=28:fontcolor=white@0.78:borderw=1:bordercolor=black@0.6"
            f":x='w-mod(t*140\\,w+text_w+80)':y=h*0.06[{nxt}]"
        )
        last = f"[{nxt}]"

    logo = layout.get("logo") or {}
    if has_logo and logo.get("enabled"):
        lw = max(24, _px(float(logo.get("w") or 0.14), aw))
        opacity = float(logo.get("opacity") or 0.9)
        chains.append(f"[1:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={opacity:.3f}[lg]")
        if logo.get("bounce"):
            xexpr = (
                "if(lt(mod(t,8),2),12,"
                "if(lt(mod(t,8),4),main_w-overlay_w-12,"
                "if(lt(mod(t,8),6),12,main_w-overlay_w-12)))"
            )
            yexpr = (
                "if(lt(mod(t,8),2),12,"
                "if(lt(mod(t,8),4),12,"
                "if(lt(mod(t,8),6),main_h-overlay_h-12,main_h-overlay_h-12)))"
            )
            chains.append(f"{last}[lg]overlay=x='{xexpr}':y='{yexpr}'[vout]")
        else:
            x = _px(float(logo.get("x") or 0.04), aw)
            y = _px(float(logo.get("y") or 0.04), ah)
            chains.append(f"{last}[lg]overlay={x}:{y}[vout]")
        last = "[vout]"
    else:
        if last != "[vout]":
            if last == "[0:v]":
                chains.append("[0:v]null[vout]")
            else:
                chains.append(f"{last}null[vout]")
            last = "[vout]"

    return ";".join(chains), last


def _is_noop(layout: dict[str, Any], has_logo: bool) -> bool:
    tf = layout.get("transform") or {}
    if tf.get("hflip") or int(tf.get("rotate") or 0) or float(tf.get("zoom") or 1.0) > 1.001:
        return False
    if has_logo:
        return False
    mq = layout.get("marquee") or {}
    if mq.get("enabled") and str(mq.get("text") or "").strip():
        return False
    for r in layout.get("regions") or []:
        kind = r.get("kind")
        if kind in ("blur", "overlay"):
            return False
        if kind == "sub" and str(r.get("text") or "").strip():
            return False
    return True


def _run_editor_ffmpeg(cmd: list[str]) -> None:
    import subprocess

    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if proc.returncode == 0:
        return
    err = proc.stderr or ""
    log.error("Editor ffmpeg fail (%s):\n%s", proc.returncode, err[-2000:])
    useful: list[str] = []
    for line in err.splitlines():
        low = line.lower()
        if any(k in low for k in ("error", "invalid", "failed", "nothing was written", "cannot find")):
            useful.append(line.strip())
    hint = " | ".join(useful[-4:])[:420] if useful else (err.strip()[-280:] or "unknown")
    raise RuntimeError(f"ffmpeg that bai (code {proc.returncode}): {hint}")


def apply_layout(
    src: Path,
    dst: Path,
    layout: dict[str, Any],
    *,
    logo_path: Path | None = None,
    font_path: str = "",
) -> Path:
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = float(layout.get("duration") or 0) or probe_duration(src)
    layout = dict(layout)
    layout["duration"] = duration
    has_logo = bool(
        logo_path
        and Path(logo_path).is_file()
        and (
            (layout.get("logo") or {}).get("enabled")
            or (layout.get("logo") or {}).get("bounce")
        )
    )
    if has_logo:
        layout.setdefault("logo", {})["enabled"] = True
    if _is_noop(layout, has_logo):
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        log.info("Editor apply: khong co hieu ung, copy %s", dst.name)
        return dst
    fc, _vmap = build_filter_complex(
        layout, duration=duration, font_path=font_path, has_logo=has_logo,
    )
    tmp = dst.with_suffix(".editor_tmp.mp4")
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if has_logo:
        cmd += ["-i", str(logo_path)]
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-pix_fmt", "yuv420p"]
    if probe_has_audio(src):
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-movflags", "+faststart",
        str(tmp),
    ]
    log.info("Editor apply ffmpeg: %s -> %s", src.name, dst.name)
    _run_editor_ffmpeg(cmd)
    if dst.exists():
        dst.unlink()
    shutil.move(str(tmp), str(dst))
    return dst


def _iou_norm(a: dict, b: dict) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 1e-9 else 0.0


def _ocr_frame(img_bgr, *, min_score: float, pad: int) -> list[dict[str, Any]]:
    from .hardsub import _get_engine

    h, w = img_bgr.shape[:2]
    engine = _get_engine()
    result, _ = engine(img_bgr)
    out: list[dict[str, Any]] = []
    if not result:
        return out
    for item in result:
        if not item or len(item) < 3:
            continue
        box, text, score = item[0], item[1], item[2]
        try:
            sc = float(score)
        except (TypeError, ValueError):
            sc = 0.0
        if sc < min_score:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        x1 = max(0, int(min(xs)) - pad)
        y1 = max(0, int(min(ys)) - pad)
        x2 = min(w, int(max(xs)) + pad)
        y2 = min(h, int(max(ys)) + pad)
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        if (x2 - x1) * (y2 - y1) > 0.45 * w * h:
            continue
        out.append({
            "x": x1 / w,
            "y": y1 / h,
            "w": (x2 - x1) / w,
            "h": (y2 - y1) / h,
            "src_text": str(text or "")[:200],
            "score": round(sc, 3),
        })
    return out


def extract_frame_jpeg(src: Path, t: float, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-ss", f"{max(0.0, t):.3f}",
        "-i", str(src),
        "-frames:v", "1", "-q:v", "2",
        str(dest),
    ])
    return dest


def scan_text_regions(
    src: Path,
    *,
    t: float = 0.0,
    window_sec: float = 0.0,
    step_sec: float = 3.0,
    min_score: float = 0.35,
) -> list[dict[str, Any]]:
    """OCR 1 frame (hoac cua so thoi gian). Gop box chong nhau."""
    import cv2

    src = Path(src)
    duration = probe_duration(src)
    t = max(0.0, min(t, max(0.0, duration - 0.05)))
    times = [t]
    if window_sec > 0.2:
        cur = t
        end = min(duration, t + window_sec)
        while cur <= end + 1e-6:
            times.append(round(cur, 3))
            cur += max(0.8, step_sec)
        times = sorted(set(times))

    merged: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="dyflow_ocr_") as td:
        tmp = Path(td)
        for ts in times:
            jpg = tmp / f"f_{ts:.2f}.jpg"
            try:
                extract_frame_jpeg(src, ts, jpg)
            except RuntimeError:
                continue
            img = cv2.imread(str(jpg))
            if img is None:
                continue
            for box in _ocr_frame(img, min_score=min_score, pad=10):
                hit = None
                for m in merged:
                    if _iou_norm(m, box) >= 0.35:
                        hit = m
                        break
                if hit is None:
                    merged.append({
                        **box,
                        "t0": ts,
                        "t1": None if window_sec <= 0.2 else min(duration, ts + max(window_sec, 2.0)),
                    })
                else:
                    if box.get("score", 0) > hit.get("score", 0):
                        hit["src_text"] = box.get("src_text") or hit.get("src_text")
                        hit["score"] = box.get("score")
                    ax2 = hit["x"] + hit["w"]
                    ay2 = hit["y"] + hit["h"]
                    hit["x"] = min(hit["x"], box["x"])
                    hit["y"] = min(hit["y"], box["y"])
                    hit["w"] = max(ax2, box["x"] + box["w"]) - hit["x"]
                    hit["h"] = max(ay2, box["y"] + box["h"]) - hit["y"]
                    hit["t0"] = min(hit["t0"], ts)

    regions = []
    for i, m in enumerate(merged):
        regions.append(normalize_region({
            "id": f"ocr{i}_{uuid.uuid4().hex[:5]}",
            "kind": "overlay",
            "role": "overlay",
            "x": m["x"],
            "y": m["y"],
            "w": m["w"],
            "h": m["h"],
            "t0": m.get("t0") or t,
            "t1": m.get("t1"),
            "src_text": m.get("src_text") or "",
            "blur": "medium",
        }, duration=duration))
    log.info("Editor OCR: %d vung @ t=%.2f window=%.1fs", len(regions), t, window_sec)
    return regions


def save_logo(work: Path, data: bytes, suffix: str = ".png") -> Path:
    work.mkdir(parents=True, exist_ok=True)
    ext = suffix.lower() if suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    dest = work / (LOGO_NAME if ext == ".png" else f"editor_logo{ext}")
    dest.write_bytes(data)
    if dest.name != LOGO_NAME and ext != ".png":
        png = work / LOGO_NAME
        shutil.copy2(dest, png)
        return png
    return dest
