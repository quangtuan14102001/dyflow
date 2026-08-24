#!/usr/bin/env python3
"""CLI: kiem tra dub quality — moi job deu co segments_qa.json sau pipeline.

  python scripts/audit_dub.py work/<job_id>
  python scripts/audit_dub.py work/<job_id> --fail
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.dub_audit import audit_script_han, audit_work_dir  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Audit dub segment timings")
    p.add_argument("work_dir", help="Thu muc work/<job_id>")
    p.add_argument("--fail", action="store_true", help="Exit 1 neu loi")
    p.add_argument("--engine", default="edge", help="edge | openai")
    p.add_argument("--min-coverage", type=float, default=0.55)
    args = p.parse_args()

    code, summary, lines = audit_work_dir(
        Path(args.work_dir),
        tts_engine=args.engine,
        min_coverage=args.min_coverage,
    )
    print(f"{'#':>2} start  cov  spd  flags  text")
    print("-" * 72)
    for ln in lines:
        print(ln)
    print("-" * 72)
    print(summary)
    han = audit_script_han(Path(args.work_dir))
    if han:
        print(f"FAIL: {han} ky tu Han trong script")
        code = 1
    if args.fail and code:
        return code
    return 0 if not args.fail else code


if __name__ == "__main__":
    sys.exit(main())
