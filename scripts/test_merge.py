#!/usr/bin/env python3
"""Test tinh nang ghep video (Video Merger) ca backend va Web API.

  python scripts/test_merge.py
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.video_merger import merge_videos  # noqa: E402
from dyflow.utils import probe_duration, probe_has_audio, probe_video_size  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_clip(path: Path, seconds: float = 1.0, color: str = "black", has_audio: bool = True, size: str = "320x240") -> None:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={seconds}",
    ]
    if has_audio:
        cmd.extend(["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"])
        cmd.extend(["-c:a", "aac"])
    else:
        cmd.append("-an")
    cmd.extend([
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-shortest", str(path),
    ])
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_merge_module(tmp: Path) -> None:
    print("--- Test Video Merger Module ---")
    v1 = tmp / "v1.mp4"
    v2 = tmp / "v2.mp4"
    v3 = tmp / "v3.mp4"
    out_contain = tmp / "out_contain.mp4"
    out_cover = tmp / "out_cover.mp4"
    out_first = tmp / "out_first.mp4"

    # Clip 1: 320x240 (4:3), co audio, 1s
    _make_clip(v1, seconds=1.0, color="red", has_audio=True, size="320x240")
    # Clip 2: 640x360 (16:9), KHONG audio, 1.5s
    _make_clip(v2, seconds=1.5, color="blue", has_audio=False, size="640x360")
    # Clip 3: 400x400 (1:1), co audio, 1.2s
    _make_clip(v3, seconds=1.2, color="green", has_audio=True, size="400x400")

    # 1. Test merge 9:16 contain
    res1 = merge_videos([v1, v2, v3], out_contain, aspect_ratio="9:16", fit="contain")
    if not res1.is_file() or res1.stat().st_size < 1000:
        _fail("Merge 9:16 contain that bai")
    w1, h1 = probe_video_size(res1)
    if (w1, h1) != (1080, 1920):
        _fail(f"Kich thuoc output khong dung (1080, 1920): {(w1, h1)}")
    dur1 = probe_duration(res1)
    if abs(dur1 - 3.7) > 0.3:
        _fail(f"Tong thoi luong khong dung (~3.7s): {dur1}")
    if not probe_has_audio(res1):
        _fail("Output video thieu audio sau khi ghep")
    print(f"OK merge 9:16 contain: {res1.name} ({w1}x{h1}, {dur1:.2f}s, {res1.stat().st_size} bytes)")

    # 2. Test merge 16:9 cover
    res2 = merge_videos([v1, v2], out_cover, aspect_ratio="16:9", fit="cover")
    if not res2.is_file() or res2.stat().st_size < 1000:
        _fail("Merge 16:9 cover that bai")
    w2, h2 = probe_video_size(res2)
    if (w2, h2) != (1920, 1080):
        _fail(f"Kich thuoc output khong dung (1920, 1080): {(w2, h2)}")
    print(f"OK merge 16:9 cover: {res2.name} ({w2}x{h2})")

    # 3. Test merge aspect_ratio="first"
    res3 = merge_videos([v1, v2], out_first, aspect_ratio="first", fit="contain")
    w3, h3 = probe_video_size(res3)
    if (w3, h3) != (320, 240):
        _fail(f"Kich thuoc first khong dung (320, 240): {(w3, h3)}")
    print(f"OK merge first aspect: {res3.name} ({w3}x{h3})")


def test_merge_api(tmp: Path) -> None:
    print("--- Test Merge Web API ---")
    from starlette.testclient import TestClient
    from dyflow.web.app import app
    from dyflow.web import auth

    client = TestClient(app)

    # 1. GET /merge (unauthenticated -> redirect /login)
    r = client.get("/merge", follow_redirects=False)
    if r.status_code not in (200, 302):
        _fail(f"GET /merge return {r.status_code}")
    print(f"OK GET /merge route: {r.status_code}")

    # 2. Authenticated test
    v1 = tmp / "api_v1.mp4"
    v2 = tmp / "api_v2.mp4"
    _make_clip(v1, seconds=1.0, color="yellow", size="320x240")
    _make_clip(v2, seconds=1.0, color="purple", size="320x240")

    # Mock user login
    import secrets
    from dyflow.web import models
    email = f"merge_test_{secrets.token_hex(4)}@example.com"
    user = models.create_user(email, auth.hash_password("password123"))
    token = models.create_session(user["id"])
    cookies = {auth.COOKIE_NAME: token}

    r_auth = client.get("/merge", cookies=cookies)
    if r_auth.status_code != 200:
        _fail(f"GET /merge authenticated failed: {r_auth.status_code}")
    if "Ghép nhiều video" not in r_auth.text and "Merge Videos" not in r_auth.text:
        _fail("GET /merge response thieu tieu de trang")
    print("OK GET /merge trang web load thanh cong")

    # 3. POST /api/merge
    with open(v1, "rb") as f1, open(v2, "rb") as f2:
        files = [
            ("videos", ("clip1.mp4", f1, "video/mp4")),
            ("videos", ("clip2.mp4", f2, "video/mp4")),
        ]
        data = {
            "aspect_ratio": "first",
            "fit": "contain",
            "fast_copy": "0",
        }
        res_post = client.post("/api/merge", data=data, files=files, cookies=cookies)
    if res_post.status_code != 200:
        _fail(f"POST /api/merge that bai: {res_post.status_code} - {res_post.text}")
    res_json = res_post.json()
    if not res_json.get("ok") or not res_json.get("download_url"):
        _fail(f"POST /api/merge response json khong dung: {res_json}")
    print(f"OK POST /api/merge API: {res_json['filename']} ({res_json['size']} bytes)")

    # 4. GET download url
    dl_url = res_json["download_url"]
    r_dl = client.get(dl_url, cookies=cookies)
    if r_dl.status_code != 200 or len(r_dl.content) < 1000:
        _fail(f"Download video merged that bai: {r_dl.status_code}")
    print(f"OK GET download merged video: {len(r_dl.content)} bytes")


def main() -> None:
    print("Chay bo test ghep video...")
    if not _have_ffmpeg():
        print("SKIP: Thieu ffmpeg/ffprobe tren may")
        return

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_merge_module(tmp)
        test_merge_api(tmp)

    print("\n>>> ALL TESTS PASSED: Tinh nang ghep video hoat dong hoan hao! <<<")


if __name__ == "__main__":
    main()
