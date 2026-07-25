"""Buoc 2: BIEN DOI SAU - chong reused content that su.

Khong chi crop be mat. Cat video thanh nhieu DOAN, moi doan zoom/pan +
mau khac nhau roi noi lai -> doi ca perceptual hash lan dong chay hinh anh.
Them: doi nhip, lat tuy chon, loc nhieu audio goc, dich mau,
lam mo sub goc (burned-in), them vien tren/duoi (anti reup).

Luu y: day la bien doi KY THUAT, giam rui ro bi match. De ben vung that
su van can bien doi NOI DUNG (b-roll, ke chuyen khac) - xem README.
"""
from __future__ import annotations
from pathlib import Path

from .utils import log, run, probe_duration, probe_has_audio


def _maybe_ocr_blur(src: Path, work_dir: Path, cfg: dict) -> Path:
    """Neu mode ocr/both: chay RapidOCR blur truoc cac buoc transform khac."""
    sub = cfg.get("blur_burned_subs") or {}
    if not sub.get("enabled", False):
        return src
    mode = str(sub.get("mode", "fixed")).lower()
    if mode not in ("ocr", "both"):
        return src
    try:
        from . import hardsub
    except ImportError as e:
        log.warning("RapidOCR chua cai — bo qua OCR blur (%s). pip install rapidocr-onnxruntime opencv-python-headless", e)
        return src
    out = work_dir / f"{src.stem}_ocrblur.mp4"
    log.info("Dang OCR + blur chu burned-in (RapidOCR)...")
    return hardsub.blur_hardsubs_ocr(src, out, sub)

# vi tri pan luan phien cho tung doan (bieu thuc crop x:y)
_PAN = [
    ("(iw-ow)/2", "(ih-oh)/2"),  # giua
    ("0", "0"),                   # goc tren-trai
    ("(iw-ow)", "0"),             # goc tren-phai
    ("0", "(ih-oh)"),             # goc duoi-trai
    ("(iw-ow)", "(ih-oh)"),       # goc duoi-phai
]


def _atempo_chain(speed: float) -> str:
    chain, s = [], speed
    while s > 2.0:
        chain.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        chain.append("atempo=0.5"); s /= 0.5
    chain.append(f"atempo={s:.4f}")
    return ",".join(chain)


def _segment_video_filter(
    *,
    start: float,
    end: float,
    lbl: str,
    fit: str,
    W: int,
    H: int,
    z: float,
    px: str,
    py: str,
    eq: str,
) -> str:
    """fit: source=giu ty le goc | contain=vua khung+viền đen | cover=cat cho day khung."""
    head = f"[0:v]trim={start:.4f}:{end:.4f},setpts=PTS-STARTPTS,"
    if fit == "source":
        return f"{head}{eq}[{lbl}]"
    if fit == "contain":
        return (
            f"{head}scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,{eq}[{lbl}]"
        )
    sw, sh = round(W * z), round(H * z)
    return (
        f"{head}scale={sw}:{sh}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H}:{px}:{py},setsar=1,{eq}[{lbl}]"
    )


def _append_blur_subs(parts: list[str], vin: str, vout: str, cfg: dict) -> str:
    """Lam mo dai ngang co dinh (sub/watermark). Bo qua neu mode=ocr thuan."""
    sub = cfg.get("blur_burned_subs") or {}
    if not sub.get("enabled", False):
        parts.append(f"{vin}null{vout}")
        return vout
    mode = str(sub.get("mode", "fixed")).lower()
    # mode=ocr: da blur bang RapidOCR o buoc truoc
    if mode == "ocr":
        parts.append(f"{vin}null{vout}")
        return vout

    top = max(0.0, min(0.4, float(sub.get("top_pct", 0.0))))
    bot = max(0.0, min(0.4, float(sub.get("bottom_pct", 0.14))))
    strength = max(2, min(64, int(sub.get("strength", 16))))
    if top <= 0 and bot <= 0:
        parts.append(f"{vin}null{vout}")
        return vout

    cur = vin
    n = 0
    if top > 0:
        mid = "[vblur_top]"
        parts.append(
            f"{cur}split[bs{n}a][bs{n}b];"
            f"[bs{n}b]crop=iw:ih*{top:.4f}:0:0,"
            f"boxblur={strength}:{max(1, strength // 2)}[bt{n}];"
            f"[bs{n}a][bt{n}]overlay=0:0{mid}"
        )
        cur = mid
        n += 1
    if bot > 0:
        parts.append(
            f"{cur}split[bs{n}a][bs{n}b];"
            f"[bs{n}b]crop=iw:ih*{bot:.4f}:0:ih*(1-{bot:.4f}),"
            f"boxblur={strength}:{max(1, strength // 2)}[bb{n}];"
            f"[bs{n}a][bb{n}]overlay=0:main_h-overlay_h{vout}"
        )
        return vout
    parts.append(f"{cur}null{vout}")
    return vout


