#!/usr/bin/env python3
"""Helpers liet ke kenh Douyin (khong can mang).

  python scripts/test_channel_list.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dyflow.chrome_dl import (  # noqa: E402
    _is_aweme_post_url,
    aweme_ids_from_user_post,
    pc_user_url,
    post_has_more,
    post_next_cursor,
    sec_uid_from_user_url,
)
from dyflow.pipeline import is_channel_url  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def main() -> int:
    sec = "MS4wLjABAAAAmU8p0qdhVUjtay9FVRE5_tqWsnzN_u_8p7C9WtNtJaw"
    href = (
        f"https://www.douyin.com/user/{sec}"
        "?from_tab_name=main&vid=7649978535217614120"
    )
    _assert(is_channel_url(href), "user+vid (no modal) is channel")
    _assert(is_channel_url(f"https://www.douyin.com/user/{sec}"), "clean user is channel")
    _assert(
        not is_channel_url(f"https://www.douyin.com/user/{sec}?modal_id=7649978535217614120"),
        "modal = not channel",
    )
    _assert(sec_uid_from_user_url(href) == sec, "sec uid")
    pc = pc_user_url(href)
    _assert(pc == f"https://www.douyin.com/user/{sec}", pc)
    _assert("vid=" not in pc, "strip vid")
    _assert("iesdouyin" not in pc, "pc not share")

    share = f"https://www.iesdouyin.com/share/user/{sec}"
    _assert(pc_user_url(share) == pc, "share -> pc")

    api = (
        "https://www.douyin.com/aweme/v1/web/aweme/post/?"
        f"sec_user_id={quote(sec)}&max_cursor=0&count=18"
    )
    _assert(_is_aweme_post_url(api, sec), "encoded sec_user_id still matches")
    _assert(
        not _is_aweme_post_url(api, "MS4wLjABAAAA_other"),
        "wrong sec rejected",
    )

    page1 = {
        "aweme_list": [{"aweme_id": "1111111111111111111", "create_time": 1700000000}],
        "max_cursor": 1700000000000,
        "has_more": 0,
    }
    _assert(post_has_more(page1) is False, "has_more false")
    _assert(post_next_cursor(page1, 0) == 1700000000000, "cursor advances anyway")
    ids = aweme_ids_from_user_post(page1)
    _assert(ids == ["1111111111111111111"], ids)

    print("OK channel_list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
