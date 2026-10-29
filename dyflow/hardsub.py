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


def _boxes_from_ocr(
    result, min_score: float, pad: int, w: int, h: int, y_offset: int = 0, inv: float = 1.0,
) -> list[tuple[int, int, int, int]]:
    """RapidOCR result -> list (x1,y1,x2,y2) da pad + clip ve toa do frame goc."""
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
        xs = [float(p[0]) * inv for p in box]
        ys = [float(p[1]) * inv + y_offset for p in box]
        x1 = max(0, int(min(xs)) - pad)
        y1 = max(0, int(min(ys)) - pad)
        x2 = min(w, int(max(xs)) + pad)
        y2 = min(h, int(max(ys)) + pad)
        if x2 - x1 < 6 or y2 - y1 < 6:
            continue
        # bo box qua lon (thuong la nham canh/canh khung)
        if (x2 - x1) * (y2 - y1) > 0.45 * w * h:
            continue
        out.append((x1, y1, x2, y2))
    return out


def _merge_subtitle_lines(
    raw_boxes: list[tuple[int, int, int, int]],
    w: int,
    h: int,
    pad_x: int = 24,
    pad_y: int = 12,
    auto_center: bool = True,
) -> list[tuple[int, int, int, int]]:
    """Gom cac tu/cum tu rieng le tren cung 1 hang thanh 1 thanh chu nhat phu de lien mach."""
    if not raw_boxes:
        return []

    # Sap xep theo truc Y (tu tren xuong duoi)
    boxes = sorted(raw_boxes, key=lambda b: (b[1] + b[3]) / 2)
    lines: list[list[tuple[int, int, int, int]]] = []

    for b in boxes:
        bx1, by1, bx2, by2 = b
        bc_y = (by1 + by2) / 2
        bh = max(1, by2 - by1)

        matched_line = None
        for line in lines:
            line_cy = sum((it[1] + it[3]) / 2 for it in line) / len(line)
            line_h = max(it[3] - it[1] for it in line)
            overlap_y = max(
                0,
                min(by2, max(it[3] for it in line)) - max(by1, min(it[1] for it in line)),
            )
            # Nam cung do cao Y hoac overlap doc >= 40%
            if abs(bc_y - line_cy) <= max(bh, line_h) * 0.65 or overlap_y >= min(bh, line_h) * 0.4:
                matched_line = line
                break

        if matched_line is not None:
            matched_line.append(b)
        else:
            lines.append([b])

    merged: list[tuple[int, int, int, int]] = []
    for line in lines:
        x1 = max(0, min(b[0] for b in line) - pad_x)
        y1 = max(0, min(b[1] for b in line) - pad_y)
        x2 = min(w, max(b[2] for b in line) + pad_x)
        y2 = min(h, max(b[3] for b in line) + pad_y)

        # Neu dong chu phu de can giua hoac rong >= 25% chieu ngang video:
        # Can xung quanh truc giua man hinh de thanh che vuong van, dep mat
        line_w = x2 - x1
        mid_x = w / 2
        line_cx = (x1 + x2) / 2
        if auto_center and line_w >= 0.25 * w and abs(line_cx - mid_x) < 0.22 * w:
            half = max(mid_x - x1, x2 - mid_x)
            margin_x = int(w * 0.04)
            x1 = max(margin_x, int(mid_x - half))
            x2 = min(w - margin_x, int(mid_x + half))

        merged.append((x1, y1, x2, y2))

    return merged


