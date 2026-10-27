#!/usr/bin/env python3
"""Verify content QA gate + OpenAI default + suspect source + timing overhead."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.translate_llm import (  # noqa: E402
    enforce_content_qa_gate,
    flag_suspicious_sources,
    resolve_translate_engine,
    review_segments_content,
)


def _static() -> None:
    # Empty / auto → openai when key mocked
    with mock.patch("dyflow.preflight.openai_key", return_value="sk-test"):
        assert resolve_translate_engine(None) == "openai"
        assert resolve_translate_engine("") == "openai"
        assert resolve_translate_engine("auto") == "openai"
    with mock.patch("dyflow.preflight.openai_key", return_value=""):
        assert resolve_translate_engine(None) == "google"
        assert resolve_translate_engine("") == "google"
    # Explicit user choice wins
    with mock.patch("dyflow.preflight.openai_key", return_value="sk-test"):
        assert resolve_translate_engine("google") == "google"
        assert resolve_translate_engine("openai") == "openai"

    segs = [
        {"start": 0.0, "end": 4.0, "source_text": "啊", "text": "啊"},  # too sparse
        {
            "start": 4.0, "end": 6.0,
            "source_text": "妈妈今天做了很好吃的饭。" * 20,
            "text": "x",
        },  # too dense
        {
            "start": 6.0, "end": 8.0,
            "source_text": "妈妈今天做了很好吃的饭。",
            "text": "Mẹ hôm nay nấu cơm rất ngon.",
        },
    ]
    flagged = flag_suspicious_sources(segs)
    assert flagged[0].get("source_suspect"), flagged[0]
    assert flagged[1].get("source_suspect"), flagged[1]
    assert not flagged[2].get("source_suspect"), flagged[2]
    print("STATIC OK")


def _live_genres() -> int:
    from dyflow.preflight import hydrate_openai_key, openai_key

    hydrate_openai_key()
    if not openai_key():
        print("SKIP live — no OpenAI key")
        return 0

    # 5 "job" mini: family / tutorial / sci-fi / myth / dialogue — mixed good+bad
    jobs = [
        {
            "name": "family",
            "segs": [
                {
                    "start": 0, "end": 3,
                    "source_text": "妈妈，我回来了。",
                    "text": "Mẹ, con về rồi.",
                    "speaker_id": "boy",
                },
                {
                    "start": 3, "end": 6,
                    "source_text": "快洗手吃饭。",
                    "text": "Hôm nay chứng khoán tăng mạnh.",  # SAI — bịa
                    "speaker_id": "mom",
                },
            ],
        },
        {
            "name": "tutorial",
            "segs": [
                {
                    "start": 0, "end": 4,
                    "source_text": "首先打开设置页面。",
                    "text": "Trước tiên mở trang cài đặt.",
                },
                {
                    "start": 4, "end": 8,
                    "source_text": "然后点击保存。",
                    "text": "Rồi bấm Lưu.",
                },
            ],
        },
        {
            "name": "scifi",
            "segs": [
                {
                    "start": 0, "end": 3,
                    "source_text": "启动青龙阵！",
                    "text": "Kích hoạt Azure Dragon Formation!",  # leak EN
                },
                {
                    "start": 3, "end": 6,
                    "source_text": "白泽出现了。",
                    "text": "Bai Ze xuất hiện.",  # pinyin
                },
            ],
        },
        {
            "name": "myth",
            "segs": [
                {
                    "start": 0, "end": 3,
                    "source_text": "夸父追日。",
                    "text": "Khoa Phụ đuổi theo mặt trời.",
                },
            ],
        },
        {
            "name": "dialogue",
            "segs": [
                {
                    "start": 0, "end": 2.5,
                    "source_text": "你去哪儿？",
                    "text": "Bạn đi đâu?",
                },
                {
                    "start": 2.5, "end": 5.0,
                    "source_text": "我去超市买菜。",
                    "text": "Tôi đi siêu thị mua rau.",
                },
            ],
        },
    ]

    totals = {
        "jobs": 0,
        "segs": 0,
        "sai_before": 0,
        "retried": 0,
        "unresolved": 0,
        "gan_dung": 0,
        "elapsed": 0.0,
        "fail_closed": 0,
    }
    details = []

    for job in jobs:
        totals["jobs"] += 1
        segs = [dict(s) for s in job["segs"]]
        totals["segs"] += len(segs)
        with tempfile.TemporaryDirectory() as td:
            wp = Path(td)
            t0 = time.perf_counter()
            try:
                out = enforce_content_qa_gate(
                    segs,
                    video_dur=max(float(s["end"]) for s in segs) + 1,
                    target="vi",
                    chars_per_sec=14.5,
                    work_dir=wp,
                    fail_closed=True,
                )
                failed = False
            except RuntimeError as e:
                failed = True
                totals["fail_closed"] += 1
                out = segs
                print(f"  [{job['name']}] FAIL-CLOSED: {e}")
            elapsed = time.perf_counter() - t0
            totals["elapsed"] += elapsed
            gate = {}
            gp = wp / "content_qa_gate.json"
            if gp.exists():
                gate = json.loads(gp.read_text(encoding="utf-8"))
            sm = gate.get("summary") or {}
            totals["retried"] += int(sm.get("retried") or 0)
            totals["unresolved"] += int(sm.get("unresolved") or 0)
            totals["gan_dung"] += int(sm.get("gan_dung_warn") or 0)
            # sai before ≈ retried + unresolved (approx)
            totals["sai_before"] += int(sm.get("retried") or 0) + int(
                sm.get("unresolved") or 0
            )
            # term consistency check on output texts
            texts = " | ".join(str(s.get("text") or "") for s in out)
            detail = {
                "name": job["name"],
                "elapsed_sec": round(elapsed, 2),
                "summary": sm,
                "fail_closed": failed,
                "texts": texts[:200],
            }
            details.append(detail)
            print(
                f"  [{job['name']}] {elapsed:.1f}s "
                f"retried={sm.get('retried')} unresolved={sm.get('unresolved')} "
                f"gan_dung={sm.get('gan_dung_warn')} fail={failed}"
            )

    avg = totals["elapsed"] / max(1, totals["jobs"])
    print("\n=== BENCH SUMMARY (5 mini-jobs) ===")
    print(json.dumps({**totals, "avg_sec_per_job": round(avg, 2)}, ensure_ascii=False, indent=2))
    print("\nChi tiết:")
    for d in details:
        print(f"  - {d['name']}: {d['elapsed_sec']}s fail={d['fail_closed']} | {d['texts'][:80]}")

    # Expect family/scifi may retry; tutorial/myth/dialogue should mostly pass
    assert totals["jobs"] == 5
    print("LIVE OK")
    return 0


def main() -> int:
    _static()
    return _live_genres()


if __name__ == "__main__":
    raise SystemExit(main())
