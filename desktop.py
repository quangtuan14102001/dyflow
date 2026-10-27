#!/usr/bin/env python3
"""Mo VideosCreator nhu app desktop (cua so + pipeline local).

Mac / Linux (dev):
  python desktop.py

Windows .exe: build bang GitHub Actions (workflow build-windows.yml)
hoac tren may Windows: powershell -File scripts/build_windows.ps1
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Uu tien nap ban va patch dong tu AppData neu co (truoc bat ky import dyflow nao)
try:
    if sys.platform == "win32":
        _appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        _patch_dir = Path(_appdata) / "VideosCreator" / "patch"
    elif sys.platform == "darwin":
        _patch_dir = Path.home() / "Library" / "Application Support" / "VideosCreator" / "patch"
    else:
        _patch_dir = Path.home() / ".config" / "videoscreator" / "patch"

    if _patch_dir.is_dir():
        if str(_patch_dir) not in sys.path:
            sys.path.insert(0, str(_patch_dir))
        _patch_dyflow = _patch_dir / "dyflow"
        if _patch_dyflow.is_dir():
            import dyflow
            _pstr = str(_patch_dyflow.resolve())
            if _pstr not in dyflow.__path__:
                dyflow.__path__.insert(0, _pstr)
except Exception:
    pass

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.platform == "win32":
    # Dam bao Windows DLL loader tim thay moi thu vien C++ runtime va torch/lib
    for _p in [
        getattr(sys, "_MEIPASS", None),
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
        (Path(sys.executable).resolve().parent / "_internal") if getattr(sys, "frozen", False) else None,
        (Path(sys.executable).resolve().parent / "_internal" / "torch" / "lib") if getattr(sys, "frozen", False) else None,
    ]:
        if _p and Path(_p).is_dir():
            try:
                os.add_dll_directory(str(_p))
            except Exception:
                pass
            os.environ["PATH"] = str(_p) + os.pathsep + os.environ.get("PATH", "")


def _no_proxy_opener():
    """Windows: proxy he thong lam GET 127.0.0.1 bi timeout."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _is_socket_listening(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _wait_up(
    url: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    server_thread: threading.Thread | None = None,
    serve_errors: list[BaseException] | None = None,
    timeout: float = 30.0,
) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    opener = _no_proxy_opener()
    health_url = f"http://{host}:{port}/api/healthz"
    while time.time() < deadline:
        if server_thread is not None and not server_thread.is_alive():
            err_msg = f": {serve_errors[0]!r}" if serve_errors else ""
            raise RuntimeError(f"Server web uvicorn bi dung dot ngot{err_msg}")
        try:
            with opener.open(health_url, timeout=4) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError) as e:
            last = e
            # Neu HTTP probe cham hoac bi proxy chan nhung TCP socket da lang nghe:
            if _is_socket_listening(host, port):
                _log_startup(f"TCP socket {host}:{port} da mo thanh cong (HTTP probe: {e})")
                return
            time.sleep(0.3)
    # Neu het timeout ma thread uvicorn van song va port TCP dang mo:
    if server_thread is not None and server_thread.is_alive() and _is_socket_listening(host, port):
        _log_startup(
            f"Canh bao: HTTP probe {health_url} timed out nhung server van dang chay -> tiep tuc mo giao dien"
        )
        return
    raise RuntimeError(f"Server khong mo duoc {url}: {last}")


def _startup_log() -> Path:
    try:
        from dyflow.desktop_mode import data_dir

        return data_dir() / "startup.log"
    except Exception:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VideosCreator"
        base.mkdir(parents=True, exist_ok=True)
        return base / "startup.log"


def _log_startup(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n"
    try:
        p = _startup_log()
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(msg, flush=True)


def _write_crash_log(msg: str) -> None:
    """Ghi crash.log ngay canh file VideosCreator.exe de nguoi dung gui log de dang."""
    try:
        exe_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else ROOT
        )
        crash_file = exe_dir / "crash.log"
        with crash_file.open("a", encoding="utf-8") as f:
            f.write(f"==== {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n{msg}\n\n")
    except Exception:
        pass


