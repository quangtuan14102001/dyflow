"""Phat hien chu burned-in bang RapidOCR roi blur dung vung (mien phi, local).

Chay tren CPU/ONNX. OCR moi N frame, frame xen ke tai su dung bbox gan nhat
de vua nhan dien chu tren/giua/duoi vua khong cham qua cham.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .utils import log, probe_has_audio, run

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
        log.info("RapidOCR san sang (onnxruntime)")
    return _engine


def _boxes_from_ocr(result, min_score: float, pad: int, w: int, h: int) -> list[tuple[int, int, int, int]]:
    """RapidOCR result -> list (x1,y1,x2,y2) da pad + clip."""
    out: list[tuple[int, int, int, int]] = []
    if not result:
        return out
    for item in result:
        # item: [box(4 diem), text, score]
        if not item or len(item) < 3:
            continue
        box, _text, score = item[0], item[1], item[2]
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
        # bo box qua lon (thuong la nham canh/canh khung)
        if (x2 - x1) * (y2 - y1) > 0.45 * w * h:
            continue
        out.append((x1, y1, x2, y2))
    return out


def _blur_regions(frame: np.ndarray, boxes: list[tuple[int, int, int, int]], ksize: int) -> np.ndarray:
    import cv2

    if not boxes:
        return frame
    k = ksize if ksize % 2 == 1 else ksize + 1
    k = max(3, k)
    out = frame
    for x1, y1, x2, y2 in boxes:
        roi = out[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        blurred = cv2.GaussianBlur(roi, (k, k), 0)
        # copy de tranh view read-only
        if out is frame:
            out = frame.copy()
        out[y1:y2, x1:x2] = blurred
    return out


def blur_hardsubs_ocr(src: str | Path, out_path: str | Path, cfg: dict) -> Path:
    """Doc video, RapidOCR + blur chu, giu audio goc. Tra ve out_path."""
    import cv2

    src, out_path = Path(src), Path(out_path)
    every_n = max(1, int(cfg.get("ocr_every_n", 5)))
    pad = max(0, int(cfg.get("ocr_pad", 14)))
    blur = max(3, int(cfg.get("ocr_blur", 35)))
    min_score = float(cfg.get("ocr_min_score", 0.4))
    scale = float(cfg.get("ocr_scale", 0.6))
    scale = min(1.0, max(0.3, scale))

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"Khong mo duoc video de OCR: {src}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    tmp_video = out_path.with_suffix(".ocrblur.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(tmp_video), fourcc, fps, (w, h))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Khong tao duoc VideoWriter cho OCR blur")

    engine = _get_engine()
    boxes: list[tuple[int, int, int, int]] = []
    idx = 0
    hits = 0
    log.info(
        "OCR hardsub blur: every_n=%d scale=%.2f blur=%d (%dx%d ~%d frame)",
        every_n, scale, blur, w, h, nframes,
    )

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % every_n == 0:
                if scale < 0.999:
                    small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                else:
                    small = frame
                result, _ = engine(small)
                inv = 1.0 / scale
                raw_boxes = _boxes_from_ocr(result, min_score, 0, small.shape[1], small.shape[0])
                boxes = []
                for x1, y1, x2, y2 in raw_boxes:
                    boxes.append((
                        max(0, int(x1 * inv) - pad),
                        max(0, int(y1 * inv) - pad),
                        min(w, int(x2 * inv) + pad),
                        min(h, int(y2 * inv) + pad),
                    ))
                if boxes:
                    hits += 1
            frame = _blur_regions(frame, boxes, blur)
            writer.write(frame)
            idx += 1
            if idx % 150 == 0:
                log.info("OCR blur tien do: %d/%s frame", idx, nframes or "?")
    finally:
        cap.release()
        writer.release()

    log.info("OCR blur xong: %d frame, %d lan detect co chu", idx, hits)

    # Ghep lai audio tu file goc (OpenCV mat audio)
    if probe_has_audio(src):
        run([
            "ffmpeg", "-y",
            "-i", str(tmp_video),
            "-i", str(src),
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path),
        ])
        tmp_video.unlink(missing_ok=True)
    else:
        run([
            "ffmpeg", "-y", "-i", str(tmp_video),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-an", str(out_path),
        ])
        tmp_video.unlink(missing_ok=True)

    return out_path
