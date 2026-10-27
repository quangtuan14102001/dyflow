"""Module ghep nhieu video thanh 1 video duy nhat (Video Merger).

Ho tro:
- Chon nhieu video tu folder / file picker.
- Sap xep thu tu ghep.
- Chuan hoa khung hinh (fit=contain/cover, ty le first/9:16/16:9/1:1).
- Chuan hoa am thanh (tu sinh silent audio neu co clip khong tieng).
- Fast stream copy (neu tat ca clip cung format) + fallback filter complex tu dong.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .utils import (
    ensure_media_bins,
    ffmpeg_path,
    ffprobe_path,
    log,
    probe_duration,
    probe_has_audio,
    probe_video_size,
    run,
    win_hidden_kwargs,
)

ASPECT_MAP: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


def _even(val: int) -> int:
    return val if val % 2 == 0 else val + 1


def _can_fast_copy(video_paths: list[Path]) -> bool:
    """Kiem tra xem cac clip co cung do phan giai, fps va audio khong (co lay mau neu danh sach lon)."""
    if len(video_paths) <= 1:
        return True
    try:
        # Neu danh sach dai (> 10 files), kiem tra mau de tranh chay ffprobe hang tram lan gay treo
        sample_paths = [video_paths[0], video_paths[-1]]
        if len(video_paths) > 2:
            sample_paths.insert(1, video_paths[1])
            sample_paths.insert(2, video_paths[len(video_paths) // 2])

        ref_w, ref_h = probe_video_size(sample_paths[0])
        ref_audio = probe_has_audio(sample_paths[0])
        for p in sample_paths[1:]:
            w, h = probe_video_size(p)
            if (w, h) != (ref_w, ref_h):
                return False
            if probe_has_audio(p) != ref_audio:
                return False
        return True
    except Exception:
        return False


def _try_stream_copy_merge(
    video_paths: list[Path],
    out_path: Path,
    timeout: int = 1800,
    cancel_check: Callable[[], bool] | None = None,
) -> bool:
    """Thu ghep nhanh bang ffmpeg concat demuxer (stream copy khong encode)."""
    if cancel_check and cancel_check():
        return False
    with tempfile.TemporaryDirectory() as td:
        list_file = Path(td) / "concat_list.txt"
        lines = []
        for p in video_paths:
            # QUAN TRONG: Chuyen thanh forward slashes '/' de FFmpeg concat tren Windows khong loi escape
            posix_path = p.resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{posix_path}'\n")
        list_file.write_text("".join(lines), encoding="utf-8")

        tmp_out = out_path.with_suffix(".copytmp.mp4")
        ensure_media_bins()
        cmd = [
            ffmpeg_path(), "-y",
            "-fflags", "+genpts+discardcorrupt",
            "-avoid_negative_ts", "make_zero",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            "-max_muxing_queue_size", "2048",
            "-movflags", "+faststart",
            str(tmp_out),
        ]
        try:
            real_timeout = max(timeout, len(video_paths) * 20)
            res = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=real_timeout,
                check=False,
                **win_hidden_kwargs(below_normal=True),
            )
            if cancel_check and cancel_check():
                return False
            if res.returncode == 0 and tmp_out.is_file() and tmp_out.stat().st_size > 1000:
                if out_path.exists():
                    out_path.unlink()
                shutil.move(str(tmp_out), str(out_path))
                log.info("Fast stream copy merge thanh cong: %s (%d files)", out_path.name, len(video_paths))
                return True
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace") if res.stderr else ""
                log.warning("Stream copy exit code %d: %s", res.returncode, err_msg[:300])
        except Exception as e:
            log.warning("Fast stream copy failed: %s -> chuyen sang encode filter", e)
        finally:
            if tmp_out.exists():
                tmp_out.unlink(missing_ok=True)
    return False


def merge_videos(
    video_paths: list[str | Path],
    out_path: str | Path,
    *,
    aspect_ratio: str = "first",
    fit: str = "contain",
    fast_copy: bool = True,
    fps: int = 30,
    progress_callback: Callable[[float, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Path:
    """Ghep danh sach cac video theo dung thu tu vao out_path.

    - aspect_ratio: "first" (theo clip dau), "9:16", "16:9", "1:1"
    - fit: "contain" (giu tron khung them vien den), "cover" (cat vua khung)
    - fast_copy: neu True, thu dung concat demuxer copy stream truoc (sieu toc, giu 100% chat luong)
    - cancel_check: ham kiem tra xem tien trinh co bi yeu cau dung/huy khong
    """
    import uuid
    ensure_media_bins()

    if cancel_check and cancel_check():
        raise RuntimeError("Tác vụ ghép video đã bị hủy theo yêu cầu.")

    paths = [Path(p).resolve() for p in video_paths if Path(p).is_file()]
    if not paths:
        raise ValueError("Khong co file video hop le nao de ghep.")

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(5.0, "Kiem tra danh sach video...")

    # Neu chi co 1 video va khong can doi ty le: copy hoac encode don gian
    if len(paths) == 1 and aspect_ratio == "first":
        if paths[0] != out_path:
            shutil.copy2(paths[0], out_path)
        if progress_callback:
            progress_callback(100.0, "Hoan thanh")
        return out_path

    # Thu fast stream copy neu clip phu hop (sieu toc ~2-3 giay)
    if fast_copy and aspect_ratio == "first":
        if _can_fast_copy(paths):
            log.info("Cac clip co cung do phan giai -> Thu ghep sieu toc Stream Copy...")
            if progress_callback:
                progress_callback(20.0, "Dang ghep sieu toc (stream copy)...")
            if _try_stream_copy_merge(paths, out_path):
                if progress_callback:
                    progress_callback(100.0, "Hoan thanh ghep video")
                return out_path
            log.info("Stream copy khong kha thi -> Chuyen sang re-encode...")

    # Neu so luong video lon (> 30 clips), tu dong chia thanh cac chunk de:
    # 1. Tranh loi [WinError 206] The filename or extension is too long (gioi han 32767 ky tu cua Windows).
    # 2. Tranh mo qua nhieu file descriptor cung luc gay crash FFmpeg.
    CHUNK_SIZE = 30
    if len(paths) > CHUNK_SIZE:
        log.info(
            "So luong video lon (%d clips) -> Tu dong chia nho (%d clips/phan) de ghep an toan, chong tran dong lenh Windows...",
            len(paths),
            CHUNK_SIZE,
        )
        chunks = [paths[i:i + CHUNK_SIZE] for i in range(0, len(paths), CHUNK_SIZE)]
        part_files: list[Path] = []
        temp_dir = out_path.parent / f".tmp_chunk_{uuid.uuid4().hex[:8]}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            for idx, chunk in enumerate(chunks):
                if cancel_check and cancel_check():
                    raise RuntimeError("Tác vụ ghép video đã bị hủy theo yêu cầu.")
                part_path = temp_dir / f"part_{idx:03d}.mp4"
                if progress_callback:
                    pct = 10.0 + (idx / len(chunks)) * 80.0
                    progress_callback(
                        round(pct, 1),
                        f"Dang ghep phan {idx + 1}/{len(chunks)} ({len(chunk)} video)...",
                    )
                merge_videos(
                    chunk,
                    part_path,
                    aspect_ratio=aspect_ratio,
                    fit=fit,
                    fast_copy=fast_copy,
                    fps=fps,
                    cancel_check=cancel_check,
                )
                part_files.append(part_path)

            if cancel_check and cancel_check():
                raise RuntimeError("Tác vụ ghép video đã bị hủy theo yêu cầu.")

            if progress_callback:
                progress_callback(92.0, "Dang noi cac phan thanh video hoan chinh...")
            if _try_stream_copy_merge(part_files, out_path, cancel_check=cancel_check):
                if progress_callback:
                    progress_callback(100.0, "Hoan thanh ghep video")
                return out_path

            # Fallback noi tiep neu can
            return merge_videos(
                part_files,
                out_path,
                aspect_ratio=aspect_ratio,
                fit=fit,
                fast_copy=False,
                fps=fps,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Xac dinh do phan giai dich W, H
    if aspect_ratio in ASPECT_MAP:
        target_w, target_h = ASPECT_MAP[aspect_ratio]
    else:  # "first"
        try:
            fw, fh = probe_video_size(paths[0])
            target_w, target_h = _even(fw), _even(fh)
        except Exception:
            target_w, target_h = (1080, 1920)

    W, H = _even(target_w), _even(target_h)
    fit_mode = fit.lower().strip() if fit in ("contain", "cover") else "contain"

    # Xay dung filter_complex cho concat
    cmd_inputs: list[str] = []
    vparts: list[str] = []
    aparts: list[str] = []
    concat_inputs: list[str] = []
    total_duration = 0.0

    if progress_callback:
        progress_callback(15.0, "Phan tich am thanh & kich thuoc cac clip...")

    for idx, p in enumerate(paths):
        cmd_inputs.extend(["-i", str(p.resolve())])
        try:
            dur = probe_duration(p)
        except Exception:
            dur = 1.0
        dur = max(0.1, dur)
        total_duration += dur

        has_audio = probe_has_audio(p)

        # Video filter: setpts reset moc thoi gian ve 0, scale/crop/pad, setsar 1:1, fps chuan, yuv420p
        if fit_mode == "cover":
            vf = (
                f"[{idx}:v]setpts=PTS-STARTPTS,scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:(iw-ow)/2:(ih-oh)/2,setsar=1,fps={fps},format=yuv420p[v{idx}]"
            )
        else:  # contain
            vf = (
                f"[{idx}:v]setpts=PTS-STARTPTS,scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p[v{idx}]"
            )
        vparts.append(vf)

        # Audio filter: asetpts reset moc am thanh, aformat 44100Hz stereo, aresample chong lech tieng
        if has_audio:
            af = f"[{idx}:a]asetpts=PTS-STARTPTS,aformat=sample_rates=44100:channel_layouts=stereo,aresample=async=1000[a{idx}]"
        else:
            af = f"anullsrc=channel_layout=stereo:sample_rate=44100:d={dur:.3f}[a{idx}]"
        aparts.append(af)

        concat_inputs.append(f"[v{idx}][a{idx}]")

    n = len(paths)
    concat_line = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[vout][aout]"
    fc = ";".join(vparts + aparts + [concat_line])

    # Dung file script ASCII sach chua filter_complex de tranh loi encoding ten tieng Viet va gioi han Windows
    fc_file = out_path.parent / f".fc_{uuid.uuid4().hex[:8]}.txt"
    fc_file.write_text(fc, encoding="utf-8")

    from .hardware import probe_best_h264_encoder

    enc_name, enc_args = probe_best_h264_encoder()
    log.info("Su dung video encoder cho ghep video: %s", enc_name)

    tmp_out = out_path.with_suffix(".mergetmp.mp4")
    cmd = [
        ffmpeg_path(), "-y",
        *cmd_inputs,
        "-filter_complex_script", str(fc_file),
        "-map", "[vout]",
        "-map", "[aout]",
        *enc_args,
        "-pix_fmt", "yuv420p",
        "-threads", "0",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-movflags", "+faststart",
        str(tmp_out),
    ]

    log.info("Bat dau ghep %d video -> %s (%dx%d, fit=%s, fps=%d)", n, out_path.name, W, H, fit_mode, fps)
    if progress_callback:
        progress_callback(30.0, f"Dang xu ly ghep {n} video qua FFmpeg...")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            **win_hidden_kwargs(below_normal=True),
        )

        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        recent_lines: list[str] = []
        while True:
            if cancel_check and cancel_check():
                try:
                    proc.kill()
                except Exception:
                    pass
                if tmp_out.exists():
                    tmp_out.unlink(missing_ok=True)
                raise RuntimeError("Tác vụ ghép video đã bị hủy theo yêu cầu.")

            line = proc.stderr.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                cleaned = line.strip()
                if cleaned:
                    recent_lines.append(cleaned)
                    if len(recent_lines) > 20:
                        recent_lines.pop(0)
            if line and progress_callback and total_duration > 0:
                m = time_pattern.search(line)
                if m:
                    hours, mins, secs = float(m.group(1)), float(m.group(2)), float(m.group(3))
                    cur_sec = hours * 3600 + mins * 60 + secs
                    pct = min(98.0, 30.0 + (cur_sec / total_duration) * 65.0)
                    progress_callback(round(pct, 1), f"Dang ghep video ({round(pct)}%)...")

        rc = proc.wait()
        if rc != 0 or not tmp_out.is_file() or tmp_out.stat().st_size < 1000:
            if tmp_out.exists():
                tmp_out.unlink(missing_ok=True)
            err_details = ("\n" + "\n".join(recent_lines[-5:])) if recent_lines else ""
            raise RuntimeError(f"FFmpeg ghép video thất bại (mã lỗi {rc}){err_details}")

        if out_path.exists():
            out_path.unlink()
        shutil.move(str(tmp_out), str(out_path))

        if progress_callback:
            progress_callback(100.0, "Hoan thanh ghep video")

        log.info("Ghep video thanh cong: %s (%d bytes)", out_path.name, out_path.stat().st_size)
        return out_path
    finally:
        if fc_file.exists():
            fc_file.unlink(missing_ok=True)
