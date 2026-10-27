"""Test deliver Save folder + purge grace + ASCII names.

Chay: .venv\\Scripts\\python.exe scripts\\test_deliver_and_purge.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.utils import MIN_OUTPUT_MP4_BYTES, verify_output_mp4  # noqa: E402
from dyflow.web.export_delivery import (  # noqa: E402
    KEEP_RECENT_OUTPUTS,
    OUTPUT_GRACE_SEC,
    copy_to_save_folder,
    safe_export_name,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_ascii_name() -> None:
    name = safe_export_name(
        "Nhiều năm sau, tôi nhận ra rằng tôi không mặc váy vào ngày hôm đó.",
        "web_abc123def4567890",
    )
    _assert(name.endswith(".mp4"), name)
    _assert("#" not in name and "|" not in name and "?" not in name, name)
    _assert(all(ord(c) < 128 for c in name), f"non-ascii: {name}")
    print("ascii name:", name)


def test_copy_to_save(tmp: Path) -> None:
    src = tmp / "src.mp4"
    src.write_bytes(b"x" * (MIN_OUTPUT_MP4_BYTES + 200))
    dest_dir = tmp / "save_here"
    got = copy_to_save_folder(
        src,
        job_id="web_" + ("c" * 32),
        title="Nhiều năm sau, tôi không mặc váy",
        dest_dir=dest_dir,
    )
    _assert(got.is_file(), str(got))
    _assert(got.parent == dest_dir.resolve(), str(got))
    verify_output_mp4(got, label="copied")
    _assert(all(ord(c) < 128 for c in got.name), got.name)


def test_grace_constants() -> None:
    _assert(OUTPUT_GRACE_SEC >= 24 * 3600, str(OUTPUT_GRACE_SEC))
    _assert(KEEP_RECENT_OUTPUTS >= 3, str(KEEP_RECENT_OUTPUTS))


def main() -> int:
    test_ascii_name()
    test_grace_constants()
    with tempfile.TemporaryDirectory(prefix="vc_deliver_") as td:
        test_copy_to_save(Path(td))
    for i in range(20):
        with tempfile.TemporaryDirectory(prefix=f"vc_d{i}_") as td:
            test_copy_to_save(Path(td))
    print("ALL OK", time.strftime("%H:%M:%S"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
