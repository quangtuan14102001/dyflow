"""Regression: save folder + export-local must work (no silent no-op)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DYFLOW_DESKTOP"] = "1"

from dyflow.desktop_mode import prepare

prepare()

from fastapi.testclient import TestClient

from dyflow.web import models
from dyflow.web import app as webapp


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise SystemExit(f"FAIL {name}: {detail}")
    print(f"OK {name}" + (f" — {detail}" if detail else ""))


def test_win_picker_skips_tkinter() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        class R:
            returncode = 0
            stdout = str(Path.home() / "Downloads") + "\n"
            stderr = ""
        # Ensure Downloads exists
        Path(Path.home() / "Downloads").mkdir(parents=True, exist_ok=True)
        return R()

    with mock.patch("dyflow.web.app.subprocess.run", side_effect=fake_run):
        with mock.patch.dict("sys.modules", {"tkinter": None}):
            # If code imports tkinter on win32, this still ok if not imported.
            chosen = webapp._pick_folder_dialog(str(Path.home() / "Downloads"))
    _ok("picker_uses_powershell", bool(calls), str(calls[:1]))
    _ok("picker_sta", any("-STA" in c for c in calls[0]), str(calls[0]))
    _ok("picker_returns_dir", chosen is not None and Path(chosen).is_dir(), str(chosen))


def test_export_local_and_meta() -> None:
    c = TestClient(webapp.app)
    r = c.get("/api/desktop/save-prefs")
    _ok("save_prefs", r.status_code == 200 and r.json().get("dir"), r.text)

    with models._connect() as con:
        row = con.execute(
            "SELECT id, output_path FROM jobs "
            "WHERE status='completed' AND IFNULL(output_path,'')!='' "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    _ok("has_completed_job", row is not None, "need at least one completed job")
    job_id = row["id"]
    src = Path(row["output_path"])
    _ok("output_exists", src.is_file() and src.stat().st_size > 0, str(src))

    meta = c.get(f"/api/jobs/{job_id}/export-meta")
    _ok("export_meta", meta.status_code == 200 and meta.json().get("ready"), meta.text)
    _ok("export_meta_name", bool(meta.json().get("filename")), meta.text)

    dest = Path(tempfile.mkdtemp(prefix="vc_export_test_"))
    try:
        r2 = c.post(f"/api/jobs/{job_id}/export-local", json={"dir": str(dest)})
        data = r2.json()
        _ok("export_local_http", r2.status_code == 200 and data.get("ok"), r2.text)
        out = Path(data["path"])
        _ok("export_file", out.is_file() and out.stat().st_size == src.stat().st_size, str(out))
        _ok("export_in_dest", out.parent.resolve() == dest.resolve(), str(out.parent))
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_js_has_save_picker() -> None:
    js = (ROOT / "dyflow" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    _ok("js_showSaveFilePicker", "showSaveFilePicker" in js)
    _ok("js_pickSaveFolder", "_pickSaveFolder" in js)
    _ok("js_bindSaveActions", "_bindSaveActions" in js)
    _ok("js_export_meta", "export-meta" in js)
    html = (ROOT / "dyflow" / "web" / "templates" / "job.html").read_text(encoding="utf-8")
    _ok("input_editable", "readonly" not in html.split("save-path-input")[1][:200])


def test_picker_source_no_tk_on_win() -> None:
    src = (ROOT / "dyflow" / "web" / "app.py").read_text(encoding="utf-8")
    # Windows branch must return powershell before tkinter.
    i_win = src.find("if sys.platform == \"win32\":")
    i_ps = src.find("return _pick_folder_win_powershell")
    i_tk = src.find("import tkinter as tk")
    _ok("win_before_tk", i_win != -1 and i_ps != -1 and i_ps < i_tk)


if __name__ == "__main__":
    if sys.platform == "win32":
        test_win_picker_skips_tkinter()
        test_picker_source_no_tk_on_win()
    test_js_has_save_picker()
    test_export_local_and_meta()
    print("ALL_SAVE_TESTS_OK")
