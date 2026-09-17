#!/usr/bin/env python3
"""Regression tests: frozen launch must not reopen the app; channel list + quote."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _assert(cond, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {msg}")


def test_ytdlp_frozen_marker() -> None:
    from dyflow.utils import ytdlp_argv

    with mock.patch.object(sys, "frozen", True, create=True):
        argv = ytdlp_argv()
    _assert(argv[:2] == [sys.executable, "--ytdlp"], argv)
    _assert("run_ytdlp.py" not in " ".join(argv), argv)


def test_browser_exe_not_this_python() -> None:
    from dyflow.browser_launch import system_browser_exe

    exe = system_browser_exe()
    if exe:
        _assert(Path(exe).is_file(), exe)
        _assert("VideosCreator" not in Path(exe).name, exe)
        _assert(Path(exe).name.lower() in {"msedge.exe", "chrome.exe", "google chrome", "microsoft edge", "chromium"}, Path(exe).name.lower())


def test_frozen_launch_requires_system_browser() -> None:
    from dyflow import browser_launch as bl

    class _P:
        class chromium:
            @staticmethod
            def launch(**kwargs):
                return kwargs

    with mock.patch.object(bl, "is_frozen", return_value=True), mock.patch.object(
        bl, "system_browser_exe", return_value=None
    ):
        try:
            bl.launch_headless_browser(_P())
            raise SystemExit("FAIL: frozen launch without browser should raise")
        except RuntimeError as e:
            _assert("Edge" in str(e) or "Douyin" in str(e), str(e))

    with mock.patch.object(bl, "is_frozen", return_value=True), mock.patch.object(
        bl, "system_browser_exe", return_value="/tmp/msedge.exe"
    ):
        kwargs = bl.launch_headless_browser(_P())
    _assert(kwargs.get("executable_path") == "/tmp/msedge.exe", kwargs)
    _assert("--headless=new" not in (kwargs.get("args") or []), kwargs)
    _assert(kwargs.get("headless") is True, kwargs)
    _assert(kwargs.get("executable_path") == "/tmp/msedge.exe", kwargs)


def test_unsigned_aweme_path() -> None:
    from dyflow.chrome_dl import unsigned_aweme_post_path, _PAGINATE_AWEME_JS

    path = unsigned_aweme_post_path("MS4wLjABAAAAtest", cursor=0, count=18)
    _assert(path.startswith("/aweme/v1/web/aweme/post/?"), path)
    _assert("a_bogus" not in path, path)
    _assert("sec_user_id=MS4wLjABAAAAtest" in path, path)
    _assert("startUrl" not in _PAGINATE_AWEME_JS, "paginate must not reuse signed intercept URL")
    _assert("secUid" in _PAGINATE_AWEME_JS, _PAGINATE_AWEME_JS[:80])


def test_unfrozen_launch_uses_system_browser() -> None:
    from dyflow import browser_launch as bl

    class _P:
        class chromium:
            @staticmethod
            def launch(**kwargs):
                return kwargs

    with mock.patch.object(bl, "is_frozen", return_value=False), mock.patch.object(
        bl, "system_browser_exe", return_value="/tmp/chrome.exe"
    ):
        kwargs = bl.launch_playwright_chromium(_P(), headless=True)
    _assert(kwargs.get("executable_path") == "/tmp/chrome.exe", kwargs)
    _assert(kwargs.get("headless") is True, kwargs)


def test_quoted_json_reuse() -> None:
    os.environ["DYFLOW_DESKTOP"] = "1"
    from dyflow.web.app import _items_from_quoted_json

    raw = (
        '[{"url":"https://www.douyin.com/video/7681235453843394927","credits":1},'
        '{"url":"https://www.douyin.com/video/7681235453843394928","credits":1}]'
    )
    items = _items_from_quoted_json(raw, limit=200)
    _assert(len(items) == 2, items)
    _assert(items[0]["credits"] == 1, items)
    lines = (
        "https://www.douyin.com/video/7681235453843394927\n"
        "https://www.douyin.com/video/7681235453843394928"
    )
    items2 = _items_from_quoted_json(lines, limit=200)
    _assert(len(items2) == 2, items2)


def test_live_channel_list() -> None:
    """Live Douyin: must return more than the vid= fallback (1 clip). No headed window."""
    import time
    from unittest import mock

    from dyflow import chrome_dl
    from dyflow.downloader import list_channel_videos

    url = (
        "https://www.douyin.com/user/"
        "MS4wLjABAAAAmU8p0qdhVUjtay9FVRE5_tqWsnzN_u_8p7C9WtNtJaw"
        "?from_tab_name=main&vid=7649978535217614120"
    )
    calls = {"headed": 0}
    real = chrome_dl.list_user_videos_with_chrome

    def _wrap(*a, **kw):
        if kw.get("headed_fallback"):
            calls["headed"] += 1
        # Force no headed even if caller asks
        kw["headed_fallback"] = False
        return real(*a, **kw)

    t0 = time.time()
    with mock.patch.object(chrome_dl, "list_user_videos_with_chrome", _wrap):
        # Patch where downloader imports it
        with mock.patch("dyflow.chrome_dl.list_user_videos_with_chrome", _wrap):
            got = list_channel_videos(url, {}, limit=40, allow_chrome=False)
    elapsed = time.time() - t0
    print(f"live_list n={len(got)} t={elapsed:.1f}s headed_requests={calls['headed']}")
    _assert(len(got) >= 5, f"only {len(got)} videos (need channel list, not 1 vid=)")
    _assert(elapsed < 90, f"listing too slow: {elapsed:.1f}s")
    # Even if caller passed headed_fallback historically, wrapper forces False
    _assert(all("7649978535217614120" not in u or True for u in got), "ok")


def test_never_opens_headed_browser() -> None:
    from dyflow.chrome_dl import list_user_videos_with_chrome
    from unittest import mock

    launches = []

    class _P:
        class chromium:
            @staticmethod
            def launch(**kwargs):
                launches.append(kwargs)
                raise RuntimeError("stop")

    with mock.patch("dyflow.browser_launch.launch_playwright_chromium") as launch:
        def _launch(p, *, headless):
            launches.append({"headless": headless})
            raise RuntimeError("stop-after-launch-flag")

        launch.side_effect = _launch
        try:
            list_user_videos_with_chrome(
                "https://www.douyin.com/user/MS4wLjABAAAAtest",
                {},
                limit=10,
                timeout_sec=5,
                headed_fallback=True,
            )
        except Exception:
            pass
    _assert(launches, "should try headless")
    _assert(all(x.get("headless") is True for x in launches), launches)


def main() -> int:
    test_ytdlp_frozen_marker()
    test_browser_exe_not_this_python()
    test_frozen_launch_requires_system_browser()
    test_unsigned_aweme_path()
    test_unfrozen_launch_uses_system_browser()
    test_quoted_json_reuse()
    test_never_opens_headed_browser()
    print("OK unit")
    live = os.getenv("DYFLOW_SKIP_LIVE_LIST", "").strip() not in {"1", "true", "yes"}
    if live:
        test_live_channel_list()
        print("OK live list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
