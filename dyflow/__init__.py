"""dyflow - Douyin -> edit -> long tieng Viet -> export pipeline."""
__version__ = "0.1.0"

# Homebrew Python tren may nay tung bi dan shell vao thread.py — patch som.
try:
    from .stdlib_fix import ensure_thread_stdlib
    ensure_thread_stdlib()
except Exception:  # noqa: BLE001
    pass

try:
    import sys
    import os
    from pathlib import Path
    if sys.platform == "win32":
        _appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        _patch_dir = Path(_appdata) / "VideosCreator" / "patch"
    else:
        _patch_dir = Path.home() / ".config" / "videoscreator" / "patch"
    if _patch_dir.is_dir() and not any(type(f).__name__ == "_PatchMetaFinder" for f in sys.meta_path):
        class _PatchMetaFinder:
            def __init__(self, root: Path):
                self.root = root

            def find_spec(self, fullname: str, path=None, target=None):
                parts = fullname.split(".")
                if parts[0] == "dyflow":
                    rel = Path(*parts)
                    py_f = self.root / f"{rel}.py"
                    init_f = self.root / rel / "__init__.py"
                    target_f = py_f if py_f.is_file() else (init_f if init_f.is_file() else None)
                    if target_f:
                        import importlib.util
                        return importlib.util.spec_from_file_location(
                            fullname,
                            target_f,
                            submodule_search_locations=[str(target_f.parent)] if target_f.name == "__init__.py" else None,
                        )
                return None
        sys.meta_path.insert(0, _PatchMetaFinder(_patch_dir))
except Exception:
    pass

