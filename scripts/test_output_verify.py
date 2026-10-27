"""Soak / unit: verify_output_mp4 + thu tu discard khong nuot MP4.

Chay: .venv\\Scripts\\python.exe scripts\\test_output_verify.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.utils import MIN_OUTPUT_MP4_BYTES, verify_output_mp4  # noqa: E402
from dyflow.web import storage  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_verify_missing() -> None:
    try:
        verify_output_mp4(Path("Z:/no/such/file.mp4"), label="t")
        raise AssertionError("expected raise")
    except RuntimeError as e:
        _assert("khong co file" in str(e).lower() or "khong co file" in str(e), str(e))


def test_verify_empty(tmp: Path) -> None:
    p = tmp / "empty.mp4"
    p.write_bytes(b"")
    try:
        verify_output_mp4(p, label="t")
        raise AssertionError("expected raise")
    except RuntimeError as e:
        _assert("qua nho" in str(e), str(e))


def test_verify_tiny(tmp: Path) -> None:
    p = tmp / "tiny.mp4"
    p.write_bytes(b"x" * 100)
    try:
        verify_output_mp4(p, min_bytes=MIN_OUTPUT_MP4_BYTES, label="t")
        raise AssertionError("expected raise")
    except RuntimeError as e:
        _assert("qua nho" in str(e), str(e))


def test_verify_ok(tmp: Path) -> None:
    p = tmp / "ok.mp4"
    p.write_bytes(b"x" * (MIN_OUTPUT_MP4_BYTES + 50))
    got = verify_output_mp4(p, label="t")
    _assert(got.is_file(), str(got))


def test_discard_does_not_delete_output(tmp: Path) -> None:
    """Giong worker: output nam NGOAI work/<id>, discard chi xoa work."""
    work = tmp / "work"
    out_dir = tmp / "output"
    jid = "web_" + ("a" * 32)
    work_job = work / jid
    work_job.mkdir(parents=True)
    (work_job / "raw.bin").write_bytes(b"temp" * 1000)
    mp4 = out_dir / f"{jid}.mp4"
    out_dir.mkdir(parents=True)
    mp4.write_bytes(b"x" * (MIN_OUTPUT_MP4_BYTES + 100))

    storage.discard_job_work(jid, work_root=work)
    _assert(not work_job.exists(), "work job must be gone")
    _assert(mp4.is_file() and mp4.stat().st_size > MIN_OUTPUT_MP4_BYTES, "output must remain")
    verify_output_mp4(mp4, label="after discard")


def test_discard_would_fail_if_output_inside_work(tmp: Path) -> None:
    """Neu (sai) MP4 nam trong work/<id>, discard xoa mat — verify sau phai fail."""
    work = tmp / "work"
    jid = "web_" + ("b" * 32)
    work_job = work / jid
    work_job.mkdir(parents=True)
    mp4 = work_job / f"{jid}.mp4"
    mp4.write_bytes(b"x" * (MIN_OUTPUT_MP4_BYTES + 100))
    storage.discard_job_work(jid, work_root=work)
    _assert(not mp4.exists(), "mp4 inside work deleted")
    try:
        verify_output_mp4(mp4, label="bad layout")
        raise AssertionError("expected raise after discard ate output")
    except RuntimeError:
        pass


def soak(n: int = 40) -> None:
    for i in range(n):
        with tempfile.TemporaryDirectory(prefix=f"vc_out_{i}_") as td:
            tmp = Path(td)
            test_verify_ok(tmp)
            test_verify_empty(tmp)
            test_verify_tiny(tmp)
            test_discard_does_not_delete_output(tmp)
            test_discard_would_fail_if_output_inside_work(tmp)
        if (i + 1) % 10 == 0:
            print(f"soak {i + 1}/{n} ok")


def main() -> int:
    test_verify_missing()
    with tempfile.TemporaryDirectory(prefix="vc_out_unit_") as td:
        tmp = Path(td)
        test_verify_empty(tmp)
        test_verify_tiny(tmp)
        test_verify_ok(tmp)
        test_discard_does_not_delete_output(tmp)
        test_discard_would_fail_if_output_inside_work(tmp)
    print("unit OK")
    soak(40)
    print("ALL OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        # avoid leftover
        pass
