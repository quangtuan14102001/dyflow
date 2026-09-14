#!/usr/bin/env python3
"""Mo phong thieu OPENAI_API_KEY: job phai fail o pre-flight trong vai giay.

  python scripts/test_preflight.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Mo phong thieu key: khong doc .env that cua may (anh van co key).
os.environ["OPENAI_API_KEY"] = ""
os.environ["DYFLOW_IGNORE_DOTENV"] = "1"
os.environ.pop("ELEVENLABS_API_KEY", None)
os.environ.pop("ELEVEN_API_KEY", None)
os.environ["DYFLOW_ENV"] = "production"


def _ok_row(i: int, **kw) -> dict:
    row = {
        "i": i,
        "start": float(i),
        "slot": 2.0,
        "play": 2.0,
        "speed": 1.0,
        "drift_pct": 0.0,
        "flags": [],
        "text": "ok",
    }
    row.update(kw)
    return row


def main() -> int:
    from dyflow.preflight import MISSING_OPENAI, allow_degraded, assert_job_ready, hydrate_openai_key
    from dyflow.dub_audit import audit_qa_rows
    from dyflow.main import process_job
    from dyflow.utils import load_config
    import dyflow.downloader as downloader

    # --- 0. Key that trong .env van dung duoc du OPENAI_API_KEY="" ---
    os.environ["DYFLOW_IGNORE_DOTENV"] = "0"
    fake = ROOT / "work" / "_preflight_fake.env"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("OPENAI_API_KEY=sk-user-key-from-env-file\n", encoding="utf-8")
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["DYFLOW_ENV_FILE"] = str(fake)
    got = hydrate_openai_key()
    if got != "sk-user-key-from-env-file":
        raise SystemExit(f"FAIL: phai dung key trong .env, got {got!r}")
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["OPENAI_API_KEY"] = ""
    assert_job_ready({"voiceover": {}}, {"id": "sim_user_key"})
    print("OK dung key trong .env du bien moi truong rong")
    os.environ.pop("DYFLOW_ENV_FILE", None)
    fake.unlink(missing_ok=True)
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["DYFLOW_IGNORE_DOTENV"] = "1"

    # --- 1. Pre-flight thieu key: fail ngay ---
    t0 = time.time()
    try:
        assert_job_ready({"voiceover": {"tts_engine": "edge"}}, {"id": "sim_no_key"})
        raise SystemExit("FAIL: assert_job_ready phai raise khi thieu OPENAI_API_KEY")
    except RuntimeError as e:
        dt = time.time() - t0
        if "OPENAI_API_KEY" not in str(e):
            raise SystemExit(f"FAIL: thong bao sai: {e}") from e
        if dt > 3:
            raise SystemExit(f"FAIL: pre-flight {dt:.2f}s — phai < 3s") from None
        print(f"OK preflight raise {dt:.3f}s: {e}")

    # --- 2. DYFLOW_ENV=dev: cho phep draft ---
    os.environ["DYFLOW_ENV"] = "dev"
    if not allow_degraded():
        raise SystemExit("FAIL: DYFLOW_ENV=dev phai allow_degraded")
    assert_job_ready({"voiceover": {}}, {"id": "sim_dev"})
    print("OK dev cho phep thieu key")
    os.environ["DYFLOW_ENV"] = "production"

    # --- 3. process_job khong duoc toi buoc tai video ---
    called = {"download": False}

    def _no_download(*_a, **_k):
        called["download"] = True
        raise AssertionError("download_one KHONG duoc goi khi thieu key")

    downloader.download_one = _no_download  # type: ignore[assignment]
    cfg = load_config(str(ROOT / "config.yaml"))
    t1 = time.time()
    try:
        process_job(
            {
                "id": "preflight_sim",
                "source": "https://www.douyin.com/video/7670466706573184298",
            },
            cfg,
        )
        raise SystemExit("FAIL: process_job phai fail o pre-flight")
    except RuntimeError as e:
        dt = time.time() - t1
        if called["download"]:
            raise SystemExit("FAIL: da goi download — pre-flight khong chan kip") from None
        if "OPENAI_API_KEY" not in str(e) and MISSING_OPENAI[:20] not in str(e):
            raise SystemExit(f"FAIL: process_job thong bao sai: {e}") from e
        if dt > 5:
            raise SystemExit(f"FAIL: process_job {dt:.2f}s — phai fail trong vai giay") from None
        print(f"OK process_job fail {dt:.3f}s, chua tai video: {e}")

    # --- 4. QA qua_dai / drift ---
    ten = [_ok_row(i) for i in range(10)]
    code, summary, _ = audit_qa_rows(ten, tts_engine="edge")
    if code != 0:
        raise SystemExit(f"FAIL: 0 qua_dai phai pass, got {code} {summary}")

    bad_ratio = [_ok_row(i) for i in range(10)]
    for i in range(3):
        bad_ratio[i]["flags"] = ["qua_dai"]
        bad_ratio[i]["play"] = 2.4
        bad_ratio[i]["drift_pct"] = 20.0
    code, summary, _ = audit_qa_rows(bad_ratio, tts_engine="edge")
    if code != 1 or "qua_dai" not in summary:
        raise SystemExit(f"FAIL: 30% qua_dai phai fail, got {code} {summary}")
    print(f"OK QA qua_dai 30%: {summary}")

    one_drift = [_ok_row(i) for i in range(10)]
    one_drift[0]["drift_pct"] = 51.0
    one_drift[0]["play"] = 3.02
    one_drift[0]["flags"] = ["qua_dai"]
    code, summary, _ = audit_qa_rows(one_drift, tts_engine="edge")
    if code != 1 or "drift" not in summary:
        raise SystemExit(f"FAIL: drift 51% phai fail, got {code} {summary}")
    print(f"OK QA drift 51%: {summary}")

    pause = [_ok_row(i) for i in range(10)]
    for r in pause:
        r["play"] = 1.48  # 74% tong cov — clip goc co khoang lang
    code, summary, _ = audit_qa_rows(pause, tts_engine="edge")
    if code != 0:
        raise SystemExit(f"FAIL: 74% cov khong duoc fail job, got {code} {summary}")
    if "tong cov" in summary:
        raise SystemExit(f"FAIL: khong duoc FAIL tong cov, got {summary}")
    print(f"OK QA 74% khoang lang van giao clip: {summary}")

    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
