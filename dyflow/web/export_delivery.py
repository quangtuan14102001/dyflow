"""Copy MP4 ra Save folder user ngay khi job xong (truoc khi retention xoa)."""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path

from ..utils import log, verify_output_mp4

# Giu MP4 noi bo it nhat bao lau (dong bo storage.OUTPUT_GRACE_SEC).
OUTPUT_GRACE_SEC = 48 * 3600
KEEP_RECENT_OUTPUTS = 5


def user_downloads() -> Path:
    home = Path.home()
    for name in ("Downloads", "Tải xuống"):
        p = home / name
        if p.is_dir():
            return p
    p = home / "Downloads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def export_prefs_path() -> Path:
    from ..desktop_mode import data_dir

    return data_dir() / "export_prefs.json"


def load_export_dir() -> Path:
    try:
        p = export_prefs_path()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            d = Path(str((data or {}).get("dir") or "").strip())
            if d.is_dir():
                return d
    except Exception:  # noqa: BLE001
        pass
    return user_downloads()


def merge_export_prefs_path() -> Path:
    from ..desktop_mode import data_dir

    return data_dir() / "merge_export_prefs.json"


def load_merge_export_dir() -> Path:
    try:
        p = merge_export_prefs_path()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            d = Path(str((data or {}).get("dir") or "").strip())
            if d.is_dir():
                return d
    except Exception:  # noqa: BLE001
        pass
    def_dir = user_downloads() / "Merged_Videos"
    def_dir.mkdir(parents=True, exist_ok=True)
    return def_dir


def save_merge_export_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        pref = merge_export_prefs_path()
        pref.write_text(
            json.dumps({"dir": str(path)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("save merge export prefs: %s", e)
    return path


def _ascii_slug(text: str) -> str:
    """Bo dau Viet + ky tu la → ten file VLC/Explorer mo on dinh."""
    t = (text or "").strip()
    if not t:
        return ""
    t = t.translate(str.maketrans("đĐ", "dD"))
    try:
        t = unicodedata.normalize("NFKD", t)
    except Exception:  # noqa: BLE001
        pass
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return t[:60]


_LOCAL_UPLOAD_RE = re.compile(r"^local_[0-9a-f]{8}_(.+)$", re.I)
# 第1集 / 第01集 / 第001集 / 第16集 …
_ZH_EP_RE = re.compile(
    r"^第\s*0*(?P<n>\d{1,4})\s*集(?:[_\-\s].*)?$",
)
# EP01 / Ep 16 / E16 / Tập 16 / Tap16
_ALT_EP_RE = re.compile(
    r"^(?:ep|e|tap|tập|tap_|ep_)\s*0*(?P<n>\d{1,4})\b",
    re.I,
)


def localize_source_title(stem: str) -> str:
    """Ten file goc → ten xuat Viet (vd 第016集 → Tap_016). Khong de chu Han."""
    s = (stem or "").strip()
    if not s:
        return ""
    m = _ZH_EP_RE.match(s)
    if m:
        return f"Tap_{int(m.group('n')):03d}"
    m = _ALT_EP_RE.match(s)
    if m:
        return f"Tap_{int(m.group('n')):03d}"
    # Con chu Han / CJK → uu tien so tap nam trong ten, khong thi ASCII slug
    if any("\u4e00" <= ch <= "\u9fff" for ch in s):
        nums = re.findall(r"(\d{1,4})", s)
        if nums:
            # lay so dai nhat / cuoi (thuong la so tap)
            n = max(nums, key=lambda x: (len(x), int(x)))
            return f"Tap_{int(n):03d}"
        slug = _ascii_slug(s)
        return slug or s
    return s


def title_from_source_path(source_url: str) -> str:
    """Lay ten file goc tu path upload, doi sang ten Viet (Tap_016…)."""
    raw = (source_url or "").strip()
    if not raw:
        return ""
    # Chi ap dung file local / path may — khong dung URL Douyin
    if "://" in raw and not raw.lower().startswith("file:"):
        return ""
    stem = Path(raw).stem
    m = _LOCAL_UPLOAD_RE.match(stem)
    if m:
        return localize_source_title(m.group(1).strip())
    # Path local thuong (khong qua uploads/)
    if Path(raw).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
        return localize_source_title(stem.strip())
    return ""


def safe_export_name(title: str, job_id: str) -> str:
    """Ten file an toan Windows. Uu tien Tap_NNN / ASCII — tranh chu Han."""
    jid = (job_id or "job").replace("-", "")[:12]
    fallback = f"videoscreator_{jid}"
    raw = localize_source_title((title or "").strip()) or (title or "").strip()
    try:
        raw = unicodedata.normalize("NFC", raw)
    except Exception:  # noqa: BLE001
        pass
    raw = "".join(
        ch for ch in raw
        if ch != "\ufffd" and not (0xD800 <= ord(ch) <= 0xDFFF)
    )
    # Neu van con CJK → slug ASCII
    if any("\u4e00" <= ch <= "\u9fff" for ch in raw):
        slug = _ascii_slug(raw)
        if len(slug) >= 3:
            return f"{slug}.mp4"
        return f"{fallback}.mp4"
    cleaned = re.sub(r'[<>:"/\\|?*#%&{};+=\x00-\x1f]', " ", raw)
    cleaned = re.sub(r"[\u200b-\u200f\ufeff]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if cleaned:
        name = cleaned[:80].rstrip(" .")
        if name:
            return f"{name}.mp4"
    slug = _ascii_slug(raw)
    if len(slug) >= 4:
        return f"{slug}.mp4"
    return f"{fallback}.mp4"


def copy_to_save_folder(
    src: Path,
    *,
    job_id: str,
    title: str = "",
    dest_dir: Path | None = None,
) -> Path:
    """Copy MP4 sang Save folder. Raise neu that bai / file dich rong."""
    src = verify_output_mp4(src, label="deliver src")
    dest_dir = Path(dest_dir) if dest_dir else load_export_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = safe_export_name(title, job_id)
    dest = dest_dir / name
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        log.info("Deliver skip (da co): %s", dest)
        return dest.resolve()
    stem = dest.stem
    n = 2
    while dest.exists():
        dest = dest_dir / f"{stem}_{n}.mp4"
        n += 1
    try:
        shutil.copy2(src, dest)
    except OSError as e:
        raise RuntimeError(
            f"Khong copy duoc MP4 sang Save folder ({dest_dir}): {e}. "
            f"Kiem tra o dia / quyen ghi."
        ) from e
    verify_output_mp4(dest, label="deliver dest")
    log.info("Da luu MP4 vao Save folder: %s (%d byte)", dest, dest.stat().st_size)
    return dest.resolve()
