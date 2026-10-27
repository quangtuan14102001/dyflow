"""Quan ly tien trinh ghep video chay ngam (Background Merge Task Manager).

Dac diem:
- Chay ghep video doc lap trong background thread.
- Nguoi dung co the tu do chuyen sang cac tab khac (Tao video, Hang cho, Thu vien...)
  ma khong lam gian doan tien trinh ghep.
- Ho tro cap nhat tien do thoi gian thuc va tiep tuc hien thi khi quay lai /merge.
- Ho tro huy tien trinh (cancel) va don dep file tam.
- Dat process priority BELOW_NORMAL de khong lam lag may hay chen ep tien trinh tao video.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from ..utils import ensure_media_bins, log
from ..video_merger import merge_videos


class MergeManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._active_task_id: str | None = None
        self._cancel_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def is_busy(self) -> bool:
        with self._lock:
            if not self._active_task_id:
                return False
            task = self._tasks.get(self._active_task_id)
            if not task:
                return False
            return task.get("status") in ("queued", "running")

    def get_status(self, task_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            tid = task_id or self._active_task_id
            if not tid or tid not in self._tasks:
                return {"ok": True, "has_task": False}
            task = dict(self._tasks[tid])

        now = time.time()
        created_at = task.get("created_at", now)
        task["elapsed_seconds"] = max(0, int(now - created_at))
        task["has_task"] = True
        task["ok"] = True
        return task

    def start_task(
        self,
        task_id: str,
        saved_inputs: list[Path],
        out_path: Path,
        out_folder: Path,
        work_dir: Path,
        aspect_ratio: str = "first",
        fit: str = "contain",
        fast_copy: bool = True,
        total_videos: int = 0,
        video_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.is_busy():
                raise RuntimeError("Hiện đang có một video đang được ghép. Vui lòng chờ hoàn thành hoặc bấm Hủy.")

            if video_items:
                items_info = list(video_items)
            else:
                items_info = [
                    {"name": p.name, "size": p.stat().st_size if p.is_file() else 0}
                    for p in saved_inputs
                ]

            self._cancel_event.clear()
            self._active_task_id = task_id
            self._tasks[task_id] = {
                "id": task_id,
                "status": "running",
                "progress": 5.0,
                "message": f"Chuẩn bị ghép {total_videos or len(saved_inputs)} video...",
                "total_videos": total_videos or len(saved_inputs),
                "video_items": items_info,
                "aspect_ratio": aspect_ratio,
                "fit": fit,
                "fast_copy": fast_copy,
                "out_folder": str(out_folder),
                "out_path": str(out_path),
                "work_dir": str(work_dir),
                "result": None,
                "error": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            }

        thread = threading.Thread(
            target=self._run_worker,
            args=(task_id, saved_inputs, out_path, out_folder, work_dir, aspect_ratio, fit, fast_copy, total_videos),
            daemon=True,
            name=f"MergeWorker-{task_id}",
        )
        self._worker_thread = thread
        thread.start()
        return self.get_status(task_id)

    def _update_task(self, task_id: str, **kwargs: Any) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.update(kwargs)
                task["updated_at"] = time.time()

    def cancel_active(self) -> bool:
        with self._lock:
            if not self._active_task_id:
                return False
            task = self._tasks.get(self._active_task_id)
            if not task or task.get("status") not in ("queued", "running"):
                return False
            self._cancel_event.set()
            task["status"] = "cancelled"
            task["message"] = "Đã hủy ghép video."
            task["updated_at"] = time.time()
            log.info("[MergeManager] Da yeu cau huy task_id=%s", self._active_task_id)
            return True

    def clear_task(self, task_id: str | None = None) -> bool:
        with self._lock:
            tid = task_id or self._active_task_id
            if not tid:
                return False
            task = self._tasks.get(tid)
            if task and task.get("status") in ("queued", "running"):
                self._cancel_event.set()
            if tid == self._active_task_id:
                self._active_task_id = None
            self._tasks.pop(tid, None)
            return True

    def _run_worker(
        self,
        task_id: str,
        saved_inputs: list[Path],
        out_path: Path,
        out_folder: Path,
        work_dir: Path,
        aspect_ratio: str,
        fit: str,
        fast_copy: bool,
        total_videos: int,
    ) -> None:
        log.info("[MergeManager] Bat dau ghep video chay ngam: task_id=%s (%d video)", task_id, total_videos)

        def on_progress(pct: float, msg: str) -> None:
            self._update_task(task_id, progress=round(pct, 1), message=msg)

        def check_cancelled() -> bool:
            return self._cancel_event.is_set()

        try:
            ensure_media_bins()
            merged = merge_videos(
                saved_inputs,
                out_path,
                aspect_ratio=aspect_ratio,
                fit=fit,
                fast_copy=fast_copy,
                progress_callback=on_progress,
                cancel_check=check_cancelled,
            )

            if check_cancelled():
                self._update_task(task_id, status="cancelled", message="Đã hủy ghép video.")
                return

            out_name = out_path.name
            folder_param = f"?folder={urllib.parse.quote_plus(str(out_folder))}"
            file_size = merged.stat().st_size if merged.exists() else 0

            try:
                from . import models
                models.add_merge_history(
                    filename=out_name,
                    output_path=str(merged),
                    output_folder=str(out_folder),
                    file_size=file_size,
                    total_videos=total_videos,
                    task_id=task_id,
                )
            except Exception as ex_db:
                log.warning("[MergeManager] Khong the luu merge_history vao database: %s", ex_db)

            result = {
                "filename": out_name,
                "path": str(merged),
                "size": file_size,
                "download_url": f"/api/merge/download/{out_name}{folder_param}",
            }
            self._update_task(
                task_id,
                status="completed",
                progress=100.0,
                message=f"Hoàn thành ghép {total_videos} video!",
                result=result,
            )
            log.info("[MergeManager] Ghep video hoan thanh: task_id=%s -> %s (%d bytes)", task_id, out_name, file_size)

        except Exception as e:
            if check_cancelled():
                self._update_task(task_id, status="cancelled", message="Đã hủy ghép video.")
            else:
                log.exception("[MergeManager] Loi khi ghep video task_id=%s: %s", task_id, e)
                self._update_task(
                    task_id,
                    status="failed",
                    error=str(e),
                    message=f"Ghép video thất bại: {e}",
                )
        finally:
            try:
                if work_dir.is_dir():
                    shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass


merge_mgr = MergeManager()
