#!/usr/bin/env python3
"""Windows EXE: POST /api/jobs must create queued jobs, never opaque 500."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["DYFLOW_DESKTOP"] = "1"
os.environ["DYFLOW_WEB_NO_WORKER"] = "1"
os.environ["DYFLOW_LICENSE_SKIP"] = "1"
os.environ["OPENAI_API_KEY"] = "sk-test-job-create"
os.environ["DYFLOW_IGNORE_DOTENV"] = "1"


def _assert(cond, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def _client(tmp: Path):
    from dyflow.web import models, worker
    from dyflow.web.app import app

    models.DB_PATH = tmp / "dyflow.db"
    models.init_db()
    models.ensure_desktop_owner()
    worker.ensure_started = lambda *a, **k: None  # type: ignore[method-assign]
    worker.start_background = lambda *a, **k: None  # type: ignore[assignment]

    from fastapi.testclient import TestClient

    return TestClient(app)


def _quoted_items(n: int = 17) -> list[dict]:
    return [
        {
            "url": f"https://www.douyin.com/video/768123545384339{i:04d}",
            "credits": 1,
            "unknown": True,
        }
        for i in range(n)
    ]


def test_quoted_json_and_urls_parse() -> None:
    os.environ["DYFLOW_DESKTOP"] = "1"
    from dyflow.web.app import _items_from_quoted_json

    items = _quoted_items(2)
    got = _items_from_quoted_json(json.dumps(items), limit=200)
    _assert(len(got) == 2, got)

    lines = "\n".join(x["url"] for x in items)
    got2 = _items_from_quoted_json(lines, limit=200)
    _assert(len(got2) == 2, got2)

    wrapped = json.dumps({"items": items})
    got3 = _items_from_quoted_json(wrapped, limit=200)
    _assert(len(got3) == 2, got3)

    urls_only = json.dumps([x["url"] for x in items])
    got4 = _items_from_quoted_json(urls_only, limit=200)
    _assert(len(got4) == 2, got4)


def test_create_jobs_from_quoted_json() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-job-create"}):
            client = _client(tmp)
            items = _quoted_items(17)
            res = client.post(
                "/api/jobs",
                data={
                    "url": (
                        "https://www.douyin.com/user/"
                        "MS4wLjABAAAAtestuser?from_tab_name=main"
                        "&vid=7681235453843390000"
                    ),
                    "voice": "male_child",
                    "lang": "vi",
                    "tts": "edge",
                    "channel_mode": "full",
                    "channel_count": "10",
                    "quoted_json": json.dumps(items),
                    "quoted_urls": "\n".join(x["url"] for x in items),
                },
            )
        _assert(res.status_code == 202, f"{res.status_code} {res.text}")
        body = res.json()
        _assert(body.get("count") == 17, body)
        _assert(len(body.get("ids") or []) == 17, body)

        listed = client.get("/api/jobs")
        _assert(listed.status_code == 200, listed.text)
        jobs = listed.json().get("jobs") or listed.json()
        if isinstance(jobs, dict):
            jobs = jobs.get("jobs") or jobs.get("items") or []
        queued = [j for j in jobs if j.get("status") in ("queued", "running")]
        _assert(len(queued) >= 1, f"jobs not visible: {listed.text[:400]}")


def test_create_jobs_quoted_urls_only() -> None:
    """WebView2 co the mat quoted_json — van tao job tu danh sach URL."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        client = _client(tmp)
        items = _quoted_items(3)
        res = client.post(
            "/api/jobs",
            data={
                "url": "https://www.douyin.com/user/MS4wLjABAAAAtestuser",
                "voice": "male_adult",
                "lang": "vi",
                "tts": "edge",
                "channel_mode": "full",
                "quoted_urls": "\n".join(x["url"] for x in items),
            },
        )
        _assert(res.status_code == 202, f"{res.status_code} {res.text}")
        _assert(res.json().get("count") == 3, res.json())


