/**
 * Client-side logic for Merge Videos feature (Dyflow Merger)
 * Ho tro tien trinh ghep video chay ngam (Background Task),
 * tu do chuyen tab sidebar ma khong bi mat hay huy tien trinh.
 */
"use strict";

const DyflowMerger = {
  items: [], // Array of { file: File, id: string, name: string, size: number, enabled: boolean }
  busy: false,
  lastResult: null,
  pollInterval: null,
  pollTaskId: null,
  currentAbortCtrl: null,
  isUploading: false,

  init() {
    this.dropZone = document.getElementById("merge-drop-zone");
    this.pickFilesBtn = document.getElementById("merge-pick-files-btn");
    this.pickFolderBtn = document.getElementById("merge-pick-folder-btn");
    this.clearBtn = document.getElementById("merge-clear-btn");
    this.fileInput = document.getElementById("merge-files");
    this.folderInput = document.getElementById("merge-folder");
    this.listEl = document.getElementById("merge-list");
    this.emptyEl = document.getElementById("merge-empty");
    this.countEl = document.getElementById("merge-list-count");
    this.actionsWrap = document.getElementById("merge-list-actions");
    this.selectAllBtn = document.getElementById("merge-select-all");
    this.deselectAllBtn = document.getElementById("merge-deselect-all");
    this.startBtn = document.getElementById("merge-start-btn");
    this.cancelBtn = document.getElementById("merge-cancel-btn");
    this.newBtn = document.getElementById("merge-new-btn");
    this.errorEl = document.getElementById("merge-error");
    this.progressWrap = document.getElementById("merge-progress-wrap");
    this.progressBar = document.getElementById("merge-progress-bar");
    this.progressText = document.getElementById("merge-progress-text");
    this.resultCard = document.getElementById("merge-result-card");
    this.resultVideo = document.getElementById("merge-result-video");
    this.downloadBtn = document.getElementById("merge-download-btn");
    this.openFolderBtn = document.getElementById("merge-open-folder-btn");
    this.historyBox = document.getElementById("merge-history-box");
    this.historyList = document.getElementById("merge-history-list");
    this.historyCount = document.getElementById("merge-history-count");
    this.historyRefreshBtn = document.getElementById("merge-history-refresh-btn");

    this._bindEvents();
    this._initSaveDir();
    this._checkServerStatus();
    this._loadHistory();
  },

  _bindEvents() {
    // Buttons trigger file / folder picker (uu tien native dialog tren Desktop)
    if (this.pickFilesBtn) {
      this.pickFilesBtn.addEventListener("click", async () => {
        if (this.busy) return;
        if (document.body.classList.contains("desktop-app")) {
          try {
            const res = await fetch("/api/desktop/pick-video-files", { method: "POST" });
            const data = await res.json();
            if (data.ok && Array.isArray(data.files) && data.files.length) {
              this._addNativeFiles(data.files);
              return;
            } else if (data.cancelled) {
              return;
            }
          } catch (_) {}
        }
        if (this.fileInput) this.fileInput.click();
      });
      if (this.fileInput) {
        this.fileInput.addEventListener("change", (e) => this._handleFiles(e.target.files));
      }
    }

    if (this.pickFolderBtn) {
      this.pickFolderBtn.addEventListener("click", async () => {
        if (this.busy) return;
        if (document.body.classList.contains("desktop-app")) {
          try {
            const res = await fetch("/api/desktop/pick-video-folder", { method: "POST" });
            const data = await res.json();
            if (data.ok && Array.isArray(data.files) && data.files.length) {
              this._addNativeFiles(data.files, data.dir || "");
              return;
            } else if (data.cancelled) {
              return;
            }
          } catch (_) {}
        }
        if (this.folderInput) this.folderInput.click();
      });
      if (this.folderInput) {
        this.folderInput.addEventListener("change", (e) => this._handleFiles(e.target.files));
      }
    }

    // Bao ve khong bi mat tien trinh khi user click sang tab khac luc dang upload
    window.addEventListener("beforeunload", (e) => {
      if (this.isUploading) {
        e.preventDefault();
        e.returnValue = "Video đang được tải lên để kích hoạt tiến trình ghép ngầm. Bạn có chắc muốn rời đi?";
        return e.returnValue;
      }
    });

    document.querySelectorAll(".studio-link").forEach((link) => {
      link.addEventListener("click", (e) => {
        if (this.isUploading) {
          e.preventDefault();
          alert("Video đang được tải lên để kích hoạt tiến trình ghép ngầm. Vui lòng đợi trong giây lát!");
        }
      });
    });
    if (this.clearBtn) {
      this.clearBtn.addEventListener("click", () => this._clear());
    }
    if (this.selectAllBtn) {
      this.selectAllBtn.addEventListener("click", () => this._setAllChecked(true));
    }
    if (this.deselectAllBtn) {
      this.deselectAllBtn.addEventListener("click", () => this._setAllChecked(false));
    }

    // Drag and drop zone
    if (this.dropZone) {
      ["dragenter", "dragover"].forEach((evt) => {
        this.dropZone.addEventListener(evt, (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (!this.busy) this.dropZone.classList.add("dragover");
        });
      });
      ["dragleave", "drop"].forEach((evt) => {
        this.dropZone.addEventListener(evt, (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.dropZone.classList.remove("dragover");
        });
      });
      this.dropZone.addEventListener("drop", (e) => {
        if (this.busy) return;
        const dt = e.dataTransfer;
        if (dt && dt.files && dt.files.length) {
          this._handleFiles(dt.files);
        }
      });
    }

    // List event delegation (move up, move down, remove, toggle)
    if (this.listEl) {
      this.listEl.addEventListener("click", (e) => {
        if (this.busy) return;
        const btn = e.target.closest("[data-action]");
        if (!btn) return;
        const id = btn.getAttribute("data-id");
        const action = btn.getAttribute("data-action");
        if (action === "up") this._move(id, -1);
        else if (action === "down") this._move(id, 1);
        else if (action === "remove") this._remove(id);
      });

      this.listEl.addEventListener("change", (e) => {
        if (this.busy) return;
        if (e.target.classList.contains("merge-item-check")) {
          const id = e.target.getAttribute("data-id");
          const item = this.items.find((it) => it.id === id);
          if (item) {
            item.enabled = e.target.checked;
            this._renderList();
          }
        }
      });
    }

    // Start merge button
    if (this.startBtn) {
      this.startBtn.addEventListener("click", () => this._startMerge());
    }

    // Cancel merge button
    if (this.cancelBtn) {
      this.cancelBtn.addEventListener("click", () => this._cancelMerge());
    }

    // Start new merge button
    if (this.newBtn) {
      this.newBtn.addEventListener("click", () => this._startNew());
    }

    // Open output folder in File Explorer
    if (this.openFolderBtn) {
      this.openFolderBtn.addEventListener("click", () => this._revealOutput());
    }

    if (this.historyRefreshBtn) {
      this.historyRefreshBtn.addEventListener("click", () => this._loadHistory());
    }
  },

  _formatSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  },

  _formatTime(secs) {
    const s = Math.max(0, Math.floor(secs));
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return `${String(m).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
  },

  _handleFiles(fileList) {
    if (this.busy || !fileList || !fileList.length) return;
    const validExts = /\.(mp4|mov|mkv|webm|avi|m4v)$/i;
    let added = 0;

    Array.from(fileList).forEach((file) => {
      if (!validExts.test(file.name)) return;
      const key = `${file.name}:${file.size}:${file.lastModified}`;
      if (this.items.some((it) => it.key === key)) return;

      this.items.push({
        id: "m_" + Math.random().toString(36).substring(2, 9),
        key: key,
        file: file,
        path: file.path || "",
        name: file.webkitRelativePath || file.name,
        size: file.size,
        enabled: true,
      });
      added++;
    });

    if (added > 0) {
      if (this.errorEl) this.errorEl.textContent = "";
      this._renderList();

      const firstWithPath = this.items.find((it) => it.path);
      if (firstWithPath && firstWithPath.path) {
        const dirMatch = firstWithPath.path.match(/^(.*)[/\\][^/\\]+$/);
        if (dirMatch && dirMatch[1]) {
          const detectedDir = dirMatch[1];
          const saveInput = document.getElementById("merge-save-path-input");
          if (saveInput && !saveInput.value) {
            saveInput.value = detectedDir;
            const displayEl = document.getElementById("merge-save-path-status");
            if (displayEl) {
              displayEl.textContent = `✓ Đã chọn: ${detectedDir}`;
              displayEl.hidden = false;
            }
          }
        }
      }
    }
    if (this.fileInput) this.fileInput.value = "";
    if (this.folderInput) this.folderInput.value = "";
  },

  _addNativeFiles(files, sourceDir = "") {
    if (this.busy || !files || !files.length) return;
    let added = 0;
    files.forEach((f) => {
      const key = `${f.path || f.name}:${f.size}`;
      if (this.items.some((it) => it.key === key)) return;
      this.items.push({
        id: "m_" + Math.random().toString(36).substring(2, 9),
        key: key,
        path: f.path || "",
        name: f.name || (f.path ? f.path.split(/[\\/]/).pop() : "clip.mp4"),
        size: f.size || 0,
        enabled: true,
      });
      added++;
    });
    if (added > 0) {
      if (this.errorEl) this.errorEl.textContent = "";
      this._renderList();

      const saveInput = document.getElementById("merge-save-path-input");
      const displayEl = document.getElementById("merge-save-path-status");
      const detectedDir = sourceDir || (files[0] && files[0].path ? files[0].path.replace(/[\\/][^\\/]+$/, "") : "");
      if (detectedDir && saveInput && !saveInput.value) {
        saveInput.value = detectedDir;
        if (displayEl) {
          displayEl.textContent = `✓ Đã chọn: ${detectedDir}`;
          displayEl.hidden = false;
        }
      }
    }
  },

  _move(id, delta) {
    const idx = this.items.findIndex((it) => it.id === id);
    if (idx === -1) return;
    const targetIdx = idx + delta;
    if (targetIdx < 0 || targetIdx >= this.items.length) return;
    const tmp = this.items[idx];
    this.items[idx] = this.items[targetIdx];
    this.items[targetIdx] = tmp;
    this._renderList();
  },

  _remove(id) {
    this.items = this.items.filter((it) => it.id !== id);
    this._renderList();
  },

  _clear() {
    if (this.busy) return;
    this.items = [];
    if (this.errorEl) this.errorEl.textContent = "";
    this._renderList();
  },

  _setAllChecked(val) {
    if (this.busy) return;
    this.items.forEach((it) => (it.enabled = val));
    this._renderList();
  },

  _setLockUI(locked) {
    this.busy = locked;
    if (this.dropZone) {
      this.dropZone.style.pointerEvents = locked ? "none" : "auto";
      this.dropZone.style.opacity = locked ? "0.6" : "1";
    }
    if (this.pickFilesBtn) this.pickFilesBtn.disabled = locked;
    if (this.pickFolderBtn) this.pickFolderBtn.disabled = locked;
    if (this.clearBtn) this.clearBtn.disabled = locked;
    if (this.selectAllBtn) this.selectAllBtn.disabled = locked;
    if (this.deselectAllBtn) this.deselectAllBtn.disabled = locked;
    if (this.startBtn) this.startBtn.disabled = locked || this.items.filter((it) => it.enabled).length < 2;

    const checks = this.listEl?.querySelectorAll(".merge-item-check");
    if (checks) checks.forEach((c) => (c.disabled = locked));

    const btns = this.listEl?.querySelectorAll(".merge-ctrl-btn");
    if (btns) btns.forEach((b) => (b.disabled = locked));
  },

  _renderList() {
    const total = this.items.length;
    const enabledCount = this.items.filter((it) => it.enabled).length;

    if (this.countEl) {
      this.countEl.textContent = total
        ? `Đã chọn: ${enabledCount} / ${total} video`
        : "0 video";
    }

    if (this.clearBtn) this.clearBtn.hidden = total === 0;
    if (this.actionsWrap) this.actionsWrap.hidden = total === 0;
    if (this.startBtn) this.startBtn.disabled = enabledCount < 2 || this.busy;

    if (!this.listEl) return;
    this.listEl.innerHTML = "";

    if (total === 0) {
      if (this.emptyEl) {
        this.listEl.appendChild(this.emptyEl);
      }
      return;
    }

    this.items.forEach((it, idx) => {
      const li = document.createElement("li");
      li.className = "merge-item";
      li.setAttribute("data-id", it.id);
      if (!it.enabled) li.style.opacity = "0.5";

      const isFirst = idx === 0;
      const isLast = idx === total - 1;

      li.innerHTML = `
        <input type="checkbox" class="merge-item-check" data-id="${it.id}" ${it.enabled ? "checked" : ""} ${this.busy ? "disabled" : ""} />
        <span class="merge-item-idx">${idx + 1}</span>
        <div class="merge-item-info">
          <div class="merge-item-name" title="${this._escHtml(it.name)}">${this._escHtml(it.name)}</div>
          <div class="merge-item-size">${this._formatSize(it.size)}</div>
        </div>
        <div class="merge-item-controls">
          <button type="button" class="merge-ctrl-btn" data-action="up" data-id="${it.id}" ${isFirst || this.busy ? "disabled" : ""} title="Di chuyển lên">▲</button>
          <button type="button" class="merge-ctrl-btn" data-action="down" data-id="${it.id}" ${isLast || this.busy ? "disabled" : ""} title="Di chuyển xuống">▼</button>
          <button type="button" class="merge-ctrl-btn merge-ctrl-del" data-action="remove" data-id="${it.id}" ${this.busy ? "disabled" : ""} title="Xóa clip này">×</button>
        </div>
      `;
      this.listEl.appendChild(li);
    });
  },

  _escHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },

  _formatError(errData, fallback = "Đã xảy ra lỗi khi ghép video") {
    if (!errData) return fallback;
    if (errData instanceof Error) {
      if (errData.message && errData.message !== "[object Object]") {
        return errData.message;
      }
    }
    if (typeof errData === "string") {
      return errData === "[object Object]" ? fallback : errData;
    }
    if (typeof errData.error === "string" && errData.error && errData.error !== "[object Object]") return errData.error;
    if (typeof errData.message === "string" && errData.message && errData.message !== "[object Object]") return errData.message;
    if (typeof errData.detail === "string" && errData.detail && errData.detail !== "[object Object]") return errData.detail;
    if (Array.isArray(errData.detail) && errData.detail.length) {
      const msgs = errData.detail.map((d) => {
        if (typeof d === "string") return d;
        if (d && typeof d.msg === "string") {
          const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
          return loc ? `${loc}: ${d.msg}` : d.msg;
        }
        try {
          return JSON.stringify(d);
        } catch (_) {
          return String(d);
        }
      });
      return msgs.join("; ");
    }
    if (errData.detail && typeof errData.detail === "object") {
      try {
        return JSON.stringify(errData.detail);
      } catch (_) {
        return fallback;
      }
    }
    return fallback;
  },

  async _checkServerStatus() {
    try {
      const res = await fetch("/api/merge/status");
      if (!res.ok) return;
      const data = await res.json();
      if (!data || !data.has_task) return;

      if (data.status === "running") {
        this._setLockUI(true);
        if (Array.isArray(data.video_items) && data.video_items.length) {
          this.items = data.video_items.map((it, idx) => ({
            id: `server_${idx}`,
            name: it.name,
            size: it.size || 0,
            enabled: true,
          }));
          this._renderList();
        }
        if (this.countEl) {
          this.countEl.textContent = `Đang ghép: ${data.total_videos || this.items.length} video`;
        }
        if (this.startBtn) this.startBtn.textContent = "⏳ Đang ghép video ngầm...";
        if (this.cancelBtn) {
          this.cancelBtn.hidden = false;
          this.cancelBtn.disabled = false;
          this.cancelBtn.textContent = "⏹️ Hủy";
        }
        if (this.progressWrap) this.progressWrap.hidden = false;
        if (this.progressBar) this.progressBar.style.width = `${Math.max(5, data.progress || 5)}%`;
        if (this.progressText) {
          const timeStr = data.elapsed_seconds ? ` (${this._formatTime(data.elapsed_seconds)})` : "";
          this.progressText.textContent = `${data.message || "Đang ghép video..."}${timeStr} — Bạn có thể chuyển tab làm việc khác`;
        }
        this._startPolling(data.id);
      } else if (data.status === "completed" && data.result) {
        this.lastResult = data.result;
        if (Array.isArray(data.video_items) && data.video_items.length) {
          this.items = data.video_items.map((it, idx) => ({
            id: `server_${idx}`,
            name: it.name,
            size: it.size || 0,
            enabled: true,
          }));
          this._renderList();
        }
        if (this.countEl) {
          this.countEl.textContent = `Đã ghép xong: ${data.total_videos || this.items.length} video`;
        }
        if (this.errorEl) this.errorEl.textContent = "";
        if (this.progressWrap) this.progressWrap.hidden = false;
        if (this.progressBar) this.progressBar.style.width = "100%";
        if (this.progressText) this.progressText.textContent = `🎉 ${data.message || "Ghép video hoàn tất!"}`;
        this._renderResult(data.result);
        this._loadHistory();
      } else if (data.status === "failed") {
        if (Array.isArray(data.video_items) && data.video_items.length) {
          this.items = data.video_items.map((it, idx) => ({
            id: `server_${idx}`,
            name: it.name,
            size: it.size || 0,
            enabled: true,
          }));
          this._renderList();
        }
        if (this.errorEl) this.errorEl.textContent = data.message || "Ghép video trước đó thất bại";
      }
    } catch (_) {}
  },

  async _startMerge() {
    const active = this.items.filter((it) => it.enabled);
    if (active.length < 2) {
      if (this.errorEl) this.errorEl.textContent = "Vui lòng chọn ít nhất 2 video để ghép.";
      return;
    }

    this._setLockUI(true);
    if (this.errorEl) this.errorEl.textContent = "";
    if (this.cancelBtn) {
      this.cancelBtn.hidden = false;
      this.cancelBtn.disabled = false;
      this.cancelBtn.textContent = "⏹️ Hủy";
    }
    if (this.progressWrap) this.progressWrap.hidden = false;
    if (this.resultCard) this.resultCard.hidden = true;

    const aspect = document.getElementById("merge-aspect-ratio")?.value || "first";
    const fit = document.getElementById("merge-fit")?.value || "contain";
    const fastCopy = document.getElementById("merge-fast-copy")?.checked ? "1" : "0";
    const saveFolder = document.getElementById("merge-save-path-input")?.value || "";

    const isDesktop = document.body.classList.contains("desktop-app") || !!window.pywebview;
    const hasAnyPath = active.some((it) => it.path || (it.file && it.file.path));
    const allHaveFiles = active.every((it) => !!it.file);
    // Uu tien tuyet doi che do ghep local sieu toc tren Desktop: khong bao gio upload qua HTTP
    const shouldUseLocal = isDesktop || hasAnyPath || !allHaveFiles || !!saveFolder;

    try {
      let json = null;
      if (shouldUseLocal) {
        // Khoi dong sieu toc tren Desktop (0.005s, khong can upload qua HTTP)
        if (this.startBtn) this.startBtn.textContent = "⏳ Đang kích hoạt ghép...";
        if (this.progressBar) this.progressBar.style.width = "10%";
        if (this.progressText) this.progressText.textContent = `Đang kích hoạt ghép ${active.length} video...`;

        const pathsToSend = active.map((it) => {
          return it.path || (it.file && it.file.path ? it.file.path : "") || it.name;
        });

        const res = await fetch("/api/merge/start-local", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            paths: pathsToSend,
            aspect_ratio: aspect,
            fit: fit,
            fast_copy: fastCopy,
            save_folder: saveFolder,
          }),
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          const errMsg = this._formatError(errData, "Không khởi tạo được ghép video");
          throw new Error(errMsg);
        }
        json = await res.json();
      } else {
        // Fallback upload FormData (chi khi tren browser thuong khong phai Desktop)
        this.isUploading = true;
        if (this.startBtn) this.startBtn.textContent = "⏳ Đang tải video lên...";
        if (this.progressBar) this.progressBar.style.width = "8%";
        if (this.progressText) this.progressText.textContent = `Đang tải ${active.length} video lên và kích hoạt tiến trình ngầm...`;

        const data = new FormData();
        active.forEach((it) => {
          if (it.file) data.append("videos", it.file, it.file.name);
        });
        data.set("aspect_ratio", aspect);
        data.set("fit", fit);
        data.set("fast_copy", fastCopy);
        data.set("async_mode", "1");
        if (saveFolder) data.set("save_folder", saveFolder);

        this.currentAbortCtrl = new AbortController();
        const res = await fetch("/api/merge", {
          method: "POST",
          body: data,
          signal: this.currentAbortCtrl.signal,
        });
        this.isUploading = false;
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          const errMsg = this._formatError(errData, "Ghép video thất bại");
          throw new Error(errMsg);
        }
        json = await res.json();
      }

      if (!json || !json.ok) {
        throw new Error(this._formatError(json, "Không khởi tạo được tiến trình ghép"));
      }

      if (json.async && json.task_id) {
        if (this.startBtn) this.startBtn.textContent = "⏳ Đang ghép video ngầm...";
        this._startPolling(json.task_id);
      } else if (json.download_url) {
        // Synchronous fallback
        this._setLockUI(false);
        if (this.cancelBtn) {
          this.cancelBtn.hidden = true;
          this.cancelBtn.disabled = false;
          this.cancelBtn.textContent = "⏹️ Hủy";
        }
        if (this.startBtn) {
          this.startBtn.disabled = false;
          this.startBtn.textContent = "🚀 Bắt đầu ghép video";
        }
        this._renderResult(json);
      }
    } catch (err) {
      this.isUploading = false;
      if (err.name === "AbortError") {
        if (this.errorEl) this.errorEl.textContent = "Đã hủy tải video lên.";
      } else {
        let msg = this._formatError(err, "Đã xảy ra lỗi khi ghép video");
        if (typeof msg === "string" && msg.includes("[object Object]")) {
          msg = "Có lỗi xảy ra khi bắt đầu ghép video. Vui lòng kiểm tra lại đường dẫn thư mục hoặc file video.";
        }
        if (this.errorEl) this.errorEl.textContent = `Lỗi: ${msg}`;
      }
      if (this.progressWrap) this.progressWrap.hidden = true;
      if (this.cancelBtn) {
        this.cancelBtn.hidden = true;
        this.cancelBtn.disabled = false;
        this.cancelBtn.textContent = "⏹️ Hủy";
      }
      this._setLockUI(false);
      if (this.startBtn) {
        this.startBtn.disabled = this.items.filter((it) => it.enabled).length < 2;
        this.startBtn.textContent = "🚀 Bắt đầu ghép video";
      }
    } finally {
      this.isUploading = false;
      this.currentAbortCtrl = null;
    }
  },

  _startPolling(taskId) {
    if (this.pollInterval) clearInterval(this.pollInterval);
    this.pollTaskId = taskId;

    this.pollInterval = setInterval(async () => {
      try {
        const url = `/api/merge/status?task_id=${encodeURIComponent(this.pollTaskId || "")}`;
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        if (!data || !data.has_task) return;

        if (data.status === "running") {
          const pct = Math.max(5, Math.min(99, Math.round(data.progress || 10)));
          if (this.progressBar) this.progressBar.style.width = `${pct}%`;
          if (this.progressText) {
            const timeStr = data.elapsed_seconds ? ` (${this._formatTime(data.elapsed_seconds)})` : "";
            this.progressText.textContent = `${data.message || "Đang ghép video..."}${timeStr} — Bạn có thể chuyển tab làm việc khác`;
          }
        } else if (data.status === "completed") {
          clearInterval(this.pollInterval);
          this.pollInterval = null;
          this._setLockUI(false);
          if (this.errorEl) this.errorEl.textContent = "";
          if (this.cancelBtn) {
            this.cancelBtn.hidden = true;
            this.cancelBtn.disabled = false;
            this.cancelBtn.textContent = "⏹️ Hủy";
          }
          if (this.startBtn) {
            this.startBtn.disabled = false;
            this.startBtn.textContent = "🚀 Bắt đầu ghép video";
          }
          if (this.progressBar) this.progressBar.style.width = "100%";
          if (this.progressText) {
            const timeStr = data.elapsed_seconds ? ` (${this._formatTime(data.elapsed_seconds)})` : "";
            this.progressText.textContent = `🎉 ${data.message || "Ghép video hoàn tất!"}${timeStr}`;
          }
          if (data.result) {
            this._renderResult(data.result);
          }
          this._loadHistory();
        } else if (data.status === "failed") {
          clearInterval(this.pollInterval);
          this.pollInterval = null;
          this._setLockUI(false);
          if (this.cancelBtn) {
            this.cancelBtn.hidden = true;
            this.cancelBtn.disabled = false;
            this.cancelBtn.textContent = "⏹️ Hủy";
          }
          if (this.startBtn) {
            this.startBtn.disabled = this.items.filter((it) => it.enabled).length < 2;
            this.startBtn.textContent = "🚀 Bắt đầu ghép video";
          }
          if (this.progressWrap) this.progressWrap.hidden = true;
          let failMsg = this._formatError(data, "Ghép video thất bại");
          if (typeof failMsg === "string" && failMsg.includes("[object Object]")) {
            failMsg = data.message || data.error || "Ghép video thất bại";
          }
          if (this.errorEl) this.errorEl.textContent = `Lỗi: ${failMsg}`;
        } else if (data.status === "cancelled") {
          clearInterval(this.pollInterval);
          this.pollInterval = null;
          this._setLockUI(false);
          if (this.cancelBtn) {
            this.cancelBtn.hidden = true;
            this.cancelBtn.disabled = false;
            this.cancelBtn.textContent = "⏹️ Hủy";
          }
          if (this.startBtn) {
            this.startBtn.disabled = this.items.filter((it) => it.enabled).length < 2;
            this.startBtn.textContent = "🚀 Bắt đầu ghép video";
          }
          if (this.progressWrap) this.progressWrap.hidden = true;
          if (this.errorEl) this.errorEl.textContent = "Đã hủy tiến trình ghép video.";
        }
      } catch (_) {}
    }, 1000);
  },

  async _cancelMerge() {
    if (!confirm("Bạn có chắc chắn muốn hủy tiến trình ghép video đang chạy?")) {
      return;
    }
    if (this.cancelBtn) {
      this.cancelBtn.disabled = true;
      this.cancelBtn.textContent = "⏳ Đang hủy...";
    }
    if (this.currentAbortCtrl) {
      try {
        this.currentAbortCtrl.abort();
      } catch (_) {}
      this.currentAbortCtrl = null;
    }
    try {
      await fetch("/api/merge/cancel", { method: "POST" });
    } catch (_) {}
  },

  async _startNew() {
    try {
      await fetch("/api/merge/clear", { method: "POST" });
    } catch (_) {}
    if (this.resultCard) this.resultCard.hidden = true;
    if (this.progressWrap) this.progressWrap.hidden = true;
    if (this.errorEl) this.errorEl.textContent = "";
    this._clear();
  },

  _renderResult(json) {
    if (!json || typeof json !== "object") return;
    this.lastResult = json;
    if (this.errorEl) this.errorEl.textContent = "";
    if (this.resultCard) this.resultCard.hidden = false;
    const dlUrl = typeof json.download_url === "string" ? json.download_url : "";
    if (this.resultVideo && dlUrl) {
      this.resultVideo.src = dlUrl;
      this.resultVideo.load();
    }
    if (this.downloadBtn && dlUrl) {
      const sep = dlUrl.includes("?") ? "&" : "?";
      this.downloadBtn.href = dlUrl + sep + "dl=1";
      this.downloadBtn.download = json.filename || "merged_video.mp4";
    }
    if (this.openFolderBtn) {
      this.openFolderBtn.hidden = false;
      this.openFolderBtn.textContent = "📂 Mở thư mục chứa video";
    }
    try {
      this.resultCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (_) {}
  },

  _initSaveDir() {
    const input = document.getElementById("merge-save-path-input");
    const browse = document.getElementById("merge-save-path-browse");
    const status = document.getElementById("merge-save-path-status");

    if (browse) {
      browse.hidden = false;
      browse.addEventListener("click", (e) => {
        e.preventDefault();
        this._pickSaveDir();
      });
    }

    if (input) {
      const persistDir = () => {
        const dir = (input.value || "").trim();
        if (dir) {
          fetch("/api/desktop/merge-save-prefs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ dir }),
          })
            .then((r) => r.json())
            .then((res) => {
              if (res.ok && res.dir) {
                input.value = res.dir;
                input.title = res.dir;
                input.placeholder = res.dir;
                if (status) {
                  status.hidden = false;
                  status.textContent = "✓ Đã lưu thư mục: " + res.dir;
                  status.style.color = "#4ade80";
                  setTimeout(() => {
                    if (status && status.textContent.includes(res.dir)) {
                      status.hidden = true;
                    }
                  }, 4000);
                }
              }
            })
            .catch(() => {});
        }
      };
      input.addEventListener("change", persistDir);
      input.addEventListener("blur", persistDir);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          persistDir();
        }
      });
    }

    // Load initial default save folder for merge
    fetch("/api/desktop/merge-save-prefs")
      .then((res) => res.json())
      .then((data) => {
        if (data.dir && input) {
          if (!input.value || input.value === "Downloads/Merged_Videos") {
            input.value = data.dir;
            input.title = data.dir;
            input.placeholder = data.dir;
          }
        }
      })
      .catch(() => {});
  },

  async _pickSaveDir() {
    const browse = document.getElementById("merge-save-path-browse");
    const input = document.getElementById("merge-save-path-input");
    const status = document.getElementById("merge-save-path-status");

    if (browse) browse.disabled = true;
    if (status) {
      status.hidden = false;
      status.textContent = "⏳ Đang mở cửa sổ chọn thư mục...";
      status.style.color = "#9da2b8";
    }

    try {
      const res = await fetch("/api/desktop/merge-pick-folder", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || "Không thể chọn thư mục");
      }
      if (data.cancelled) {
        if (status) {
          status.hidden = false;
          status.textContent = "Đã hủy chọn thư mục.";
          status.style.color = "#9da2b8";
          setTimeout(() => {
            if (status) status.hidden = true;
          }, 2500);
        }
        return;
      }
      if (data.ok && data.dir) {
        if (input) {
          input.value = data.dir;
          input.title = data.dir;
          input.placeholder = data.dir;
        }
        if (status) {
          status.hidden = false;
          status.textContent = "✓ Đã chọn: " + data.dir;
          status.style.color = "#4ade80";
        }
      } else {
        throw new Error(data.detail || "Không thể chọn thư mục");
      }
    } catch (e) {
      if (status) {
        status.hidden = false;
        status.textContent = "Lỗi chọn thư mục: " + (e.message || e);
        status.style.color = "#ff8a8a";
      }
    } finally {
      if (browse) browse.disabled = false;
    }
  },

  async _revealOutput() {
    if (!this.lastResult) return;
    try {
      if (this.openFolderBtn) this.openFolderBtn.textContent = "⏳ Đang mở Explorer...";
      const res = await fetch("/api/merge/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: this.lastResult.path,
          filename: this.lastResult.filename,
        }),
      });
      const data = await res.json();
      if (!data.ok && data.error) {
        alert(data.error);
      }
    } catch (e) {
      console.warn("Loi mo thu muc:", e);
    } finally {
      if (this.openFolderBtn) this.openFolderBtn.textContent = "📂 Mở thư mục chứa video";
    }
  },

  async _loadHistory() {
    if (!this.historyList) return;
    try {
      const res = await fetch("/api/merge/history");
      if (!res.ok) return;
      const data = await res.json();
      const items = Array.isArray(data.items) ? data.items : [];
      if (this.historyCount) {
        this.historyCount.textContent = items.length;
      }
      if (!items.length) {
        this.historyList.innerHTML = '<div style="color: var(--muted); font-size: 13px; text-align: center; padding: 14px 0;">Chưa có video nào trong lịch sử ghép.</div>';
        return;
      }

      this.historyList.innerHTML = items.map((it) => {
        const name = this._escapeHtml(it.filename || "merged_video.mp4");
        const sizeStr = it.size_fmt || (it.file_size ? `${(it.file_size / (1024*1024)).toFixed(1)} MB` : "");
        const timeStr = it.created_at_fmt || "";
        const clipsStr = it.total_videos ? ` · ${it.total_videos} clip` : "";
        const downloadUrl = `/api/merge/download/${encodeURIComponent(it.filename || "")}?dl=1`;
        const viewUrl = `/api/merge/download/${encodeURIComponent(it.filename || "")}`;
        const safePath = (it.output_path || "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");

        return `
          <div class="merge-history-item" style="display: flex; align-items: center; justify-content: space-between; gap: 10px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); padding: 8px 12px; border-radius: 8px;">
            <div style="min-width: 0; flex: 1;">
              <div style="display: flex; align-items: center; gap: 6px;">
                <span style="font-size: 14px;">🎞️</span>
                <span style="font-size: 13px; font-weight: 600; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 260px;" title="${name}">${name}</span>
              </div>
              <div style="font-size: 11.5px; color: var(--muted); margin-top: 2px;">
                ${sizeStr ? `<span>${sizeStr}</span>` : ""}
                ${clipsStr ? `<span>${clipsStr}</span>` : ""}
                ${timeStr ? ` · <span>${timeStr}</span>` : ""}
              </div>
            </div>
            <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0;">
              <a href="${viewUrl}" target="_blank" class="btn primary btn-xs" style="text-decoration: none; padding: 4px 8px; font-size: 11.5px; font-weight: 600;">▶ Xem</a>
              <a href="${downloadUrl}" class="btn ghost btn-xs" style="text-decoration: none; padding: 4px 8px; font-size: 11.5px;">⬇ Tải</a>
              <button type="button" class="btn ghost btn-xs" onclick="DyflowMerger._revealHistoryItem('${safePath}', '${name}')" style="padding: 4px 8px; font-size: 11.5px; cursor: pointer;" title="Mở thư mục chứa file">📁 Mở</button>
              <button type="button" class="btn ghost btn-xs danger" onclick="DyflowMerger._deleteHistoryItem('${it.id}')" style="padding: 4px 6px; font-size: 11px; border: none; opacity: 0.6; cursor: pointer;" title="Xóa khỏi lịch sử">✕</button>
            </div>
          </div>
        `;
      }).join("");

    } catch (e) {
      console.warn("Loi tai lich su ghep:", e);
    }
  },

  async _revealHistoryItem(path, filename) {
    try {
      const res = await fetch("/api/merge/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, filename }),
      });
      const d = await res.json();
      if (!d.ok && d.error) alert(d.error);
    } catch (e) {
      alert("Lỗi: " + e);
    }
  },

  async _deleteHistoryItem(id) {
    if (!confirm("Xóa video này khỏi lịch sử ghép?")) return;
    try {
      const res = await fetch(`/api/merge/history/${encodeURIComponent(id)}`, { method: "DELETE" });
      const d = await res.json();
      if (d.ok) this._loadHistory();
    } catch (e) {
      alert("Lỗi: " + e);
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  DyflowMerger.init();
});
