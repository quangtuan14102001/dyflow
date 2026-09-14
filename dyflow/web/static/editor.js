"use strict";

const DyflowEditor = {
  init(jobId) {
    this.jobId = jobId;
    this.layout = null;
    this.selected = null;
    this.drag = null;
    this.video = document.getElementById("ed-video");
    this.overlay = document.getElementById("ed-overlay");
    this.frame = document.getElementById("ed-frame");
    this.seek = document.getElementById("ed-seek");
    this.timeEl = document.getElementById("ed-time");
    this.err = document.getElementById("ed-error");
    this.status = document.getElementById("ed-status");
    this.bind();
    this.load();
  },

  bind() {
    const v = this.video;
    document.getElementById("ed-play").addEventListener("click", () => {
      if (v.paused) v.play();
      else v.pause();
    });
    document.getElementById("ed-back1").addEventListener("click", () => {
      v.currentTime = Math.max(0, v.currentTime - 1);
    });
    document.getElementById("ed-fwd1").addEventListener("click", () => {
      v.currentTime = Math.min(v.duration || 0, v.currentTime + 1);
    });
    this.seek.addEventListener("input", () => {
      v.currentTime = Number(this.seek.value) || 0;
    });
    v.addEventListener("timeupdate", () => this.syncTime());
    v.addEventListener("loadedmetadata", () => this.syncTime());
    v.addEventListener("play", () => {
      document.getElementById("ed-play").textContent = "❚❚";
    });
    v.addEventListener("pause", () => {
      document.getElementById("ed-play").textContent = "▶";
    });
    window.addEventListener("resize", () => this.renderBoxes());

    document.getElementById("ed-add-sub").addEventListener("click", () => this.addRegion("sub"));
    document.getElementById("ed-add-overlay").addEventListener("click", () => this.addRegion("overlay"));
    document.getElementById("ed-add-blur").addEventListener("click", () => this.addRegion("blur"));
    document.getElementById("ed-scan").addEventListener("click", () => this.scan());
    document.getElementById("ed-save").addEventListener("click", () => this.save(false));
    document.getElementById("ed-apply").addEventListener("click", () => this.apply());
    document.getElementById("ed-del").addEventListener("click", () => this.deleteSelected());
    document.getElementById("ed-whole").addEventListener("click", () => this.setRange(0, null));
    document.getElementById("ed-start-here").addEventListener("click", () => {
      this.setRange(v.currentTime, this.selected && this.selected.t1);
    });
    document.getElementById("ed-end-here").addEventListener("click", () => {
      this.setRange(this.selected ? this.selected.t0 : 0, v.currentTime);
    });

    ["ed-kind", "ed-src", "ed-text", "ed-blur"].forEach((id) => {
      document.getElementById(id).addEventListener("change", () => this.readSel());
      document.getElementById(id).addEventListener("input", () => this.readSel());
    });
    ["ed-hflip", "ed-rotate", "ed-zoom", "ed-logo-on", "ed-logo-bounce", "ed-marquee-on", "ed-marquee"].forEach((id) => {
      document.getElementById(id).addEventListener("change", () => this.readLook());
      document.getElementById(id).addEventListener("input", () => this.readLook());
    });
    document.getElementById("ed-logo-file").addEventListener("change", (ev) => this.uploadLogo(ev));

    this.overlay.addEventListener("mousedown", (ev) => this.onOverlayDown(ev));
    window.addEventListener("mousemove", (ev) => this.onDrag(ev));
    window.addEventListener("mouseup", () => {
      this.drag = null;
    });
  },

  setMsg(text, isErr) {
    this.err.hidden = !isErr;
    this.status.hidden = isErr || !text;
    if (isErr) this.err.textContent = text || "";
    else this.status.textContent = text || "";
  },

  async load() {
    this.setMsg(_("editor_loading") || "Loading…", false);
    try {
      const res = await fetch(`/api/jobs/${this.jobId}/editor`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.has_video) {
        this.setMsg(data.detail || _("editor_no_video"), true);
        return;
      }
      this.layout = data.layout;
      this.video.src = data.media_url + `?t=${Date.now()}`;
      this.syncLook();
      this.renderBoxes();
      this.setMsg("", false);
    } catch (e) {
      this.setMsg(e.message || _("generic_error"), true);
    }
  },

  syncTime() {
    const d = this.video.duration || (this.layout && this.layout.duration) || 0;
    const t = this.video.currentTime || 0;
    this.seek.max = String(d || 0);
    this.seek.value = String(t);
    this.timeEl.textContent = `${t.toFixed(1)}s / ${d.toFixed(1)}s`;
    document.getElementById("ed-play").textContent = this.video.paused ? "▶" : "❚❚";
    this.renderBoxes();
  },

  fmtRange(r) {
    if (!r) return "";
    const t1 = r.t1 == null ? _("editor_whole") : `${Number(r.t1).toFixed(1)}s`;
    return `${Number(r.t0 || 0).toFixed(1)}s → ${t1}`;
  },

  activeAt(r, t) {
    const t0 = Number(r.t0 || 0);
    const t1 = r.t1 == null ? 1e9 : Number(r.t1);
    return t + 0.04 >= t0 && t <= t1;
  },

  displayRect() {
    const v = this.video;
    const fr = this.frame.getBoundingClientRect();
    const vw = v.videoWidth || (this.layout && this.layout.video_w) || 1;
    const vh = v.videoHeight || (this.layout && this.layout.video_h) || 1;
    const ar = vw / vh;
    const cr = fr.width / Math.max(1, fr.height);
    let w, h, left, top;
    if (ar > cr) {
      w = fr.width;
      h = fr.width / ar;
      left = 0;
      top = (fr.height - h) / 2;
    } else {
      h = fr.height;
      w = fr.height * ar;
      top = 0;
      left = (fr.width - w) / 2;
    }
    return { left, top, width: w, height: h };
  },

  renderBoxes() {
    if (!this.layout) return;
    const t = this.video.currentTime || 0;
    const rect = this.displayRect();
    this.overlay.style.left = `${rect.left}px`;
    this.overlay.style.top = `${rect.top}px`;
    this.overlay.style.width = `${rect.width}px`;
    this.overlay.style.height = `${rect.height}px`;
    this.overlay.innerHTML = "";
    (this.layout.regions || []).forEach((r) => {
      const el = document.createElement("div");
      const on = this.activeAt(r, t);
      el.className = `ed-box ${r.kind}${r.id === this.selectedId() ? " selected" : ""}${on ? "" : " dim"}`;
      el.style.left = `${r.x * 100}%`;
      el.style.top = `${r.y * 100}%`;
      el.style.width = `${r.w * 100}%`;
      el.style.height = `${r.h * 100}%`;
      el.dataset.id = r.id;
      const lab = document.createElement("span");
      lab.className = "ed-box-lab";
      lab.textContent = r.kind === "sub" ? "SUB" : r.kind === "blur" ? "BLUR" : "OCR";
      el.appendChild(lab);
      const handle = document.createElement("i");
      handle.className = "ed-handle";
      el.appendChild(handle);
      el.addEventListener("mousedown", (ev) => {
        ev.stopPropagation();
        this.select(r.id);
        const isHandle = ev.target === handle;
        const box = el.getBoundingClientRect();
        this.drag = {
          id: r.id,
          mode: isHandle ? "resize" : "move",
          x0: ev.clientX,
          y0: ev.clientY,
          ox: r.x,
          oy: r.y,
          ow: r.w,
          oh: r.h,
          bw: box.width,
          bh: box.height,
        };
      });
      this.overlay.appendChild(el);
    });
  },

  onOverlayDown(ev) {
    if (ev.target !== this.overlay) return;
    this.select(null);
  },

  onDrag(ev) {
    if (!this.drag || !this.layout) return;
    const r = (this.layout.regions || []).find((x) => x.id === this.drag.id);
    if (!r) return;
    const dx = (ev.clientX - this.drag.x0) / Math.max(8, this.drag.bw / Math.max(0.02, this.drag.ow));
    const dy = (ev.clientY - this.drag.y0) / Math.max(8, this.drag.bh / Math.max(0.02, this.drag.oh));
    if (this.drag.mode === "move") {
      r.x = Math.max(0, Math.min(1 - r.w, this.drag.ox + (ev.clientX - this.drag.x0) / this.overlay.clientWidth));
      r.y = Math.max(0, Math.min(1 - r.h, this.drag.oy + (ev.clientY - this.drag.y0) / this.overlay.clientHeight));
    } else {
      r.w = Math.max(0.04, Math.min(1 - r.x, this.drag.ow + (ev.clientX - this.drag.x0) / this.overlay.clientWidth));
      r.h = Math.max(0.03, Math.min(1 - r.y, this.drag.oh + (ev.clientY - this.drag.y0) / this.overlay.clientHeight));
    }
    this.renderBoxes();
    this.fillSel();
  },

  selectedId() {
    return this.selected ? this.selected.id : null;
  },

  select(id) {
    this.selected = (this.layout.regions || []).find((r) => r.id === id) || null;
    this.fillSel();
    this.renderBoxes();
  },

  fillSel() {
    const box = document.getElementById("ed-sel");
    const empty = document.getElementById("ed-sel-empty");
    const r = this.selected;
    if (!r) {
      box.hidden = true;
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    box.hidden = false;
    document.getElementById("ed-kind").value = r.kind;
    document.getElementById("ed-src").value = r.src_text || "";
    document.getElementById("ed-text").value = r.text || "";
    document.getElementById("ed-blur").value = r.blur || "medium";
    document.getElementById("ed-range").textContent = this.fmtRange(r);
  },

  readSel() {
    const r = this.selected;
    if (!r) return;
    r.kind = document.getElementById("ed-kind").value;
    r.role = r.kind === "sub" ? "main" : r.kind;
    r.src_text = document.getElementById("ed-src").value;
    r.text = document.getElementById("ed-text").value;
    r.blur = document.getElementById("ed-blur").value;
    this.renderBoxes();
    document.getElementById("ed-range").textContent = this.fmtRange(r);
  },

  readLook() {
    if (!this.layout) return;
    this.layout.transform = this.layout.transform || {};
    this.layout.transform.hflip = document.getElementById("ed-hflip").checked;
    this.layout.transform.rotate = Number(document.getElementById("ed-rotate").value) || 0;
    this.layout.transform.zoom = Number(document.getElementById("ed-zoom").value) || 1;
    this.layout.logo = this.layout.logo || {};
    this.layout.logo.enabled = document.getElementById("ed-logo-on").checked;
    this.layout.logo.bounce = document.getElementById("ed-logo-bounce").checked;
    this.layout.marquee = this.layout.marquee || {};
    this.layout.marquee.enabled = document.getElementById("ed-marquee-on").checked;
    this.layout.marquee.text = document.getElementById("ed-marquee").value;
  },

  syncLook() {
    const tf = (this.layout && this.layout.transform) || {};
    const logo = (this.layout && this.layout.logo) || {};
    const mq = (this.layout && this.layout.marquee) || {};
    document.getElementById("ed-hflip").checked = !!tf.hflip;
    document.getElementById("ed-rotate").value = String(tf.rotate || 0);
    document.getElementById("ed-zoom").value = String(tf.zoom || 1);
    document.getElementById("ed-logo-on").checked = !!logo.enabled;
    document.getElementById("ed-logo-bounce").checked = !!logo.bounce;
    document.getElementById("ed-marquee-on").checked = !!mq.enabled;
    document.getElementById("ed-marquee").value = mq.text || "";
  },

  addRegion(kind) {
    if (!this.layout) return;
    const id = "r" + Math.random().toString(16).slice(2, 10);
    const r = {
      id,
      kind,
      role: kind === "sub" ? "main" : kind,
      x: kind === "sub" ? 0.08 : 0.2,
      y: kind === "sub" ? 0.78 : 0.18,
      w: kind === "sub" ? 0.84 : 0.55,
      h: kind === "sub" ? 0.14 : 0.12,
      t0: 0,
      t1: null,
      text: "",
      src_text: "",
      blur: "medium",
    };
    this.layout.regions = this.layout.regions || [];
    this.layout.regions.push(r);
    this.select(id);
  },

  setRange(t0, t1) {
    if (!this.selected) return;
    this.selected.t0 = Math.max(0, Number(t0) || 0);
    this.selected.t1 = t1 == null || t1 === "" ? null : Math.max(this.selected.t0 + 0.05, Number(t1));
    this.fillSel();
    this.renderBoxes();
  },

  deleteSelected() {
    if (!this.selected || !this.layout) return;
    this.layout.regions = (this.layout.regions || []).filter((r) => r.id !== this.selected.id);
    this.selected = null;
    this.fillSel();
    this.renderBoxes();
  },

  payload() {
    this.readLook();
    this.readSel();
    return { layout: this.layout };
  },

  async save(silent) {
    try {
      const res = await fetch(`/api/jobs/${this.jobId}/editor`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.payload()),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || _("editor_save_fail"));
      this.layout = data.layout || this.layout;
      if (!silent) this.setMsg(_("editor_saved"), false);
      return true;
    } catch (e) {
      this.setMsg(e.message || _("editor_save_fail"), true);
      return false;
    }
  },

  async scan() {
    this.setMsg(_("editor_scan_wait"), false);
    const windowSec = Number(document.getElementById("ed-scan-window").value) || 0;
    try {
      const res = await fetch(`/api/jobs/${this.jobId}/editor/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: this.video.currentTime || 0, window: windowSec }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || _("editor_scan_fail"));
      const found = data.regions || [];
      this.layout.regions = this.layout.regions || [];
      found.forEach((r) => this.layout.regions.push(r));
      if (found.length) this.select(found[0].id);
      else this.renderBoxes();
      this.setMsg((_("editor_scan_ok") || "{n}").replace("{n}", String(found.length)), false);
    } catch (e) {
      this.setMsg(e.message || _("editor_scan_fail"), true);
    }
  },

  async uploadLogo(ev) {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.set("file", file);
    try {
      const res = await fetch(`/api/jobs/${this.jobId}/editor/logo`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || _("editor_logo_fail"));
      document.getElementById("ed-logo-on").checked = true;
      this.readLook();
      this.setMsg(_("editor_logo_ok"), false);
    } catch (e) {
      this.setMsg(e.message || _("editor_logo_fail"), true);
    }
  },

  async apply() {
    const ok = await this.save(true);
    if (!ok) return;
    const btn = document.getElementById("ed-apply");
    btn.disabled = true;
    this.setMsg(_("editor_apply_wait"), false);
    try {
      const res = await fetch(`/api/jobs/${this.jobId}/editor/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.payload()),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || _("editor_apply_fail"));
      this.setMsg(_("editor_apply_ok"), false);
      if (window.DYFLOW_DESKTOP) {
        await fetch(`/api/jobs/${this.jobId}/export-local`, { method: "POST" });
      } else {
        const a = document.createElement("a");
        a.href = `/api/jobs/${this.jobId}/download?dl=1`;
        a.download = `videoscreator_${this.jobId.slice(0, 12)}_edit.mp4`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    } catch (e) {
      this.setMsg(e.message || _("editor_apply_fail"), true);
    } finally {
      btn.disabled = false;
    }
  },
};