def test_channel_without_quote_does_not_500() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        client = _client(tmp)
        res = client.post(
            "/api/jobs",
            data={
                "url": "https://www.douyin.com/user/MS4wLjABAAAAtestuser",
                "voice": "male_adult",
                "lang": "vi",
                "tts": "edge",
                "channel_mode": "full",
            },
        )
        _assert(res.status_code == 400, f"{res.status_code} {res.text}")
        detail = str(res.json().get("detail") or "")
        _assert("Preview" in detail or "preview" in detail.lower() or "Start" in detail, detail)
        _assert(res.headers.get("content-type", "").startswith("application/json"), res.headers)


def test_missing_videos_file_field_ok() -> None:
    """Khong gui field videos (FormData thu cong) — khong 422."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        client = _client(tmp)
        items = _quoted_items(1)
        res = client.post(
            "/api/jobs",
            data={
                "url": items[0]["url"],
                "voice": "male_adult",
                "lang": "vi",
                "tts": "edge",
                "quoted_json": json.dumps(items),
            },
        )
        _assert(res.status_code == 202, f"{res.status_code} {res.text}")


def test_preflight_nameerror_gone() -> None:
    from dyflow.preflight import MISSING_OPENAI, assert_job_ready

    _assert("OPENAI_API_KEY" in MISSING_OPENAI, MISSING_OPENAI)
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["DYFLOW_IGNORE_DOTENV"] = "1"
    os.environ["DYFLOW_ENV"] = "production"
    try:
        assert_job_ready({"voiceover": {"tts_engine": "edge"}}, {"id": "t"})
        raise SystemExit("FAIL: phai raise khi thieu key")
    except RuntimeError as e:
        _assert("OPENAI_API_KEY" in str(e), str(e))
    finally:
        os.environ["OPENAI_API_KEY"] = "sk-test-job-create"


def test_missing_key_returns_json_400() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with mock.patch("dyflow.preflight.openai_key", return_value=""):
            with mock.patch("dyflow.preflight.hydrate_openai_key", return_value=""):
                with mock.patch.dict(
                    os.environ,
                    {"OPENAI_API_KEY": "", "DYFLOW_ENV": "production", "DYFLOW_IGNORE_DOTENV": "1"},
                ):
                    client = _client(tmp)
                    items = _quoted_items(1)
                    res = client.post(
                        "/api/jobs",
                        data={
                            "url": items[0]["url"],
                            "voice": "male_adult",
                            "lang": "vi",
                            "tts": "edge",
                            "quoted_json": json.dumps(items),
                        },
                    )
        _assert(res.status_code == 400, f"{res.status_code} {res.text}")
        _assert("application/json" in res.headers.get("content-type", ""), res.headers)
        _assert("OPENAI_API_KEY" in str(res.json().get("detail")), res.text)


def test_unhandled_error_is_json() -> None:
    from fastapi.testclient import TestClient
    from dyflow.web.app import app

    @app.get("/api/_boom_test")
    def _boom():
        raise RuntimeError("simulated crash")

    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/api/_boom_test")
    _assert(res.status_code == 500, res.text)
    _assert("application/json" in res.headers.get("content-type", ""), res.headers)
    _assert("simulated crash" in str(res.json().get("detail")), res.text)


def main() -> int:
    test_quoted_json_and_urls_parse()
    print("OK quoted parse")
    test_preflight_nameerror_gone()
    print("OK preflight constant")
    test_create_jobs_from_quoted_json()
    print("OK create 17 jobs")
    test_create_jobs_quoted_urls_only()
    print("OK quoted_urls only")
    test_channel_without_quote_does_not_500()
    print("OK channel without quote")
    test_missing_videos_file_field_ok()
    print("OK missing videos field")
    test_missing_key_returns_json_400()
    print("OK missing key json")
    test_unhandled_error_is_json()
    print("OK unhandled json")
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
