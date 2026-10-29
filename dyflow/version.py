"""Thong tin phien ban va quan ly cap nhat VideosCreator."""
from __future__ import annotations

__version__ = "1.1.2"
__build__ = "2026.10.29"


def get_version_info() -> dict:
    from .desktop_mode import data_dir, resource_root

    active_patch = False
    patch_ver = __version__
    try:
        p_dir = data_dir() / "patch"
        if p_dir.is_dir():
            for cand in (p_dir / "version.json", data_dir() / "patch_version.json"):
                if cand.is_file():
                    import json

                    data = json.loads(cand.read_text(encoding="utf-8-sig"))
                    cand_ver = data.get("version")
                    if cand_ver:
                        patch_ver = str(cand_ver).strip()
                        active_patch = True
                        break
        # Neu chua co patch hoac van la base version, kiem tra version.json o root
        if patch_ver == __version__:
            root_v = resource_root() / "version.json"
            if root_v.is_file():
                import json

                data = json.loads(root_v.read_text(encoding="utf-8-sig"))
                cand_ver = data.get("version")
                if cand_ver:
                    patch_ver = str(cand_ver).strip()
    except Exception:
        pass

    return {
        "version": patch_ver,
        "base_version": __version__,
        "build": __build__,
        "active_patch": active_patch,
        "name": "VideosCreator",
    }

