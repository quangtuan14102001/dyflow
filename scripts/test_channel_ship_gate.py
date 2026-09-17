#!/usr/bin/env python3
"""Gate: channel list must work before shipping a Windows zip."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Bad sec_uid (user paste) + vid= — must recover author and list many clips.
KK_BAD = (
    "https://www.douyin.com/user/"
    "MS4wLjABAAAAr7cM94IrRPrVqd5FcTKOxX2qV2_Z5DCLQI82IZJQYHwJBqVo4RiN4yuH5Gnq11Vq"
    "?from_tab_name=main&vid=7679803336194854180"
)
OTHER = (
    "https://www.douyin.com/user/"
    "MS4wLjABAAAAmU8p0qdhVUjtay9FVRE5_tqWsnzN_u_8p7C9WtNtJaw"
    "?vid=7649978535217614120"
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def main() -> int:
    from dyflow.downloader import list_channel_videos
    from dyflow.web.quote import expand_sources
    from dyflow.browser_launch import looks_like_chromium_argv

    _assert(looks_like_chromium_argv(["--type=renderer"]), "chromium argv")
    _assert(not looks_like_chromium_argv(["--ytdlp"]), "ytdlp not chromium")

    # 1) KK bad sec — twice in a row (no "Edge dang ban")
    for i in range(2):
        t0 = time.time()
        got = list_channel_videos(KK_BAD, {}, limit=18, allow_chrome=False)
        dt = time.time() - t0
        print(f"KK pass{i+1}: n={len(got)} t={dt:.1f}s")
        _assert(len(got) >= 10, f"KK only {len(got)} clips")
        _assert(dt < 45, f"KK too slow {dt:.1f}s")

    # 2) expand_sources (what Preview uses)
    t0 = time.time()
    srcs = expand_sources(KK_BAD, {}, limit=18, channel_limit=18, allow_chrome=False)
    dt = time.time() - t0
    print(f"expand: n={len(srcs)} t={dt:.1f}s")
    _assert(len(srcs) >= 10, f"expand only {len(srcs)}")
    _assert(dt < 45, f"expand too slow {dt:.1f}s")

    # 3) other channel
    t0 = time.time()
    got2 = list_channel_videos(OTHER, {}, limit=15, allow_chrome=False)
    dt = time.time() - t0
    print(f"other: n={len(got2)} t={dt:.1f}s")
    _assert(len(got2) >= 5, f"other only {len(got2)}")

    # 4) two sequential previews (user spam-click pattern)
    t0 = time.time()
    a = list_channel_videos(KK_BAD, {}, limit=12, allow_chrome=False)
    b = list_channel_videos(KK_BAD, {}, limit=12, allow_chrome=False)
    dt = time.time() - t0
    print(f"spam2: n={len(a)}/{len(b)} t={dt:.1f}s")
    _assert(len(a) >= 5 and len(b) >= 5, f"spam2 fail {len(a)}/{len(b)}")

    # 5) parallel listings must not raise "Edge dang ban"
    box: dict = {"err": None, "na": 0, "nb": 0}

    def _a() -> None:
        try:
            box["na"] = len(list_channel_videos(KK_BAD, {}, limit=10, allow_chrome=False))
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    def _b() -> None:
        try:
            box["nb"] = len(list_channel_videos(OTHER, {}, limit=10, allow_chrome=False))
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    t1 = threading.Thread(target=_a)
    t2 = threading.Thread(target=_b)
    t1.start()
    t2.start()
    t1.join(60)
    t2.join(60)
    print(f"parallel: n={box['na']}/{box['nb']} err={box['err']}")
    _assert(box["err"] is None, f"parallel error: {box['err']}")
    _assert(box["na"] >= 5 and box["nb"] >= 5, f"parallel counts {box['na']}/{box['nb']}")

    print("GATE OK — safe to ship")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
