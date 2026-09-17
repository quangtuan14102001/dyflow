#!/usr/bin/env python3
"""ffmpeg location for Windows exe: skip Chocolatey shims, pass --ffmpeg-location."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _assert(cond, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def _write_bin(path: Path, size: int) -> None:
    path.write_bytes(b"\x00" * size)


def test_skips_chocolatey_shim() -> None:
    from dyflow.utils import media_bin_dir

    with tempfile.TemporaryDirectory() as raw:
        d = Path(raw)
        _write_bin(d / "ffmpeg", 8000)
        _write_bin(d / "ffmpeg.exe", 8000)
        with mock.patch.dict(os.environ, {"DYFLOW_FFMPEG_DIR": str(d)}):
            got = media_bin_dir()
        _assert(got is None or got.resolve() != d.resolve(), f"accepted shim dir {got}")


def test_accepts_real_ffmpeg() -> None:
    from dyflow.utils import ffmpeg_path, media_bin_dir, ytdlp_ffmpeg_args

    with tempfile.TemporaryDirectory() as raw:
        d = Path(raw)
        name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        _write_bin(d / name, 200_000)
        with mock.patch.dict(os.environ, {"DYFLOW_FFMPEG_DIR": str(d)}):
            got = media_bin_dir()
            _assert(got is not None and got.resolve() == d.resolve(), got)
            _assert(ffmpeg_path().endswith(name), ffmpeg_path())
            args = ytdlp_ffmpeg_args()
            _assert(args[:2] == ["--ffmpeg-location", str(d)] or args[1] == str(d), args)


def test_ytdlp_cmd_has_ffmpeg_and_sections() -> None:
    from dyflow.downloader import _build_ytdlp_cmd
    from dyflow.utils import ytdlp_ffmpeg_args

    with tempfile.TemporaryDirectory() as raw:
        d = Path(raw)
        name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        _write_bin(d / name, 200_000)
        with mock.patch.dict(os.environ, {"DYFLOW_FFMPEG_DIR": str(d)}):
            cmd = _build_ytdlp_cmd(
                "https://www.douyin.com/video/7679803336194854180",
                "/tmp/out.%(ext)s",
                {"max_duration_sec": 120, "retries": 3},
            )
    joined = " ".join(cmd)
    _assert("--ffmpeg-location" in cmd, cmd)
    _assert("--download-sections" in joined, cmd)
    _assert(ytdlp_ffmpeg_args()[0] == "--ffmpeg-location", ytdlp_ffmpeg_args())


def test_no_sections_without_ffmpeg() -> None:
    from dyflow import utils as u
    from dyflow.downloader import _build_ytdlp_cmd

    with mock.patch.object(u, "media_bin_dir", return_value=None):
        with mock.patch.object(u, "ensure_media_bins", return_value=None):
            cmd = _build_ytdlp_cmd(
                "https://www.douyin.com/video/1",
                "/tmp/out.%(ext)s",
                {"max_duration_sec": 120},
            )
    _assert("--download-sections" not in cmd, cmd)
    _assert("--ffmpeg-location" not in cmd, cmd)


def test_frozen_ytdlp_tool_ok() -> None:
    from dyflow.utils import check_tool

    with mock.patch.object(sys, "frozen", True, create=True):
        _assert(check_tool("yt-dlp") is True, "frozen must see yt-dlp")


def test_resolve_rewrites_ffmpeg() -> None:
    from dyflow.utils import _resolve_media_cmd, ffmpeg_path

    out = _resolve_media_cmd(["ffmpeg", "-y", "-i", "a.mp4"])
    _assert(out[0] == ffmpeg_path(), out)
    _assert(out[1:] == ["-y", "-i", "a.mp4"], out)


def test_ffmpeg_version_runs() -> None:
    import shutil
    import subprocess

    from dyflow.utils import ffmpeg_path, ffprobe_path

    ff = ffmpeg_path()
    if ff == "ffmpeg" and not shutil.which("ffmpeg"):
        print("SKIP live ffmpeg")
        return
    proc = subprocess.run([ff, "-version"], capture_output=True, text=True)
    _assert(proc.returncode == 0, proc.stderr)
    _assert("ffmpeg version" in (proc.stdout + proc.stderr).lower(), proc.stdout[:80])
    probe = subprocess.run([ffprobe_path(), "-version"], capture_output=True, text=True)
    _assert(probe.returncode == 0, probe.stderr)


def main() -> int:
    test_skips_chocolatey_shim()
    print("OK skip shim")
    test_accepts_real_ffmpeg()
    print("OK real ffmpeg dir")
    test_ytdlp_cmd_has_ffmpeg_and_sections()
    print("OK ytdlp cmd")
    test_no_sections_without_ffmpeg()
    print("OK no sections without ffmpeg")
    test_frozen_ytdlp_tool_ok()
    print("OK frozen yt-dlp")
    test_resolve_rewrites_ffmpeg()
    print("OK rewrite argv")
    test_ffmpeg_version_runs()
    print("OK ffmpeg -version")
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
