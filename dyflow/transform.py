"""Buoc 2: BIEN DOI SAU - chong reused content that su.

Khong chi crop be mat. Cat video thanh nhieu DOAN, moi doan zoom/pan +
mau khac nhau roi noi lai -> doi ca perceptual hash lan dong chay hinh anh.
Them: doi nhip, lat tuy chon, loc nhieu audio goc, dich mau.

Luu y: day la bien doi KY THUAT, giam rui ro bi match. De ben vung that
su van can bien doi NOI DUNG (b-roll, ke chuyen khac) - xem README.
"""
from __future__ import annotations
import shutil
from pathlib import Path

from .utils import log, run, probe_duration

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


def _near(v: object, target: float = 1.0, eps: float = 1e-3) -> bool:
    return abs(float(v) - target) <= eps


def _is_noop(cfg: dict) -> bool:
    """Khong doi hinh/am — copy file, khong encode lai (ARM VM treo 1h+)."""
    fit = str(cfg.get("fit", "cover")).lower()
    if fit not in ("source", ""):
        return False
    if int(cfg.get("segments", 3)) > 1:
        return False
    if bool(cfg.get("mirror", False)):
        return False
    if bool(cfg.get("denoise_audio", False)):
        return False
    if not _near(cfg.get("speed_factor", 1.0), 1.0):
        return False
    if not _near(cfg.get("zoom", 1.0), 1.0):
        return False
    if abs(float(cfg.get("zoom_step", 0.0) or 0)) > 1e-6:
        return False
    if abs(float(cfg.get("trim_intro_sec", 0) or 0)) > 1e-6:
        return False
    if abs(float(cfg.get("trim_outro_sec", 0) or 0)) > 1e-6:
        return False
    if not _near(cfg.get("contrast", 1.0), 1.0):
        return False
    if not _near(cfg.get("saturation", 1.0), 1.0):
        return False
    if not _near(cfg.get("gamma", 1.0), 1.0):
        return False
    return True


def transform(src: str | Path, out_path: str | Path, cfg: dict) -> Path:
    src, out_path = Path(src), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_noop(cfg):
        if src.resolve() != out_path.resolve():
            shutil.copy2(src, out_path)
        log.info("Transform skip encode (noop copy): %s", out_path.name)
        return out_path

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
    denoise = bool(cfg.get("denoise_audio", False))

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
    post = "[vc]"
    chain = []
    if mirror:
        chain.append("hflip")
    if abs(speed - 1.0) > 1e-3:
        chain.append(f"setpts={1/speed:.4f}*PTS")
    if chain:
        post = f"[vc]{','.join(chain)}[vout]"
        vmap = "[vout]"
    else:
        post = "[vc]null[vout]"
        vmap = "[vout]"

    # audio
    afilters = []
    if denoise:
        afilters.append("afftdn=nf=-25")
    if abs(speed - 1.0) > 1e-3:
        afilters.append(_atempo_chain(speed))
    if not afilters:
        afilters.append("anull")
    aproc = f"[0:a]{','.join(afilters)}[aout]"

    fc = ";".join(parts + [concat, post, aproc])

    cmd = ["ffmpeg", "-y"]
    if trim_in > 0:
        cmd += ["-ss", f"{trim_in}"]
    cmd += ["-t", f"{effective}", "-i", str(src),
            "-filter_complex", fc,
            "-map", vmap, "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            str(out_path)]
    run(cmd)
    log.info("Transform sau xong (%d doan, fit=%s): %s", segments, fit, out_path.name)
    return out_path
