#!/usr/bin/env python3
"""Chay dub 1000 link Douyin local -> output/<id>.mp4. Bo qua clip da xong."""
from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.main import process_job  # noqa: E402
from dyflow.utils import load_config, log, setup_logging  # noqa: E402

LINKS = ROOT / "output" / "douyin_guoman_links.txt"
OUT_DIR = ROOT / "output"
LOG = ROOT / "output" / "guoman_batch.log"
FAIL = ROOT / "output" / "guoman_batch_fail.txt"


def jobs_from_links() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for ln in LINKS.read_text(encoding="utf-8").splitlines():
        m = re.search(r"(\d{15,25})", ln.strip())
        if not m:
            continue
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        items.append({
            "id": f"gm_{vid}",
            "source": f"https://www.douyin.com/video/{vid}",
            "script": "AUTO",
            "voiceover": {"audio_mode": "dub"},
            "export": {"fit": "source", "formats": ["source"]},
        })
    return items


def done_path(job_id: str) -> Path:
    return OUT_DIR / f"{job_id}.mp4"


def append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def main() -> int:
    setup_logging(True)
    cfg = load_config(str(ROOT / "config.yaml"))
    jobs = jobs_from_links()
    total = len(jobs)
    log.info("Batch guoman: %d job -> %s", total, OUT_DIR)
    ok = skip = fail = 0
    for i, job in enumerate(jobs, 1):
        jid = job["id"]
        out = done_path(jid)
        if out.exists() and out.stat().st_size > 50_000:
            skip += 1
            log.info("[%d/%d] SKIP %s", i, total, jid)
            continue
        log.info("[%d/%d] START %s", i, total, jid)
        try:
            process_job(job, cfg)
            ok += 1
            append(LOG, f"OK {jid}")
            log.info("[%d/%d] OK %s", i, total, jid)
        except Exception as e:  # noqa: BLE001
            fail += 1
            msg = f"FAIL {jid}: {e}"
            append(FAIL, msg)
            append(LOG, msg)
            log.error("[%d/%d] %s", i, total, msg)
            traceback.print_exc()
        log.info("tien do ok=%d skip=%d fail=%d / %d", ok, skip, fail, total)
    log.info("XONG batch. ok=%d skip=%d fail=%d", ok, skip, fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