class SubtitleTracker:
    """Theo doi va giu vet che phu de on dinh xuyen suot thoi gian nhan vat noi.

    Triet tieu 100% hien tuong chop nhay (flickering) do OCR frame bi hut hoac mo chuyen dong.
    """

    def __init__(self, fps: float, hold_sec: float = 0.8):
        self.hold_frames = max(5, int(fps * hold_sec))
        self.active_boxes: list[tuple[int, int, int, int]] = []
        self.hold_countdown = 0
        self.all_detected_y: list[int] = []
        self.all_detected_h: list[int] = []

    def update(
        self, detected_boxes: list[tuple[int, int, int, int]]
    ) -> list[tuple[int, int, int, int]]:
        if detected_boxes:
            self.hold_countdown = self.hold_frames

            # Ghi nhan toa do Y cua toan bo video de can vi tri cho Sub tieng Viet
            for b in detected_boxes:
                cy = int((b[1] + b[3]) / 2)
                self.all_detected_y.append(cy)
                self.all_detected_h.append(b[3] - b[1])

            if not self.active_boxes:
                self.active_boxes = detected_boxes
            else:
                smoothed = []
                for nb in detected_boxes:
                    nx1, ny1, nx2, ny2 = nb
                    n_cy = (ny1 + ny2) / 2

                    match = None
                    for ob in self.active_boxes:
                        o_cy = (ob[1] + ob[3]) / 2
                        if abs(n_cy - o_cy) < max(28, (ny2 - ny1) * 0.6):
                            match = ob
                            break

                    if match:
                        ox1, oy1, ox2, oy2 = match
                        # EMA 75% cu + 25% moi giup box dung yen em ai, khong bi rung giat
                        sy1 = int(0.75 * oy1 + 0.25 * ny1)
                        sy2 = int(0.75 * oy2 + 0.25 * ny2)
                        sx1 = min(ox1, nx1)
                        sx2 = max(ox2, nx2)
                        smoothed.append((sx1, sy1, sx2, sy2))
                    else:
                        smoothed.append(nb)
                self.active_boxes = smoothed
        else:
            if self.hold_countdown > 0:
                self.hold_countdown -= 1
            else:
                self.active_boxes = []

        return self.active_boxes

    def get_summary(self, w: int, h: int) -> dict:
        if not self.all_detected_y:
            return {
                "detected": False,
                "y_center": None,
                "margin_v": None,
                "y_ratio": None,
                "box_h": None,
                "count": 0,
            }
        # Loc phu de loi thoai vs watermark canh bao duoi day (y > 90%):
        # Tranh truong hop dong chu '剧情纯属虚构' xuat hien 100% cac frame
        # keo tuot toa do Y cua sub tieng Viet xuong sat day va lam lech khoi vung thoai.
        dialogue_boxes = [
            (y, h_box)
            for y, h_box in zip(self.all_detected_y, self.all_detected_h)
            if y <= int(h * 0.90)
        ]
        if dialogue_boxes:
            med_y = int(np.median([y for y, _ in dialogue_boxes]))
            med_h = int(np.median([hb for _, hb in dialogue_boxes]))
        else:
            med_y = int(np.median(self.all_detected_y))
            med_h = int(np.median(self.all_detected_h)) if self.all_detected_h else 50

        margin_v = max(40, h - med_y)
        return {
            "detected": True,
            "y_center": med_y,
            "margin_v": margin_v,
            "y_ratio": round(med_y / max(1, h), 4),
            "box_h": med_h,
            "count": len(self.all_detected_y),
        }


def _blur_regions(
    frame: np.ndarray, boxes: list[tuple[int, int, int, int]], ksize: int
) -> np.ndarray:
    """Lam mo cac vung hop chu nhat voi soft-edge (feathering) dep mat."""
    import cv2

    if not boxes:
        return frame
    k = ksize if ksize % 2 == 1 else ksize + 1
    k = max(3, k)
    h, w = frame.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for x1, y1, x2, y2 in boxes:
        x1_c, y1_c = max(0, x1), max(0, y1)
        x2_c, y2_c = min(w, x2), min(h, y2)
        if x2_c > x1_c and y2_c > y1_c:
            mask[y1_c:y2_c, x1_c:x2_c] = 255
    if not np.any(mask):
        return frame

    # Lam min vien mask (feathering) de vet mo khong bi canh sac vuong van
    mask_soft = cv2.GaussianBlur(mask, (15, 15), 0)
    alpha = (mask_soft.astype(np.float32) / 255.0)[:, :, None]

    blurred = cv2.GaussianBlur(frame, (k, k), 0)
    # Phu nhe mau toi (30%) de chu goc (dac biet la chu trang vien den) khong con vet mo
    blurred_dark = (blurred.astype(np.float32) * 0.70).astype(np.uint8)

    # Blend mượt mà giữa ảnh gốc và ảnh mờ
    out = (frame.astype(np.float32) * (1.0 - alpha) + blurred_dark.astype(np.float32) * alpha).astype(np.uint8)
    return out


