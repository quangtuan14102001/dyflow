"""Thong tin phien ban va quan ly cap nhat VideosCreator."""
from __future__ import annotations

__version__ = "1.0.2"
__build__ = "2026.10.27"


def get_version_info() -> dict:
    from .desktop_mode import data_dir

    active_patch = False
    patch_ver = __version__
    try:
        p_dir = data_dir() / "patch"
        if p_dir.is_dir():
            active_patch = True
            for cand in (p_dir / "version.json", data_dir() / "patch_version.json"):
                if cand.is_file():
                    import json

                    data = json.loads(cand.read_text(encoding="utf-8"))
                    patch_ver = data.get("version") or patch_ver
                    break
    except Exception:
        pass

    return {
        "version": patch_ver,
        "base_version": __version__,
        "build": __build__,
        "active_patch": active_patch,
        "name": "VideosCreator",
    }
