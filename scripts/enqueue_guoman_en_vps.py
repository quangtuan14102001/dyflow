#!/usr/bin/env python3
"""Enqueue 1000 Douyin links as English web jobs for a given user (VPS only)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dyflow.web import models  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    p.add_argument("--links", required=True, type=Path)
    p.add_argument("--lang", default="en")
    p.add_argument("--voice", default="male")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    urls = []
    seen = set()
    for line in args.links.read_text(encoding="utf-8").splitlines():
        u = line.strip()
        if not u or u.startswith("#"):
            continue
        if u in seen:
            continue
        seen.add(u)
        urls.append(u)
    if args.limit:
        urls = urls[: args.limit]
    if not urls:
        print("no urls", file=sys.stderr)
        return 1

    user = models.get_user_by_email(args.email)
    if not user:
        print(f"user not found: {args.email}", file=sys.stderr)
        return 2

    existing = set()
    with models._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT source_url FROM jobs WHERE user_id=? AND target_lang=?",
            (user["id"], args.lang),
        ).fetchall()
        existing = {r["source_url"] for r in rows}

    new = [u for u in urls if u not in existing]
    skipped = len(urls) - len(new)
    print(f"user={user['email']} id={user['id']} admin={user.get('is_admin')} credits={user['credits']}")
    print(f"links={len(urls)} already_queued_or_done={skipped} to_insert={len(new)}")
    if not new:
        return 0

    # Owner: cost 0. Chunk to keep transactions small.
    chunk = 50
    total_ids = 0
    for i in range(0, len(new), chunk):
        batch = new[i : i + chunk]
        costs = [0] * len(batch)
        ref = f"guoman-en:{i // chunk}:{len(batch)}"
        result = models.create_jobs_with_trial(
            user["id"],
            batch,
            args.voice,
            args.lang,
            costs,
            ref,
            trial_ok=False,
        )
        if result is None:
            print(f"insert failed at offset {i}", file=sys.stderr)
            return 3
        ids, charged = result
        total_ids += len(ids)
        print(f"  chunk {i // chunk + 1}: {len(ids)} jobs charged={sum(charged)}")

    print(f"inserted={total_ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
