"""Profile noi dung: health review, casual story, ..."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from .utils import log


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


_ROOT = _bundle_root()
_PROFILES_DIR = _ROOT / "profiles"


def profiles_dir() -> Path:
    return _PROFILES_DIR


def list_profiles() -> list[str]:
    if not _PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in _PROFILES_DIR.glob("*.yaml"))


def load_profile(name: str) -> dict[str, Any]:
    """Load profiles/<name>.yaml. Raise FileNotFoundError neu thieu."""
    name = (name or "").strip().removesuffix(".yaml")
    path = _PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        known = ", ".join(list_profiles()) or "(none)"
        raise FileNotFoundError(f"Khong thay profile '{name}'. Co: {known}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Profile khong hop le: {path}")
    data["_path"] = str(path)
    data.setdefault("name", name)
    return data


def apply_profile(cfg: dict, profile: dict) -> dict:
    """Merge profile vao config (profile ghi de voiceover.auto / caption...)."""
    from .utils import deep_merge

    out = dict(cfg)
    for key in ("voiceover", "caption", "transform", "export", "music"):
        if key in profile and isinstance(profile[key], dict):
            out[key] = deep_merge(out.get(key, {}), profile[key])
    out["_profile"] = {
        "name": profile.get("name"),
        "label": profile.get("label"),
        "content_domain": (profile.get("voiceover") or {}).get("auto", {}).get("content_domain"),
        "glossary": profile.get("glossary") or {},
    }
    return out


def detect_profile(
    text_sample: str,
    *,
    default: str = "casual_story",
) -> str:
    """Doan profile tu mau text (zh/vi). Tra ve ten profile."""
    sample = (text_sample or "").lower()
    if not sample.strip():
        return default

    best_name = default
    best_score = 0
    for name in list_profiles():
        try:
            prof = load_profile(name)
        except Exception:  # noqa: BLE001
            continue
        keys = prof.get("detect_keywords") or {}
        score = 0
        for lang_keys in keys.values():
            if not isinstance(lang_keys, list):
                continue
            for kw in lang_keys:
                if kw and str(kw).lower() in sample:
                    score += 1
        if score > best_score:
            best_score = score
            best_name = name

    if best_score == 0:
        log.info("Profile auto: khong khop keyword -> %s", default)
    else:
        log.info("Profile auto: '%s' (score=%d)", best_name, best_score)
    return best_name


def glossary_note(profile: dict | None) -> str:
    if not profile:
        return ""
    g = profile.get("glossary") or {}
    return str(g.get("zh_to_note") or "").strip()
