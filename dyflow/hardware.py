"""Hardware auto-detection for safe adaptive acceleration.

1. Memory check for Demucs parallel vs sequential execution:
   Ensures lower-end machines (e.g. 8GB RAM) never hit OOM, while
   high-spec machines (>=16GB RAM) enjoy background parallel separation.

2. GPU encoder auto-probe with multi-tier fallback:
   Tests for hardware H.264 encoders (NVIDIA NVENC, AMD AMF, Intel QSV,
   Apple VideoToolbox) via a 0.1s probe and falls back safely to CPU libx264.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Tuple

from .utils import log

_ENCODER_CACHE: Tuple[str, list[str]] | None = None
_ENCODER_LOCK = threading.Lock()


def get_system_ram_gb() -> tuple[float, float]:
    """Returns (total_ram_gb, avail_ram_gb)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total = float(stat.ullTotalPhys) / (1024.0 ** 3)
                avail = float(stat.ullAvailPhys) / (1024.0 ** 3)
                return round(total, 2), round(avail, 2)
        except Exception:
            pass

    try:
        import psutil
        vm = psutil.virtual_memory()
        return round(float(vm.total) / (1024.0 ** 3), 2), round(float(vm.available) / (1024.0 ** 3), 2)
    except Exception:
        pass

    # Safe fallback if detection fails: assume modest 8GB total / 4GB avail
    return 8.0, 4.0


def should_run_demucs_parallel() -> tuple[bool, str]:
    """Check if the system has sufficient resources to run Demucs concurrently.

    Safe criteria:
    - Total RAM >= 15.0 GB (nominal 16GB)
    - Available RAM >= 4.5 GB
    - CPU cores >= 6 logical processors
    """
    total_gb, avail_gb = get_system_ram_gb()
    cores = os.cpu_count() or 4

    if total_gb >= 15.0 and avail_gb >= 4.5 and cores >= 6:
        reason = f"RAM {total_gb:.1f}GB (avail {avail_gb:.1f}GB), CPU {cores} cores -> Bat chay nen song song"
        return True, reason

    reason = f"RAM {total_gb:.1f}GB (avail {avail_gb:.1f}GB), CPU {cores} cores -> Chay tuan tu an toan (tranh do may)"
    return False, reason


def probe_best_h264_encoder() -> tuple[str, list[str]]:
    """Auto-detect the fastest available H.264 video encoder.

    Tries hardware encoders with a 1-frame dummy encode:
    1. NVIDIA NVENC (h264_nvenc)
    2. AMD AMF (h264_amf)
    3. Intel QuickSync (h264_qsv)
    4. Apple VideoToolbox (h264_videotoolbox)
    5. Fallback: CPU (libx264)
    Result is cached for subsequent calls.
    """
    global _ENCODER_CACHE
    with _ENCODER_LOCK:
        if _ENCODER_CACHE is not None:
            return _ENCODER_CACHE

        candidates: list[tuple[str, list[str]]] = []
        if sys.platform == "win32":
            candidates = [
                ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20", "-b:v", "0"]),
                ("h264_amf", ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "20", "-qp_p", "20"]),
                ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "fast", "-global_quality", "20"]),
            ]
        elif sys.platform == "darwin":
            candidates = [
                ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-b:v", "4000k"]),
            ]
        else:
            candidates = [
                ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20", "-b:v", "0"]),
                ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "fast", "-global_quality", "20"]),
            ]

        for enc_name, enc_args in candidates:
            probe_cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=128x128:d=0.04",
                *enc_args,
                "-f", "null", "-",
            ]
            try:
                res = subprocess.run(
                    probe_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                )
                if res.returncode == 0:
                    log.info("Hardware video encoder detected and verified: %s", enc_name)
                    _ENCODER_CACHE = (enc_name, enc_args)
                    return _ENCODER_CACHE
            except Exception:
                continue

        log.info("No hardware video encoder available -> using standard CPU libx264")
        _ENCODER_CACHE = ("libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"])
        return _ENCODER_CACHE


def get_cpu_fallback_encoder_args(preset: str = "medium", crf: int = 20) -> list[str]:
    """Standard CPU libx264 encoding arguments."""
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
