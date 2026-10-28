"""Genre profile prompts — van phong dich theo the loai (topic_context)."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("dyflow.genre")

# Dong user hay dan nham vao o van phong — bo bot truoc khi dua vao prompt.
_FORMAT_LEAK_RE = re.compile(
    r"(?i)^\s*(?:"
    r"xuất\s*ra\s*srt|xuat\s*ra\s*srt|giữ\s*nguyên\s*timecode|giu\s*nguyen\s*timecode|"
    r"return\s*json|tra\s*json|output\s*json|định\s*dạng\s*json|dinh\s*dang\s*json|"
    r"mỗi\s*câu\s*một\s*dòng\s*srt|moi\s*cau\s*mot\s*dong\s*srt|"
    r"timecode|hh:mm:ss|whisper\s*format"
    r").*$",
    re.M,
)


DEFAULT_GENRES = [
    {"id": "tien_hiep", "label": "Tiên Hiệp / Tu Tiên", "file": "tien_hiep.txt"},
    {"id": "tan_the", "label": "Tận thế / Sinh tồn", "file": "tan_the.txt"},
    {"id": "xuyen_khong", "label": "Xuyên không / Trọng sinh", "file": "xuyen_khong.txt"},
    {"id": "kiem_hiep", "label": "Kiếm hiệp / Võ hiệp", "file": "kiem_hiep.txt"},
    {"id": "do_thi", "label": "Đô thị / Hiện đại", "file": "do_thi.txt"},
    {"id": "ngon_tinh", "label": "Ngôn tình / Lãng mạn", "file": "ngon_tinh.txt"},
    {"id": "cung_dau", "label": "Cung đấu / Trạch đấu", "file": "cung_dau.txt"},
    {"id": "quan_su", "label": "Quân sự / Kháng chiến", "file": "quan_su.txt"},
    {"id": "than_thoai", "label": "Thần thoại / Phong thần", "file": "than_thoai.txt"},
]


def _profiles_root() -> Path:
    """Dev: repo/profiles/genre. Frozen: _MEIPASS/profiles/genre. Patch: AppData/profiles/genre."""
    import os
    import sys

    cands: list[Path] = []
    # 1. Frozen / PyInstaller
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(Path(meipass) / "profiles" / "genre")
        cands.append(Path(meipass) / "_internal" / "profiles" / "genre")
    exe_dir = Path(sys.executable).resolve().parent
    cands.append(exe_dir / "profiles" / "genre")
    cands.append(exe_dir / "_internal" / "profiles" / "genre")

    # 2. Thu muc file goc & repo dev
    cands.append(Path(__file__).resolve().parents[1] / "profiles" / "genre")
    cands.append(Path.cwd() / "profiles" / "genre")
    cands.append(Path(r"C:\Github\dyflow\profiles\genre"))

    # 3. Thu muc AppData
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        la = Path(local_app)
        cands.append(la / "VideosCreator" / "patch" / "profiles" / "genre")
        cands.append(la / "VideosCreator" / "profiles" / "genre")
        cands.append(la / "Programs" / "VideosCreator" / "profiles" / "genre")
        cands.append(la / "Programs" / "VideosCreator" / "_internal" / "profiles" / "genre")

    for cand in cands:
        if cand.is_dir():
            return cand

    return Path(__file__).resolve().parents[1] / "profiles" / "genre"


def list_genres() -> list[dict]:
    """[{id, label, file, chars}] — doc manifest neu co, else quet *.txt."""
    root = _profiles_root()
    manifest = root / "manifest.json"
    items: list[dict] = []
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            raw = data.get("genres") if isinstance(data, dict) else data
            if isinstance(raw, list):
                for row in raw:
                    if not isinstance(row, dict):
                        continue
                    gid = str(row.get("id") or "").strip()
                    if not gid:
                        continue
                    fname = str(row.get("file") or f"{gid}.txt").strip()
                    path = root / fname
                    text = ""
                    if path.is_file():
                        text = path.read_text(encoding="utf-8")
                    items.append({
                        "id": gid,
                        "label": str(row.get("label") or gid).strip(),
                        "file": fname,
                        "chars": len(text),
                        "ready": bool(text.strip()) and "(điền sau)" not in text[:200],
                    })
        except Exception:  # noqa: BLE001
            log.exception("Doc genre manifest loi")
    if items:
        return items
    # Fallback: moi file .txt
    if root.is_dir():
        for path in sorted(root.glob("*.txt")):
            text = path.read_text(encoding="utf-8")
            gid = path.stem
            items.append({
                "id": gid,
                "label": gid.replace("_", " ").title(),
                "file": path.name,
                "chars": len(text),
                "ready": bool(text.strip()),
            })
    if items:
        return items
    # Fallback tuyet doi: danh sach the loai mac dinh, khong bao gio de dropdown trong
    return [
        {
            "id": g["id"],
            "label": g["label"],
            "file": g["file"],
            "chars": 0,
            "ready": True,
        }
        for g in DEFAULT_GENRES
    ]


def load_genre_template(genre_id: str) -> str:
    """Noi dung mau goc tu file profile."""
    gid = str(genre_id or "").strip()
    if not gid or gid in {"", "none", "auto", "default"}:
        return ""
    root = _profiles_root()
    for g in list_genres():
        if g["id"] == gid:
            path = root / g["file"]
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
            break
    # direct file
    path = root / f"{gid}.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def sanitize_genre_prompt(text: str) -> str:
    """Bo bot dong dinh dang user go nham; giu van phong."""
    t = (text or "").strip()
    if not t:
        return ""
    cleaned = _FORMAT_LEAK_RE.sub("", t)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def resolve_genre_prompt(
    genre_id: str | None = None,
    genre_prompt: str | None = None,
) -> tuple[str, str]:
    """Tra (genre_id, prompt_sanitized). Prompt user uu tien; khong co thi load mau."""
    gid = str(genre_id or "").strip()
    if gid in {"none", "auto", "default"}:
        gid = ""
    custom = (genre_prompt or "").strip()
    if custom:
        return gid, sanitize_genre_prompt(custom)
    if gid:
        return gid, sanitize_genre_prompt(load_genre_template(gid))
    return "", ""


def save_genre_lock(
    work_dir: Path | str,
    *,
    genre_id: str,
    genre_prompt: str,
) -> Path:
    wp = Path(work_dir)
    wp.mkdir(parents=True, exist_ok=True)
    out = wp / "genre_lock.json"
    payload = {
        "genre_id": genre_id or "",
        "genre_prompt": genre_prompt or "",
        "chars": len(genre_prompt or ""),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_genre_lock(work_dir: Path | str | None) -> tuple[str, str]:
    if not work_dir:
        return "", ""
    path = Path(work_dir) / "genre_lock.json"
    if not path.is_file():
        return "", ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return "", ""
        return (
            str(data.get("genre_id") or "").strip(),
            str(data.get("genre_prompt") or "").strip(),
        )
    except Exception:  # noqa: BLE001
        return "", ""