def blur_hardsubs_ocr(
    src: str | Path,
    out_path: str | Path,
    cfg: dict,
    meta_out: dict | None = None,
) -> Path:
    """Doc video, AI RapidOCR subtitle tracking + blur chu, giu audio goc.

    Cai tien:
    1. ROI: Chi scan vung phu de duoi day (nhanh gap 3x, khong bao gio dinh ao/bien hieu/logo).
    2. Line Merging: Tu dong gom cac tu rieng le thanh 1 thanh chu nhat vuong van, dep mat.
    3. SubtitleTracker: Khau dem chong nhay xuyen suot cau noi.
    4. meta_out: Tra ve toa do Y cua phu de cu de Sub tieng Viet dat dung tam vet mo.
    """
    import cv2

    src, out_path = Path(src), Path(out_path)
    every_n = max(1, int(cfg.get("ocr_every_n", 12)))
    pad = max(0, int(cfg.get("ocr_pad", 14)))
    blur = max(3, int(cfg.get("ocr_blur", 35)))
    min_score = float(cfg.get("ocr_min_score", 0.35))
    scale = float(cfg.get("ocr_scale", 0.6))
    scale = min(1.0, max(0.3, scale))
    hold_sec = float(cfg.get("ocr_hold_sec", 0.8))
    auto_center = bool(cfg.get("ocr_auto_center", True))

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"Khong mo duoc video de OCR: {src}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    # 1. Xac dinh ROI (Vung phu de day man hinh):
    # Video doc 9:16 (h > w): scan tu 40% -> 99% chieu cao (bao gom ca phu de nguc/bung va watermark canh bao duoi day)
    # Video ngang 16:9 (w >= h): scan tu 55% -> 99% chieu cao
    is_portrait = h > w
    roi_top_default = 0.40 if is_portrait else 0.55
    roi_bot_default = 0.99 if is_portrait else 0.99

    roi_top = float(cfg.get("ocr_roi_top", roi_top_default))
    roi_bot = float(cfg.get("ocr_roi_bottom", roi_bot_default))
    roi_top = max(0.0, min(0.9, roi_top))
    roi_bot = max(roi_top + 0.05, min(1.0, roi_bot))

    y_roi_start = int(h * roi_top)
    y_roi_end = int(h * roi_bot)

    tmp_video = out_path.with_suffix(".ocrblur.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(tmp_video), fourcc, fps, (w, h))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Khong tao duoc VideoWriter cho OCR blur")

    engine = _get_engine()
    tracker = SubtitleTracker(fps=fps, hold_sec=hold_sec)
    boxes: list[tuple[int, int, int, int]] = []
    idx = 0
    hits = 0
    log.info(
        "AI OCR hardsub tracking: ROI y=[%d..%d] (%.0f%%-%.0f%%) every_n=%d scale=%.2f blur=%d (%dx%d ~%d frame)",
        y_roi_start, y_roi_end, roi_top * 100, roi_bot * 100,
        every_n, scale, blur, w, h, nframes,
    )

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % every_n == 0:
                # Cat chi rieng vung ROI de OCR (toc do nhanh gap 3 lan, mien nhiem voi ao/bien hieu/logo)
                roi_frame = frame[y_roi_start:y_roi_end, :]
                if scale < 0.999:
                    small_roi = cv2.resize(roi_frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                else:
                    small_roi = roi_frame

                result, _ = engine(small_roi)
                inv = 1.0 / scale
                raw_boxes = _boxes_from_ocr(
                    result, min_score, 0, w, y_roi_end,
                    y_offset=y_roi_start, inv=inv,
                )
                # Gom cac cum tu tren cung 1 dong thanh 1 thanh chu nhat lien mach
                merged = _merge_subtitle_lines(
                    raw_boxes, w, h,
                    pad_x=pad + 8, pad_y=pad,
                    auto_center=auto_center,
                )
                boxes = tracker.update(merged)
                if merged:
                    hits += 1
            else:
                # Cac frame xen ke: tiep tuc update bo dem chong nhay (giu vet mo khi dang noi)
                boxes = tracker.update([])

            frame = _blur_regions(frame, boxes, blur)
            writer.write(frame)
            idx += 1
            if idx % 150 == 0:
                log.info("OCR blur tien do: %d/%s frame", idx, nframes or "?")
    finally:
        cap.release()
        writer.release()

    summary = tracker.get_summary(w, h)
    if meta_out is not None:
        meta_out.update(summary)

    log.info(
        "AI OCR tracking xong: %d frame, %d lan bat duoc chu. Y_center=%s, MarginV=%s",
        idx, hits, summary.get("y_center"), summary.get("margin_v"),
    )

    # Ghep lai audio tu file goc (OpenCV mat audio)
    if probe_has_audio(src):
        run([
            "ffmpeg", "-y",
            "-i", str(tmp_video),
            "-i", str(src),
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-c:a", "copy",
            "-shortest",
            str(out_path),
        ])
        tmp_video.unlink(missing_ok=True)
    else:
        run([
            "ffmpeg", "-y", "-i", str(tmp_video),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-an", str(out_path),
        ])
        tmp_video.unlink(missing_ok=True)

    return out_path
