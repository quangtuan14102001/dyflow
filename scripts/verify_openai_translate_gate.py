#!/usr/bin/env python3
"""Xac nhan 3 hanh vi OpenAI translate (fail-closed + dich that).

1) OpenAI + key rong -> Start bi chan, khong tao job
2) OpenAI + key sai -> dich FAIL ro, khong Google
3) OpenAI + key dung -> Dich budget + cast nhat quan (clip da nhan vat)
"""
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
os.environ["DYFLOW_IGNORE_DOTENV"] = "1"


def _assert(cond, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def _ok(msg: str) -> None:
    print(f"PASS: {msg}")


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


def _job_form(**extra) -> dict:
    data = {
        "url": "",
        "voice": "male_adult",
        "lang": "vi",
        "tts": "edge",
        "preset_name": "classic",
        "video_speed": "1.0",
        "logo_position": "top_right",
        "logo_size_percent": "10",
        "channel_mode": "count",
        "channel_count": "40",
        "translate_engine": "openai",
        "quoted_json": json.dumps(
            [{"url": "https://www.douyin.com/video/7681235453843390001", "credits": 1, "unknown": True}]
        ),
    }
    data.update(extra)
    return data


def test1_empty_key_blocks_start() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        env = {
            "DYFLOW_DESKTOP": "1",
            "DYFLOW_WEB_NO_WORKER": "1",
            "DYFLOW_LICENSE_SKIP": "1",
            "DYFLOW_IGNORE_DOTENV": "1",
            "OPENAI_API_KEY": "",
            "OPENAI_TTS_API_KEY": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            # dam bao openai_key() khong doc .env user
            with mock.patch("dyflow.preflight.hydrate_openai_key", return_value=""):
                with mock.patch("dyflow.user_openai.has_tts_key", return_value=False):
                    with mock.patch("dyflow.user_openai.load_tts_key", return_value=""):
                        with mock.patch("dyflow.preflight.openai_key", return_value=""):
                            client = _client(tmp)
                            before = client.get("/api/jobs").json()
                            n_before = len(before.get("jobs") or before.get("items") or [])
                            # listing shape may vary
                            if isinstance(before, list):
                                n_before = len(before)
                            elif "jobs" in before:
                                n_before = len(before["jobs"])
                            else:
                                n_before = len(before.get("items") or [])

                            res = client.post("/api/jobs", data=_job_form())
                            _assert(res.status_code == 400, f"expected 400 got {res.status_code} {res.text[:300]}")
                            detail = str(res.json().get("detail") or "")
                            _assert(
                                "không hợp lệ" in detail.lower()
                                or "khong hop le" in detail.lower()
                                or "invalid" in detail.lower()
                                or "sk-" in detail.lower()
                                or "key" in detail.lower(),
                                f"unexpected detail: {detail}",
                            )
                            after = client.get("/api/jobs").json()
                            if isinstance(after, list):
                                n_after = len(after)
                            elif "jobs" in after:
                                n_after = len(after["jobs"])
                            else:
                                n_after = len(after.get("items") or [])
                            _assert(n_after == n_before, f"job created? before={n_before} after={n_after}")
                            _ok(f"1) Start bi chan (HTTP 400), khong tao job. detail={detail[:80]}")


def test2_bad_key_fails_no_google() -> None:
    """Key format OK nhung OpenAI tu choi -> require_llm FAIL, khong Google."""
    from dyflow.translate_llm import (
        _openai_fail_message,
        _translate_budgeted_fallback,
        translate_segments_budgeted,
    )

    segs = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "你好",
            "source_text": "你好",
            "speaker_id": "boy",
        },
        {
            "start": 2.0,
            "end": 4.5,
            "text": "妈妈回来了",
            "source_text": "妈妈回来了",
            "speaker_id": "dad",
        },
    ]
    bad = "sk-thisisafakekeythatwillfailauth9999999999"
    called = {"google": 0}

    def _fake_google(*a, **k):
        called["google"] += 1
        raise AssertionError("MUST NOT fall back to Google when require_llm=True")

    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": bad, "OPENAI_TTS_API_KEY": bad}, clear=False):
        with mock.patch("dyflow.translate_llm._translate_budgeted_fallback", side_effect=_fake_google):
            with mock.patch("dyflow.preflight.openai_key", return_value=bad):
                try:
                    translate_segments_budgeted(
                        segs,
                        video_dur=5.0,
                        target="vi",
                        chars_per_sec=14.5,
                        require_llm=True,
                    )
                    raise SystemExit("FAIL: expected RuntimeError for bad key")
                except RuntimeError as e:
                    msg = str(e)
                    _assert(
                        "không hợp lệ" in msg.lower()
                        or "khong hop le" in msg.lower()
                        or "invalid" in msg.lower()
                        or "hop le" in msg.replace("ố", "o").lower(),
                        f"wrong fail message: {msg}",
                    )
                    _assert(called["google"] == 0, "Google fallback was called")
                    # also unit-check mapper
                    mapped = _openai_fail_message(Exception("Error code: 401 - Incorrect API key provided"))
                    _assert("hop le" in mapped.replace("ố", "o").lower(), mapped)
                    _ok(f"2) Key sai -> FAIL ro, khong Google. msg={msg}")

    # random short chars: save_tts_key / Start format gate
    from dyflow.user_openai import save_tts_key

    try:
        save_tts_key("abc123")
        raise SystemExit("FAIL: short random key should be rejected at save")
    except ValueError:
        _ok("2b) Key random ngan bi tu choi luc luu (khong tao job)")