def _has_webview2_installed() -> bool:
    """Kiem tra xem may Windows da co Microsoft Edge WebView2 Runtime chua."""
    if sys.platform != "win32":
        return True
    try:
        import winreg

        guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for prefix in (
                r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
                r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
            ):
                try:
                    with winreg.OpenKey(root, f"{prefix}\\{guid}") as k:
                        pv, _ = winreg.QueryValueEx(k, "pv")
                        if pv and pv != "0.0.0.0":
                            return True
                except OSError:
                    pass
        import glob

        for prog in (
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramFiles"),
        ):
            if prog and glob.glob(
                os.path.join(
                    prog,
                    "Microsoft",
                    "EdgeWebView",
                    "Application",
                    "*",
                    "msedgewebview2.exe",
                )
            ):
                return True
    except Exception:
        pass
    return False


def _find_free_port(start: int = 8765, max_tries: int = 50) -> int:
    """Tim port trong de tranh crash [Errno 10048] khi port cu bi chiem dung."""
    import socket

    for p in range(start, start + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", p))
                return p
        except OSError:
            continue
    return start


def _win_focus_existing() -> bool:
    """Dua cua so VideosCreator dang mo len foreground. True neu tim thay cua so app that va dang hien thi."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        # Chi tim cua so co Window Class la "VideosCreatorWnd" (lop cua so native cua VideosCreatorWebView.dll).
        # TUYET DOI KHONG dung FindWindowW(None, "VideosCreator") vi se bi trung ten voi:
        # 1. Thu muc Windows Explorer "VideosCreator" khi nguoi dung giai nen zip
        # 2. Cua so cmd cua file Chay-VideosCreator.bat
        # 3. Tab trinh duyet
        hwnd = user32.FindWindowW("VideosCreatorWnd", None)
        if not hwnd or not user32.IsWindowVisible(hwnd):
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _win_claim_gui() -> bool:
    """True neu duoc phep mo cua so moi. Chi chan khi DA CO CUA SO NATIVE DANG HIEN THI tren man hinh."""
    if sys.platform != "win32":
        return True
    if _win_focus_existing():
        _log_startup("Cua so VideosCreator da ton tai tren man hinh -> Focus vao cua so")
        return False
    return True


_worker_proc = None


def _default_desktop_workers() -> int:
    env_w = os.getenv("DYFLOW_WEB_WORKERS", "").strip()
    if env_w.isdigit() and int(env_w) >= 1:
        return int(env_w)
    cpu = os.cpu_count() or 4
    # Mac dinh: 2 luong chay song song tren may >= 4 cores de hang cho chay 2 video dong thoi
    return 2 if cpu >= 4 else 1


def _run_worker_only() -> int:
    """Process chi chay worker pool (spawn tu GUI Windows)."""
    try:
        from dyflow.desktop_mode import prepare

        prepare()
        n_workers = str(_default_desktop_workers())
        os.environ["DYFLOW_DESKTOP"] = "1"
        os.environ["DYFLOW_WEB_WORKERS"] = n_workers
        os.environ["DYFLOW_MAX_CONCURRENT_PER_USER"] = n_workers
        os.environ.pop("DYFLOW_WEB_NO_WORKER", None)
        from dyflow.utils import setup_logging
        from dyflow.web import worker

        setup_logging(False)
        _log_startup(f"worker-only process start ({n_workers} workers)")
        worker.run_forever(os.getenv("DYFLOW_CONFIG") or "config.yaml")
        _log_startup("worker-only process exit (run_forever returned)")
        return 0
    except Exception as e:  # noqa: BLE001
        _log_startup(f"worker-only CRASH: {e!r}")
        err_str = str(e).lower()
        if "126" in str(e) or "dll" in err_str or "specified module could not be found" in err_str:
            _win_alert(
                "THÔNG BÁO TỪ VIDEOSCREATOR:\n\n"
                "Máy tính của bạn đang thiếu thư viện Microsoft Visual C++ 2015-2022 (x64) để chạy AI PyTorch/Whisper.\n\n"
                "Trong thư mục ứng dụng đã có sẵn file: 'Cai-Dat-Visual-C++-Neu-Loi.exe'.\n"
                "Vui lòng nhấp đúp vào file đó để cài đặt (chỉ mất 10 giây),\n"
                "hoặc tải tại: https://aka.ms/vs/17/release/vc_redist.x64.exe\n\n"
                "Sau khi cài đặt xong, hãy mở lại VideosCreator."
            )
            return 1
        raise


def _spawn_worker_process() -> None:
    """Windows frozen: worker trong process rieng — tranh treo warmup/JIT chan hang doi."""
    global _worker_proc
    if not (sys.platform == "win32" and getattr(sys, "frozen", False)):
        return
    try:
        import atexit
        import subprocess

        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        # Bo NO_WORKER trong env con — worker can claim job.
        child_env = os.environ.copy()
        child_env.pop("DYFLOW_WEB_NO_WORKER", None)
        n_workers = str(_default_desktop_workers())
        child_env["DYFLOW_WEB_WORKERS"] = n_workers
        child_env["DYFLOW_MAX_CONCURRENT_PER_USER"] = n_workers
        worker_log = _startup_log().parent / "worker.log"
        log_f = open(worker_log, "a", encoding="utf-8", errors="replace")
        log_f.write(
            f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} spawn worker ({n_workers} threads) ====\n"
        )
        log_f.flush()
        _worker_proc = subprocess.Popen(
            [sys.executable, "--worker"],
            cwd=str(Path(sys.executable).resolve().parent),
            env=child_env,
            creationflags=creation,
            close_fds=False,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
        _log_startup(f"spawned --worker pid={_worker_proc.pid} ({n_workers} threads) log={worker_log}")

        def _kill_worker() -> None:
            p = _worker_proc
            if p is None or p.poll() is not None:
                return
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass

        atexit.register(_kill_worker)
    except Exception as e:  # noqa: BLE001
        _log_startup(f"spawn --worker fail: {e}")


def main() -> int:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if not (exe_dir / "_internal").is_dir():
            _win_alert(
                "BẠN CẦN GIẢI NÉN FILE ZIP TRƯỚC KHI MỞ!\n\n"
                "Vui lòng chuột phải vào file VideosCreator.zip -> Chọn 'Extract All' (Giải nén tất cả),\n"
                "sau đó vào thư mục vừa giải nén và mở VideosCreator.exe."
            )
            return 1
        if not _win_claim_gui():
            return 0
    from dyflow.desktop_mode import prepare

    prepare()
    os.environ["DYFLOW_DESKTOP"] = "1"
    n_workers = str(_default_desktop_workers())
    os.environ["DYFLOW_WEB_WORKERS"] = n_workers
    os.environ["DYFLOW_MAX_CONCURRENT_PER_USER"] = n_workers
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    for _k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        os.environ.pop(_k, None)

    if sys.platform == "win32" and getattr(sys, "frozen", False):
        _win_unblock_install()
    # FastAPI startup khong start worker: uvicorn trong thread Windows
    # de soat startup. Ta start worker o main thread cho chac.
    os.environ["DYFLOW_WEB_NO_WORKER"] = "1"

    from dyflow.stdlib_fix import ensure_thread_stdlib

    ensure_thread_stdlib()

    import uvicorn
    from dyflow.web.app import app
    from dyflow.web import models, worker

    ready_event = threading.Event()

    @app.middleware("http")
    async def _detect_client_ready(request, call_next):
        ua = request.headers.get("user-agent", "").lower()
        if "python" not in ua:
            ready_event.set()
        return await call_next(request)

    models.init_db()
    # Chi go job RUNNING do process cu — GIU queued de hang doi chay tiep sau khi mo lai.
    recovered = models.recover_stale(include_queued=False)
    models.ensure_desktop_owner()
    cfg_path = os.getenv("DYFLOW_CONFIG") or "config.yaml"
    if recovered:
        print(f"Da go {recovered} job running bi ket — hang doi queued van chay tiep.")

    host = "127.0.0.1"
    env_port = os.getenv("DYFLOW_DESKTOP_PORT")
    port = int(env_port) if env_port else _find_free_port(8765)
    url = f"http://{host}:{port}/"
    os.environ["PUBLIC_BASE_URL"] = f"http://{host}:{port}"
    serve_err: list[BaseException] = []

    def _serve() -> None:
        try:
            _log_startup(f"uvicorn bind {host}:{port}")
            # PyInstaller: dictConfig uvicorn (formatter 'default') crash.
            uvicorn.run(
                app,
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
                log_config=None,
            )
        except Exception as e:  # noqa: BLE001
            serve_err.append(e)
            _log_startup(f"uvicorn crash: {e!r}")

    # Mo HTTP truoc, warmup worker sau — lan dau Win nap torch co the >30s.
    t = threading.Thread(target=_serve, name="dyflow-uvicorn", daemon=True)
    t.start()
    wait_sec = 60.0 if getattr(sys, "frozen", False) else 30.0
    try:
        _wait_up(url, host=host, port=port, server_thread=t, serve_errors=serve_err, timeout=wait_sec)
    except RuntimeError as e:
        extra = f" | uvicorn: {serve_err[0]!r}" if serve_err else ""
        _log_startup(f"wait_up fail: {e}{extra}")
        raise RuntimeError(
            f"{e}{extra}. Vui long kiem tra firewall/proxy, hoac xem {_startup_log()}"
        ) from e

    # Windows .exe: worker process rieng (in-process thuong khong claim duoc job).
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        _spawn_worker_process()
    else:
        worker.start_background(cfg_path)

    force_browser = (
        "--browser" in sys.argv
        or os.getenv("DYFLOW_BROWSER_MODE", "").strip().lower() in ("1", "true", "yes")
    )
    if sys.platform == "win32" and not force_browser:
        # Neu may chua co WebView2 Runtime -> Mo trinh duyet truc tiep, khong de loi
        if not _has_webview2_installed():
            _log_startup("WebView2 Runtime chua cai dat -> Fallback sang trinh duyet")
            webbrowser.open(url)
            _win_alert(
                "THÔNG BÁO TỪ VIDEOSCREATOR:\n\n"
                "Máy tính của bạn chưa cài đặt Microsoft Edge WebView2 Runtime.\n\n"
                "Ứng dụng đã tự động mở trên Trình Duyệt Web mặc định để bạn sử dụng ngay.\n\n"
                "Để mở dạng cửa sổ phần mềm, bạn có thể tải WebView2 miễn phí tại:\n"
                "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
            )
            try:
                while t.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                return 0
            return 0

        try:
            from dyflow.win_webview import run_desktop

            _log_startup(f"win webview {url} from {_win_install_dir()}")
            run_desktop(url, title="VideosCreator", icon=_app_icon(), ready_event=ready_event)
            return 0
        except Exception as e:
            _log_startup(f"win webview fail, fallback to browser: {e}")
            webbrowser.open(url)
            _win_alert(
                f"Thông báo: {e}\n\n"
                "Ứng dụng đã tự động mở trên trình duyệt web mặc định để bạn sử dụng ngay."
            )
            try:
                while t.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                return 0
            return 0

    if force_browser or sys.platform == "win32":
        _log_startup(f"mo trinh duyet truc tiep {url}")
        webbrowser.open(url)
        try:
            while t.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 0
        return 0

    icon = _app_icon()
    try:
        win = webview.create_window(
            "VideosCreator",
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
            maximized=True,
        )
    except TypeError:
        win = webview.create_window(
            "VideosCreator",
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
        )
    if icon and win is not None:
        win.events.shown += lambda: _mac_title_icon(win, icon)
    _start_webview(icon)
    return 0


def _win_install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


def _win_unblock_install() -> None:
    """Bo Mark-of-the-Web (Downloads) — unblock exe, dll cap goc va trong _internal."""
    here = _win_install_dir()
    for root_dir in (here, here / "_internal"):
        if not root_dir.is_dir():
            continue
        for ext in ("*.exe", "*.dll", "*.pyd"):
            for p in root_dir.glob(ext):
                try:
                    os.remove(f"{p}:Zone.Identifier")
                except OSError:
                    pass


def _start_webview(icon: str | None) -> None:
    import webview

    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["gui"] = "edgechromium"
    if icon:
        kwargs["icon"] = icon
    try:
        webview.start(**kwargs)
        return
    except TypeError:
        kwargs.pop("icon", None)
        try:
            webview.start(**kwargs)
            return
        except TypeError:
            pass
    except Exception as e:
        raise RuntimeError(
            "Khong mo duoc cua so app. Cai Microsoft Edge WebView2 "
            "(Evergreen Runtime) roi chay lai.\n"
            f"({e})"
        ) from e
    webview.start()


def _app_icon() -> str | None:
    png = ROOT / "dyflow" / "web" / "static" / "logo-512.png"
    ico = ROOT / "dyflow" / "web" / "static" / "favicon.ico"
    try:
        from dyflow.desktop_mode import resource_root

        bundled = resource_root()
        p2 = bundled / "dyflow" / "web" / "static" / "logo-512.png"
        i2 = bundled / "dyflow" / "web" / "static" / "favicon.ico"
        if p2.is_file():
            png = p2
        if i2.is_file():
            ico = i2
    except Exception:
        pass
    if sys.platform == "win32" and ico.is_file():
        return str(ico)
    if png.is_file():
        return str(png)
    if ico.is_file():
        return str(ico)
    return None


def _mac_title_icon(window, icon_path: str) -> None:
    """Logo nho tren thanh title macOS (canh chu VideosCreator)."""
    if sys.platform != "darwin" or not icon_path:
        return
    try:
        from AppKit import NSImage
        from Foundation import NSURL

        img = NSImage.alloc().initWithContentsOfFile_(icon_path)
        native = getattr(window, "native", None)
        if img is None or native is None:
            return
        native.setRepresentedURL_(NSURL.fileURLWithPath_(icon_path))
        btn = native.standardWindowButton_(4)
        if btn is not None:
            btn.setImage_(img)
            btn.setHidden_(False)
    except Exception:
        pass


def _win_alert(msg: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg[:1500], "VideosCreator", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    import multiprocessing

    # freeze_support PHAI chay som tren Windows frozen.
    multiprocessing.freeze_support()

    # Playwright co the spawn VideosCreator.exe nhu Chromium — thoat ngay, khong UI.
    # Khong import dyflow o day (tranh crash truoc khi mo cua so).
    if getattr(sys, "frozen", False) and len(sys.argv) > 1:
        _args = sys.argv[1:]
        _join = " ".join(_args).lower()
        _is_chrome = (
            any(a.startswith("--type=") for a in _args)
            or any(a.startswith("--headless") for a in _args)
            or "--remote-debugging" in _join
            or ("--user-data-dir" in _join and "--no-sandbox" in _join)
            or ("--disable-blink-features" in _join and "--no-sandbox" in _join)
        )
        if _is_chrome:
            raise SystemExit(0)
        argv1 = _args[0]
        if argv1 == "--worker":
            raise SystemExit(_run_worker_only())
        if argv1 == "--ytdlp":
            from dyflow.utils import ensure_media_bins, ytdlp_ffmpeg_args

            ensure_media_bins()
            rest = list(sys.argv[2:])
            if "--ffmpeg-location" not in rest:
                rest = [*ytdlp_ffmpeg_args(), *rest]
            from yt_dlp import main as ytdlp_main

            sys.argv = ["yt-dlp", *rest]
            raise SystemExit(ytdlp_main())
        if argv1 == "--demucs":
            from demucs.separate import main as demucs_main

            sys.argv = ["demucs", *sys.argv[2:]]
            raise SystemExit(demucs_main())
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as e:
        import traceback

        tb = traceback.format_exc()
        _log_startup(f"fatal crash:\n{tb}")
        _write_crash_log(tb)
        _win_alert(
            "ĐÃ XẢY RA LỖI KHI KHỞI ĐỘNG VIDEOSCREATOR:\n\n"
            f"{e}\n\n"
            "Chi tiết lỗi đã được ghi vào:\n"
            "1. crash.log (cùng thư mục với VideosCreator.exe)\n"
            f"2. {_startup_log()}\n\n"
            "Vui lòng chụp lại màn hình này hoặc gửi file crash.log để được hỗ trợ."
        )
        raise


