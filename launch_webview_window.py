import sys
import os
import traceback
from pathlib import Path

log_file = Path(r"C:\Github\dyflow\launch_webview.log")
with log_file.open("a", encoding="utf-8") as f:
    f.write(f"--- launch_webview started at {sys.argv} ---\n")

try:
    ROOT = Path(__file__).resolve().parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from dyflow.win_webview import run_desktop

    url = "http://127.0.0.1:8765/"
    icon = str(ROOT / "dyflow" / "web" / "static" / "favicon.ico")
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"Calling run_desktop with url={url}, icon={icon}\n")
    run_desktop(url, title="VideosCreator", icon=icon)
    with log_file.open("a", encoding="utf-8") as f:
        f.write("run_desktop returned normally.\n")
except Exception as e:
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"Exception: {e}\n{traceback.format_exc()}\n")