def test3_good_key_multicast() -> None:
    from dyflow.preflight import hydrate_openai_key, openai_key
    from dyflow.translate_llm import lock_cast_relations, translate_segments_budgeted

    # Cho phep doc .env that (key user)
    os.environ.pop("DYFLOW_IGNORE_DOTENV", None)
    # clear mocks leftovers
    for k in ("OPENAI_API_KEY", "OPENAI_TTS_API_KEY"):
        if os.environ.get(k, "").startswith("sk-thisisafake") or os.environ.get(k, "") == "sk-test-job-create":
            os.environ.pop(k, None)

    hydrate_openai_key()
    key = openai_key()
    _assert(bool(key) and key.startswith("sk-") and len(key) > 20, "thieu key OpenAI that de chay test 3")

    src = Path(os.environ["LOCALAPPDATA"]) / "VideosCreator" / "work" / "cli_f8f2_planA_rerun" / "segments_vi.json"
    alt = ROOT / "work" / "review_phim_001" / "segments_vi.json"
    path = src if src.is_file() else alt
    _assert(path.is_file(), f"thieu sample segments: {src} / {alt}")

    all_segs = json.loads(path.read_text(encoding="utf-8"))
    # lay mau da nhan vat, gioi han chi phi API
    want = {"aunt", "boy", "dad", "narrator"}
    picked: list[dict] = []
    seen: set[str] = set()
    for seg in all_segs:
        sid = str(seg.get("speaker_id") or seg.get("voice_id") or "")
        src_zh = str(seg.get("source_text") or "").strip()
        if not sid or not src_zh:
            # doi khi text la VI — van can source_text; fallback text neu con chu Han
            text = str(seg.get("text") or "")
            if any("\u4e00" <= ch <= "\u9fff" for ch in text):
                src_zh = text
            else:
                continue
        row = {
            **seg,
            "source_text": src_zh,
            "text": src_zh,
            "speaker_id": sid,
        }
        if sid in want and sid not in seen:
            picked.append(row)
            seen.add(sid)
        elif len(picked) < 16 and sid in want:
            picked.append(row)
        if len(picked) >= 16 and want.issubset(seen):
            break
    if len(seen) < 2:
        # fallback: lay 16 doan dau co source
        picked = []
        for seg in all_segs[:40]:
            src_zh = str(seg.get("source_text") or seg.get("text") or "").strip()
            if not src_zh:
                continue
            picked.append({**seg, "source_text": src_zh, "text": src_zh})
            if len(picked) >= 12:
                break
    _assert(len(picked) >= 6, f"khong du doan sample ({len(picked)})")

    video_dur = max(float(s.get("end") or 0) for s in picked) + 1.0
    out_dir = ROOT / "work" / "_verify_openai_cast"
    out_dir.mkdir(parents=True, exist_ok=True)

    locked = lock_cast_relations(picked, target="vi", work_dir=out_dir)
    translated = translate_segments_budgeted(
        locked,
        video_dur=video_dur,
        target="vi",
        chars_per_sec=14.5,
        work_dir=out_dir,
        require_llm=True,
    )
    (out_dir / "segments_out.json").write_text(
        json.dumps(translated, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # Ten/xung ho nhat quan theo speaker
    by_sid: dict[str, list[str]] = {}
    pairs: dict[str, set[str]] = {}
    for seg in translated:
        sid = str(seg.get("speaker_id") or "")
        if not sid:
            continue
        by_sid.setdefault(sid, []).append(str(seg.get("text") or ""))
        pairs.setdefault(sid, set()).add(str(seg.get("pronoun_pair_used") or ""))

    _assert(any(by_sid.values()), "khong co text dich")
    inconsistent = {sid: ps for sid, ps in pairs.items() if len([x for x in ps if x]) > 1}
    _assert(not inconsistent, f"xung ho khong nhat quan: {inconsistent}")

    # khong con pinyin thô Xu Ai...
    blob = " ".join(t for lines in by_sid.values() for t in lines)
    _assert("Xu Ai" not in blob and "Qin Chuan" not in blob, f"con pinyin: {blob[:200]}")

    print("--- cast pairs ---")
    for sid, ps in pairs.items():
        print(f"  {sid}: {ps}")
    print("--- sample lines ---")
    for sid, lines in by_sid.items():
        print(f"  [{sid}] {lines[0][:80]}")

    _ok(
        f"3) Dich OpenAI that ({len(translated)} doan, speakers={sorted(by_sid)}) "
        f"-> {out_dir / 'segments_out.json'}"
    )


def main() -> int:
    print("=== verify OpenAI translate gate ===")
    test1_empty_key_blocks_start()
    test2_bad_key_fails_no_google()
    test3_good_key_multicast()
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
