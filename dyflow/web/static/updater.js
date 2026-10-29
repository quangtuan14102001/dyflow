/**
 * In-App Auto Updater for VideosCreator
 */
"use strict";

const DyflowUpdater = {
  latestData: null,

  init() {
    this.openBtn = document.getElementById("app-update-btn") || document.getElementById("rail-update-btn");
    this.modal = document.getElementById("update-modal");
    this.closeBtn = document.getElementById("update-modal-close");
    this.statusText = document.getElementById("update-status-text");
    this.changelogWrap = document.getElementById("update-changelog-wrap");
    this.changelogText = document.getElementById("update-changelog-text");
    this.checkBtn = document.getElementById("update-check-btn");
    this.applyBtn = document.getElementById("update-apply-btn");
    this.restartBtn = document.getElementById("update-restart-btn");
    this.progressWrap = document.getElementById("update-progress-wrap");
    this.progressBar = document.getElementById("update-progress-bar");
    this.progressLabel = document.getElementById("update-progress-label");
    this.fileInput = document.getElementById("update-file-input");
    this.pickFileBtn = document.getElementById("update-pick-file-btn");
    this.rollbackBtn = document.getElementById("update-rollback-btn");

    this._bindEvents();
  },

  _bindEvents() {
    if (this.openBtn) {
      this.openBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.open();
      });
    }

    if (this.closeBtn) {
      this.closeBtn.addEventListener("click", () => this.close());
    }

    if (this.modal) {
      this.modal.addEventListener("click", (e) => {
        if (e.target === this.modal) this.close();
      });
    }

    if (this.checkBtn) {
      this.checkBtn.addEventListener("click", () => this.check());
    }

    if (this.applyBtn) {
      this.applyBtn.addEventListener("click", () => this.applyUpdate());
    }

    if (this.restartBtn) {
      this.restartBtn.addEventListener("click", () => this.restart());
    }

    if (this.pickFileBtn && this.fileInput) {
      this.pickFileBtn.addEventListener("click", () => this.fileInput.click());
      this.fileInput.addEventListener("change", (e) => {
        const file = e.target.files && e.target.files[0];
        if (file) this.uploadPatchFile(file);
      });
    }

    if (this.rollbackBtn) {
      this.rollbackBtn.addEventListener("click", () => this.rollback());
    }
  },

  open() {
    if (!this.modal) return;
    this.modal.hidden = false;
    this.modal.style.display = "flex";
  },

  close() {
    if (!this.modal) return;
    this.modal.hidden = true;
    this.modal.style.display = "none";
  },

  async check() {
    if (!this.checkBtn) return;
    this.checkBtn.disabled = true;
    this.checkBtn.textContent = "⏳ Đang kiểm tra...";
    if (this.statusText) {
      this.statusText.textContent = "Đang kết nối máy chủ để tìm bản cập nhật mới nhất...";
      this.statusText.style.color = "#38bdf8";
    }
    if (this.changelogWrap) {
      this.changelogWrap.hidden = true;
      this.changelogWrap.style.display = "none";
    }
    if (this.applyBtn) {
      this.applyBtn.hidden = true;
      this.applyBtn.style.display = "none";
    }

    try {
      const res = await fetch("/api/desktop/update/check", { method: "POST" });
      const data = await res.json().catch(() => ({}));

      if (!res.ok || !data.ok) {
        throw new Error(data.error || "Không thể kiểm tra cập nhật");
      }

      this.latestData = data;
      if (data.has_update) {
        if (this.statusText) {
          this.statusText.innerHTML = `🎉 <strong>Đã có phiên bản mới: v${data.latest_version}</strong> (hiện tại: v${data.current_version})`;
          this.statusText.style.color = "#00f2ea";
        }
        if (this.changelogWrap && data.changelog) {
          this.changelogWrap.hidden = false;
          this.changelogWrap.style.display = "block";
          if (this.changelogText) this.changelogText.textContent = data.changelog;
        }
        if (this.applyBtn) {
          this.applyBtn.hidden = false;
          this.applyBtn.style.display = "block";
          this.applyBtn.disabled = false;
          this.applyBtn.textContent = `⚡ Cập nhật lên v${data.latest_version} ngay (~1-2MB)`;
        }
        if (this.checkBtn) {
          this.checkBtn.disabled = false;
          this.checkBtn.textContent = `⚡ Cập nhật lên v${data.latest_version} ngay`;
          this.checkBtn.style.background = "linear-gradient(135deg, #10b981, #059669)";
          this.checkBtn.onclick = (e) => {
            e.preventDefault();
            this.applyUpdate();
          };
        }
      } else {
        if (this.statusText) {
          this.statusText.textContent = `✓ Bạn đang sử dụng phiên bản mới nhất (v${data.current_version}).`;
          this.statusText.style.color = "#4ade80";
        }
      }
    } catch (e) {
      if (this.statusText) {
        this.statusText.textContent = "Lỗi: " + (e.message || e);
        this.statusText.style.color = "#ff8a8a";
      }
    } finally {
      if (this.checkBtn && (!this.latestData || !this.latestData.has_update)) {
        this.checkBtn.disabled = false;
        this.checkBtn.textContent = "🔍 Kiểm tra bản mới";
      }
    }
  },

  async applyUpdate() {
    if (!this.latestData || !this.latestData.patch_url) {
      alert("Không tìm thấy đường dẫn bản vá cập nhật.");
      return;
    }

    if (this.applyBtn) this.applyBtn.disabled = true;
    if (this.checkBtn) {
      this.checkBtn.disabled = true;
      this.checkBtn.textContent = "⏳ Đang tải bản vá...";
    }
    if (this.progressWrap) {
      this.progressWrap.hidden = false;
      this.progressWrap.style.display = "flex";
    }
    if (this.progressBar) this.progressBar.style.width = "40%";
    if (this.progressLabel) this.progressLabel.textContent = "Đang tải bản vá cập nhật...";

    try {
      const res = await fetch("/api/desktop/update/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patch_url: this.latestData.patch_url }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || data.detail || "Áp dụng bản vá thất bại");
      }

      if (this.progressBar) this.progressBar.style.width = "100%";
      if (this.progressLabel) this.progressLabel.textContent = "✓ Đã áp dụng bản vá thành công!";
      if (this.statusText) {
        this.statusText.innerHTML = `🎉 <strong>Cập nhật hoàn tất!</strong> Vui lòng nhấn nút bên dưới để khởi động lại app.`;
        this.statusText.style.color = "#4ade80";
      }

      if (this.applyBtn) {
        this.applyBtn.hidden = true;
        this.applyBtn.style.display = "none";
      }
      if (this.restartBtn) {
        this.restartBtn.hidden = false;
        this.restartBtn.style.display = "block";
      }
      if (this.checkBtn) {
        this.checkBtn.disabled = false;
        this.checkBtn.textContent = "🔄 Khởi động lại app ngay";
        this.checkBtn.style.background = "linear-gradient(135deg, #0284c7, #2563eb)";
        this.checkBtn.onclick = (e) => {
          e.preventDefault();
          this.restart();
        };
      }
    } catch (e) {
      if (this.statusText) {
        this.statusText.textContent = "Lỗi cập nhật: " + (e.message || e);
        this.statusText.style.color = "#ff8a8a";
      }
      if (this.progressWrap) this.progressWrap.hidden = true;
      if (this.applyBtn) this.applyBtn.disabled = false;
    }
  },

  async uploadPatchFile(file) {
    if (!file) return;

    if (this.progressWrap) this.progressWrap.hidden = false;
    if (this.progressBar) this.progressBar.style.width = "30%";
    if (this.progressLabel) this.progressLabel.textContent = `Đang nạp file ${file.name}...`;

    const formData = new FormData();
    formData.append("patch_file", file);

    try {
      const res = await fetch("/api/desktop/update/upload-patch", {
        method: "POST",
        body: formData,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || data.detail || "Lỗi cài đặt file bản vá");
      }

      if (this.progressBar) this.progressBar.style.width = "100%";
      if (this.progressLabel) this.progressLabel.textContent = "✓ Đã nạp file vá thành công!";
      if (this.statusText) {
        this.statusText.innerHTML = `🎉 <strong>Đã cài đặt bản vá từ file!</strong> Vui lòng nhấn Khởi động lại.`;
        this.statusText.style.color = "#4ade80";
      }

      if (this.restartBtn) this.restartBtn.hidden = false;
    } catch (e) {
      if (this.statusText) {
        this.statusText.textContent = "Lỗi cài đặt: " + (e.message || e);
        this.statusText.style.color = "#ff8a8a";
      }
      if (this.progressWrap) this.progressWrap.hidden = true;
    } finally {
      if (this.fileInput) this.fileInput.value = "";
    }
  },

  async rollback() {
    if (!confirm("Bạn có chắc muốn khôi phục về phiên bản gốc ban đầu của phần mềm?")) return;
    try {
      const res = await fetch("/api/desktop/update/rollback", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      alert(data.message || "Đã khôi phục về bản gốc!");
      if (data.restart_required) {
        this.restart();
      } else {
        window.location.reload();
      }
    } catch (e) {
      alert("Lỗi khôi phục: " + e.message);
    }
  },

  async restart() {
    if (this.restartBtn) {
      this.restartBtn.disabled = true;
      this.restartBtn.textContent = "⏳ Đang khởi động lại app...";
    }
    try {
      await fetch("/api/desktop/update/restart", { method: "POST" });
    } catch (_) {}
    setTimeout(() => {
      window.location.reload();
    }, 2000);
  },
};

document.addEventListener("DOMContentLoaded", () => {
  DyflowUpdater.init();
});
