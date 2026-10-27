"""Module kiem tra va tu dong cap nhat ban va (patch) cho VideosCreator."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .desktop_mode import data_dir
from .utils import log
from .version import __version__, get_version_info

DEFAULT_CHECK_URL = (
    "https://raw.githubusercontent.com/quangtuan14102001/dyflow/main/version.json"
)


def _parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in nums) if nums else (0,)


def check_update(url: str = "") -> dict:
    target_url = url.strip() or DEFAULT_CHECK_URL
    current_info = get_version_info()
    curr_ver = current_info["version"]

    req = urllib.request.Request(
        target_url,
        headers={"User-Agent": f"VideosCreator/{curr_ver} (Windows AutoUpdater)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            if resp.status != 200:
                return {
                    "ok": False,
                    "error": f"Server phản hồi mã lỗi {resp.status}",
                    "current_version": curr_ver,
                }
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except Exception as e:
        log.warning("Kiem tra update that bai: %s", e)
        return {
            "ok": False,
            "error": f"Không thể kết nối máy chủ cập nhật ({e})",
            "current_version": curr_ver,
        }

    latest_ver = str(data.get("version") or "").strip()
    has_update = _parse_version(latest_ver) > _parse_version(curr_ver)

    return {
        "ok": True,
        "current_version": curr_ver,
        "latest_version": latest_ver,
        "has_update": has_update,
        "release_date": data.get("release_date") or "",
        "changelog": data.get("changelog") or "Bản vá sửa lỗi và tối ưu hiệu năng.",
        "patch_url": data.get("patch_url") or "",
        "installer_url": data.get("installer_url") or "",
    }


def apply_patch_file(zip_path: Path) -> dict:
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        return {"ok": False, "error": "File zip bản vá không tồn tại."}

    dest_dir = data_dir() / "patch"
    tmp_dir = data_dir() / "patch_tmp"

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Bao ve khong bi path traversal (Zip Slip vulnerability)
            for member in zf.infolist():
                norm = Path(member.filename).resolve()
                if ".." in member.filename or member.filename.startswith(("/", "\\")):
                    continue
                zf.extract(member, tmp_dir)

        # Kiem tra thu muc patch co chua dyflow/
        if not (tmp_dir / "dyflow").is_dir() and not (tmp_dir / "version.json").is_file():
            # Tim thu muc con neu file zip duoc boc boi 1 folder cha
            found = False
            for sub in tmp_dir.iterdir():
                if sub.is_dir() and (sub / "dyflow").is_dir():
                    # Move noi dung ra ngoai
                    for item in sub.iterdir():
                        shutil.move(str(item), str(tmp_dir / item.name))
                    found = True
                    break
            if not found and not (tmp_dir / "dyflow").is_dir():
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return {
                    "ok": False,
                    "error": "File zip không hợp lệ (không tìm thấy thư mục mã nguồn dyflow).",
                }

        # Ghi patch_version.json neu co file version.json
        v_file = tmp_dir / "version.json"
        if v_file.is_file():
            try:
                v_data = json.loads(v_file.read_text(encoding="utf-8"))
                (data_dir() / "patch_version.json").write_text(
                    json.dumps(v_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

        # Di chuyen de vao dest_dir
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        shutil.move(str(tmp_dir), str(dest_dir))

        return {
            "ok": True,
            "message": "Áp dụng bản vá thành công! Vui lòng khởi động lại app để nạp bản mới.",
            "restart_required": True,
        }
    except Exception as e:
        log.exception("Loi giai nen patch: %s", e)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"ok": False, "error": f"Lỗi áp dụng bản vá: {e}"}


def apply_patch_url(patch_url: str) -> dict:
    url = (patch_url or "").strip()
    if not url:
        return {"ok": False, "error": "Thiếu đường dẫn tải bản vá."}

    tmp_zip = None
    try:
        fd, tmp_zip = tempfile.mkstemp(prefix="vc_patch_", suffix=".zip")
        os.close(fd)

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "VideosCreator/AutoUpdater"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_zip, "wb") as f:
            shutil.copyfileobj(resp, f)

        return apply_patch_file(Path(tmp_zip))
    except Exception as e:
        log.exception("Loi tai patch: %s", e)
        return {"ok": False, "error": f"Không thể tải bản vá: {e}"}
    finally:
        if tmp_zip and Path(tmp_zip).is_file():
            try:
                os.unlink(tmp_zip)
            except OSError:
                pass


def rollback_patch() -> dict:
    dest_dir = data_dir() / "patch"
    v_file = data_dir() / "patch_version.json"
    removed = False
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
        removed = True
    if v_file.exists():
        try:
            os.unlink(v_file)
        except OSError:
            pass
        removed = True
    return {
        "ok": True,
        "message": "Đã khôi phục về phiên bản gốc ban đầu. Vui lòng khởi động lại app." if removed else "Đang ở phiên bản gốc ban đầu.",
        "restart_required": removed,
    }


def restart_application() -> None:
    """Khoi dong lai tien trinh VideosCreator trong 1 giay."""
    exe = sys.executable
    args = [exe]
    if not getattr(sys, "frozen", False):
        # Chay source: python desktop.py
        desktop_script = Path(__file__).resolve().parents[1] / "desktop.py"
        args = [exe, str(desktop_script)]

    # Chay process doc lap tach khoi tien trinh hien tai
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    try:
        subprocess.Popen(args, creationflags=creationflags, close_fds=True)
    except Exception:
        subprocess.Popen(args)

    # Thoat tien trinh cu
    os._exit(0)
