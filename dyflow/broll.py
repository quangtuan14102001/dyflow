"""Tuy chon: chen b-roll (clip rieng cua ban) vao dau/cuoi video.

Day la bien doi NOI DUNG that su - tin hieu "original" manh hon nhieu so
voi chi chinh khung hinh. Tat mac dinh vi:
  - Can footage rieng cua ban (intro brand, logo, canh quay them...)
  - Lam thay doi tong thoi luong -> can canh script long tieng cho khop.
Bat trong config: transform.broll.enabled = true
"""
from __future__ import annotations
from pathlib import Path

from .utils import log, run


def _norm_inputs(paths: list[str], W: int, H: int, fps: int) -> tuple[list[str], str]:
    """Tao filtergraph normalize tat ca clip ve cung WxH/fps/SAR + audio."""
    cmd_inputs, vparts, concat_in = [], [], []
    for idx, p in enumerate(paths):
        cmd_inputs += ["-i", str(p)]
        vparts.append(
            f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1,fps={fps},format=yuv420p[v{idx}]"
        )
        vparts.append(
            f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{idx}]"
        )
        concat_in.append(f"[v{idx}][a{idx}]")
    n = len(paths)
    concat = f"{''.join(concat_in)}concat=n={n}:v=1:a=1[v][a]"
    fc = ";".join(vparts + [concat])
    return cmd_inputs, fc


def wrap(main_video: str | Path, out_path: str | Path,
         intro: str | None, outro: str | None,
         W: int = 1080, H: int = 1920, fps: int = 30) -> Path:
    """Noi: [intro] + main + [outro]. intro/outro co the None."""
    seq = []
    if intro and Path(intro).exists():
        seq.append(intro)
    seq.append(str(main_video))
    if outro and Path(outro).exists():
        seq.append(outro)

    if len(seq) == 1:  # khong co b-roll
        return Path(main_video)

    cmd_inputs, fc = _norm_inputs(seq, W, H, fps)
    cmd = ["ffmpeg", "-y", *cmd_inputs, "-filter_complex", fc,
           "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", str(out_path)]
    run(cmd)
    log.info("B-roll wrap xong: %s", Path(out_path).name)
    return Path(out_path)