def _append_frame_border(parts: list[str], vin: str, vout: str, cfg: dict) -> str:
    """Them vien tren/duoi (blur pad hoac thanh mau) — doi hash + lanh khung."""
    border = cfg.get("frame_border") or {}
    if not border.get("enabled", False):
        parts.append(f"{vin}null{vout}")
        return vout

    top = max(0.0, min(0.25, float(border.get("top_pct", 0.05))))
    bot = max(0.0, min(0.25, float(border.get("bottom_pct", 0.05))))
    style = str(border.get("style", "blur")).lower()
    color = str(border.get("color", "black"))
    blur = max(4, min(48, int(border.get("blur", 20))))
    total = top + bot
    if total <= 0.001:
        parts.append(f"{vin}null{vout}")
        return vout

    inner = 1.0 - total
    if style == "solid":
        parts.append(
            f"{vin}scale=iw:'ih*{inner:.4f}',"
            f"pad=iw:ih:0:'ih*{top / inner:.4f}':{color}{vout}"
        )
        return vout

    # blur: nen = ban goc mo toan khung; noi dung thu nho dat le tren
    parts.append(
        f"{vin}split[fb_bg][fb_fg];"
        f"[fb_bg]boxblur={blur}:{max(1, blur // 2)}[fb_b];"
        f"[fb_fg]scale=iw:'ih*{inner:.4f}'[fb_s];"
        f"[fb_b][fb_s]overlay=0:main_h*{top:.4f}{vout}"
    )
    return vout


def transform(src: str | Path, out_path: str | Path, cfg: dict) -> Path:
    src, out_path = Path(src), Path(out_path)
    work_dir = out_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    src = _maybe_ocr_blur(src, work_dir, cfg)

    fit = str(cfg.get("fit", "cover")).lower()
    W = int(cfg.get("target_width", 1080))
    H = int(cfg.get("target_height", 1920))
    speed = float(cfg.get("speed_factor", 1.05))
    base_zoom = float(cfg.get("zoom", 1.05))
    zoom_step = float(cfg.get("zoom_step", 0.015))
    segments = max(1, int(cfg.get("segments", 3)))
    mirror = bool(cfg.get("mirror", False))
    trim_in = float(cfg.get("trim_intro_sec", 0.0))
    trim_out = float(cfg.get("trim_outro_sec", 0.0))
    denoise = bool(cfg.get("denoise_audio", True))

    contrast = float(cfg.get("contrast", 1.03))
    saturation = float(cfg.get("saturation", 1.05))
    gamma = float(cfg.get("gamma", 0.98))
    eq = f"eq=contrast={contrast}:saturation={saturation}:gamma={gamma}"

    dur = probe_duration(src)
    effective = max(0.5, dur - trim_in - trim_out)
    seg_len = effective / segments

    # --- dung chuoi filter cho tung doan ---
    parts, labels = [], []
    for i in range(segments):
        start = i * seg_len
        end = (i + 1) * seg_len if i < segments - 1 else effective
        z = base_zoom + i * zoom_step
        px, py = _PAN[i % len(_PAN)]
        lbl = f"v{i}"
        parts.append(_segment_video_filter(
            start=start, end=end, lbl=lbl, fit=fit, W=W, H=H,
            z=z, px=px, py=py, eq=eq,
        ))
        labels.append(f"[{lbl}]")

    concat = f"{''.join(labels)}concat=n={segments}:v=1:a=0[vc]"
    chain = []
    if mirror:
        chain.append("hflip")
    if abs(speed - 1.0) > 1e-3:
        chain.append(f"setpts={1 / speed:.4f}*PTS")
    if chain:
        post = f"[vc]{','.join(chain)}[vpost]"
        cur = "[vpost]"
    else:
        post = "[vc]null[vpost]"
        cur = "[vpost]"

    post_parts: list[str] = []
    cur = _append_blur_subs(post_parts, cur, "[vblur]", cfg)
    _append_frame_border(post_parts, cur, "[vout]", cfg)

    # audio (mot so clip Douyin / Chrome fallback chi co video)
    has_audio = probe_has_audio(src)
    maps = ["-map", "[vout]"]
    encode_a: list[str] = []
    if has_audio:
        afilters = []
        if denoise:
            afilters.append("afftdn=nf=-25")
        if abs(speed - 1.0) > 1e-3:
            afilters.append(_atempo_chain(speed))
        if not afilters:
            afilters.append("anull")
        aproc = f"[0:a]{','.join(afilters)}[aout]"
        fc = ";".join(parts + [concat, post] + post_parts + [aproc])
        maps += ["-map", "[aout]"]
        encode_a = ["-c:a", "aac", "-b:a", "128k"]
    else:
        log.warning("Transform: file khong co audio -> xuat video only")
        fc = ";".join(parts + [concat, post] + post_parts)
        encode_a = ["-an"]

    cmd = ["ffmpeg", "-y"]
    if trim_in > 0:
        cmd += ["-ss", f"{trim_in}"]
    cmd += ["-t", f"{effective}", "-i", str(src),
            "-filter_complex", fc,
            *maps,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            *encode_a,
            str(out_path)]
    run(cmd)
    blur_on = bool((cfg.get("blur_burned_subs") or {}).get("enabled"))
    border_on = bool((cfg.get("frame_border") or {}).get("enabled"))
    log.info(
        "Transform sau xong (%d doan, fit=%s, blur_subs=%s, border=%s): %s",
        segments, fit, blur_on, border_on, out_path.name,
    )
    return out_path
