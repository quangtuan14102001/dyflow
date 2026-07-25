"use strict";

function _(key, fallback) {
  const bag = window.DYFLOW_I18N || {};
  return bag[key] || fallback || key;
}

const DyflowTime = {
  _localeMap: {
    vi: "vi-VN",
    en: "en-US",
    ja: "ja-JP",
    pt: "pt-BR",
    hi: "hi-IN",
    de: "de-DE",
    id: "id-ID",
    tl: "fil-PH",
  },

  format(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return "";
    const lang = window.DYFLOW_UI_LANG || "vi";
    const locale = this._localeMap[lang] || lang || undefined;
    try {
      return new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: lang === "en" || lang === "tl" ? "short" : "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: lang === "en" || lang === "tl",
      }).format(new Date(n * 1000));
    } catch (_) {
      return new Date(n * 1000).toLocaleString();
    }
  },

  init() {
    document.querySelectorAll("[data-ts]").forEach((el) => {
      const text = this.format(el.getAttribute("data-ts"));
      if (text) el.textContent = text;
    });
  },
};

// Nut dang xuat + menu mobile (co o moi trang khi da dang nhap)
document.addEventListener("DOMContentLoaded", () => {
  DyflowTime.init();
  const btn = document.getElementById("logout-btn");
  if (btn) {
    btn.addEventListener("click", async () => {
      await fetch("/api/logout", { method: "POST" });
      window.location.href = "/login";
    });
  }

  const langSel = document.getElementById("ui-lang");
  if (langSel) {
    langSel.addEventListener("change", async () => {
      const data = new FormData();
      data.set("lang", langSel.value);
      try {
        await fetch("/api/ui-lang", { method: "POST", body: data });
      } catch (_) { /* ignore */ }
      window.location.reload();
    });
  }

  const toggle = document.getElementById("nav-toggle");
  const nav = document.getElementById("site-nav");
  if (toggle && nav) {
    const setOpen = (open) => {
      document.body.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    };
    toggle.addEventListener("click", () => {
      setOpen(!document.body.classList.contains("nav-open"));
    });
    nav.querySelectorAll("a, button.nav-link").forEach((el) => {
      el.addEventListener("click", () => setOpen(false));
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setOpen(false);
    });
    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 761px)").matches) setOpen(false);
    });
  }
});

const DyflowAuth = {
  init() {
    this._bindTogglePass();
    const form = document.getElementById("auth-form");
    if (!form) return;
    const mode = form.dataset.mode;
    const btn = document.getElementById("auth-btn");
    const err = document.getElementById("auth-error");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      btn.disabled = true;
      try {
        const data = new FormData(form);
        const url = mode === "register" ? "/api/register" : "/api/login";
        const res = await fetch(url, { method: "POST", body: data });
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || _("generic_error"));
        window.location.href = "/";
      } catch (ex) {
        err.textContent = ex.message;
        btn.disabled = false;
      }
    });
  },

  _bindTogglePass() {
    document.querySelectorAll(".toggle-pass").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.target;
        const input = document.getElementById(id);
        if (!input) return;
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.textContent = show ? _("hide") : _("show");
      });
    });
  },
};

const DyflowForgot = {
  init() {
    const form = document.getElementById("forgot-form");
    if (!form) return;
    const btn = document.getElementById("forgot-btn");
    const err = document.getElementById("forgot-error");
    const ok = document.getElementById("forgot-ok");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      ok.hidden = true;
      btn.disabled = true;
      try {
        const data = new FormData(form);
        const res = await fetch("/api/forgot-password", { method: "POST", body: data });
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || _("generic_error"));
        ok.textContent = body.message || "Đã gửi email xác nhận. Vui lòng kiểm tra hộp thư.";
        ok.hidden = false;
      } catch (ex) {
        err.textContent = ex.message;
      } finally {
        btn.disabled = false;
      }
    });
  },
};

const DyflowReset = {
  init() {
    DyflowAuth._bindTogglePass();
    const form = document.getElementById("reset-form");
    if (!form) return;
    const btn = document.getElementById("reset-btn");
    const err = document.getElementById("reset-error");
    const ok = document.getElementById("reset-ok");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      ok.hidden = true;
      const pw = form.password.value;
      const pw2 = form.password2.value;
      if (pw !== pw2) {
        err.textContent = _("password_mismatch");
        return;
      }
      btn.disabled = true;
      try {
        const data = new FormData(form);
        const res = await fetch("/api/reset-password", { method: "POST", body: data });
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || _("reset_fail"));
        ok.hidden = false;
        setTimeout(() => (window.location.href = "/login"), 1200);
      } catch (ex) {
        err.textContent = ex.message;
        btn.disabled = false;
      }
    });
  },
};

const DyflowBilling = {
  init() {
    const err = document.getElementById("topup-error");
    const ok = document.getElementById("topup-ok");
    const panel = document.getElementById("topup-panel");
    const summary = document.getElementById("topup-summary");
    const payInput = document.getElementById("payment_id");
    const form = document.getElementById("submit-tx-form");
    const copyBtn = document.getElementById("copy-wallet");
    const addr = document.getElementById("wallet-address");

    if (copyBtn && addr) {
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(addr.textContent.trim());
          copyBtn.textContent = _("copied");
          setTimeout(() => (copyBtn.textContent = _("copy")), 1500);
        } catch (_) {
          copyBtn.textContent = _("copy");
        }
      });
    }

    document.querySelectorAll(".topup-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (err) err.textContent = "";
        if (ok) ok.hidden = true;
        btn.disabled = true;
        const old = btn.textContent;
        btn.textContent = "Đang tạo đơn...";
        try {
          const data = new FormData();
          data.append("package", btn.dataset.package);
          const res = await fetch("/api/topup", { method: "POST", body: data });
          const body = await res.json();
          if (!res.ok) throw new Error(body.detail || "Không tạo được đơn");
          payInput.value = body.payment_id;
          summary.textContent =
            `Gói ${body.package}: chuyển ${body.amount_usd} ${body.wallet.asset} ` +
            `(${body.wallet.network}) → nhận ${body.credits} credit. Mã đơn: ${body.payment_id}`;
          panel.hidden = false;
          panel.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (ex) {
          if (err) err.textContent = ex.message;
        } finally {
          btn.disabled = false;
          btn.textContent = old;
        }
      });
    });

    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (err) err.textContent = "";
        if (ok) ok.hidden = true;
        const submitBtn = document.getElementById("submit-tx-btn");
        submitBtn.disabled = true;
        try {
          const data = new FormData(form);
          const res = await fetch("/api/topup/submit", { method: "POST", body: data });
          const body = await res.json();
          if (!res.ok) throw new Error(body.detail || "Gửi thất bại");
          if (body.auto_credited) {
            ok.textContent = body.message || "Đã cộng credit tự động!";
          } else {
            ok.textContent = body.message || "Đã gửi. Đang chờ admin duyệt.";
          }
          ok.hidden = false;
          setTimeout(() => window.location.reload(), 1500);
        } catch (ex) {
          err.textContent = ex.message;
          submitBtn.disabled = false;
        }
      });
    }
  },
};

const DyflowAdminPayments = {
  init() {
    const err = document.getElementById("admin-error");
    document.querySelectorAll(".approve-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(_("approve_q"))) return;
        err.textContent = "";
        btn.disabled = true;
        try {
          const res = await fetch(`/api/admin/payments/${btn.dataset.id}/approve`, { method: "POST" });
          const body = await res.json();
          if (!res.ok) throw new Error(body.detail || "Duyệt thất bại");
          window.location.reload();
        } catch (ex) {
          err.textContent = ex.message;
          btn.disabled = false;
        }
      });
    });
    document.querySelectorAll(".reject-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(_("reject_q"))) return;
        err.textContent = "";
        try {
          const res = await fetch(`/api/admin/payments/${btn.dataset.id}/reject`, { method: "POST" });
          const body = await res.json();
          if (!res.ok) throw new Error(body.detail || "Từ chối thất bại");
          window.location.reload();
        } catch (ex) {
          err.textContent = ex.message;
        }
      });
    });
  },
};

const DyflowTrial = {
  init() {
    const btn = document.getElementById("trial-verify-btn");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      const prev = btn.textContent;
      btn.disabled = true;
      btn.textContent = _("trial_verify_open");
      try {
        const res = await fetch("/api/telegram/link", { method: "POST" });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || _("trial_verify_fail"));
        if (body.deep_link) {
          window.open(body.deep_link, "_blank", "noopener");
        } else {
          throw new Error(_("trial_verify_fail"));
        }
      } catch (ex) {
        alert(ex.message || _("trial_verify_fail"));
      } finally {
        btn.disabled = false;
        btn.textContent = prev;
      }
    });
  },
};

const DyflowHome = {
  init() {
    const form = document.getElementById("job-form");
    if (!form) return;
    const btn = document.getElementById("submit-btn");
    const err = document.getElementById("form-error");
    const langSel = document.getElementById("lang");

    this._languages = window.DYFLOW_LANGUAGES || [];
    this._audio = null;
    this._renderVoices();
    if (langSel) {
      langSel.addEventListener("change", () => this._renderVoices());
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      btn.disabled = true;
      btn.textContent = _("sending");
      try {
        const data = new FormData(form);
        const res = await fetch("/api/jobs", { method: "POST", body: data });
        if (res.status === 402) {
          err.innerHTML = _("no_credit");
          btn.disabled = false;
          btn.textContent = _("start");
          return;
        }
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || _("job_create_fail"));
        const ids = body.ids || (body.id ? [body.id] : []);
        if (ids.length > 1) {
          // nhieu job: ve trang chu de xem hang doi (2 luong chay song song)
          window.location.href = "/";
          return;
        }
        window.location.href = `/jobs/${ids[0] || body.id}`;
      } catch (ex) {
        err.textContent = ex.message;
        btn.disabled = false;
        btn.textContent = _("start");
      }
    });
  },

  _currentLangMeta() {
    const langId = (document.getElementById("lang") || {}).value || "vi";
    return (this._languages || []).find((l) => l.id === langId) || null;
  },

  _renderVoices() {
    const wrap = document.getElementById("voice-options");
    const hint = document.getElementById("voice-hint");
    if (!wrap) return;
    const meta = this._currentLangMeta();
    const voices = (meta && meta.voices) || [];
    if (hint) {
      hint.textContent = voices.length
        ? _("voice_count", "").replace("{n}", String(voices.length)) ||
          `${voices.length} voices`
        : "";
      // prefer i18n if key exists without broken replace
      const tpl = (window.DYFLOW_I18N || {}).voice_count;
      if (tpl) hint.textContent = tpl.replace("{n}", String(voices.length));
    }
    const prev = (wrap.querySelector('input[name="voice"]:checked') || {}).value;
    wrap.innerHTML = "";
    voices.forEach((v, i) => {
      const checked = prev ? prev === v.id : i === 0;
      const label = v.gender === "female" ? _("voice_female") : _("voice_male");
      const el = document.createElement("label");
      el.className = "voice-opt";
      el.innerHTML = `
        <input type="radio" name="voice" value="${v.id}" ${checked ? "checked" : ""} />
        <span class="voice-card">
          <strong>${label}</strong>
          <span class="voice-gender">${v.gender === "female" ? "♀" : "♂"}</span>
          <button type="button" class="voice-play" data-voice="${v.id}" title="${_("preview")}" aria-label="${_("preview")}">▶</button>
        </span>`;
      wrap.appendChild(el);
    });
    wrap.querySelectorAll(".voice-play").forEach((b) => {
      b.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        this._preview(b.dataset.voice, b);
      });
    });
  },

  async _preview(voiceId, btn) {
    const lang = (document.getElementById("lang") || {}).value || "vi";
    const resetBtns = () => {
      document.querySelectorAll(".voice-play").forEach((b) => {
        b.textContent = "▶";
        b.disabled = false;
      });
    };
    if (this._audio) {
      this._audio.pause();
      this._audio = null;
      resetBtns();
    }
    const url = `/api/voices/preview?lang=${encodeURIComponent(lang)}&voice=${encodeURIComponent(voiceId)}`;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const audio = new Audio(url);
      this._audio = audio;
      audio.onended = resetBtns;
      audio.onerror = () => {
        resetBtns();
        const err = document.getElementById("form-error");
        if (err) err.textContent = _("preview_fail");
      };
      await audio.play();
      btn.textContent = "■";
      btn.disabled = false;
    } catch (ex) {
      resetBtns();
      const err = document.getElementById("form-error");
      if (err) err.textContent = ex.message || _("preview_fail");
    }
  },
};

const DyflowJob = {
  init(jobId) {
    this.jobId = jobId;
    this.fill = document.getElementById("progress-fill");
    this.msg = document.getElementById("progress-msg");
    this.steps = Array.from(document.querySelectorAll("#stepper li"));
    this.errEl = document.getElementById("job-error");
    this._autoDownloaded = false;
    this.connect();
  },

  connect() {
    const src = new EventSource(`/api/jobs/${this.jobId}/events`);
    src.onmessage = (ev) => {
      const job = JSON.parse(ev.data);
      this.render(job);
      if (job.status === "completed" || job.status === "failed") {
        src.close();
      }
    };
    src.onerror = () => {
      // SSE ngat -> fallback polling
      src.close();
      this.poll();
    };
  },

  async poll() {
    try {
      const res = await fetch(`/api/jobs/${this.jobId}`);
      const job = await res.json();
      this.render(job);
      if (job.status !== "completed" && job.status !== "failed") {
        setTimeout(() => this.poll(), 1500);
      }
    } catch (e) {
      setTimeout(() => this.poll(), 3000);
    }
  },

  render(job) {
    this.fill.style.width = `${job.progress}%`;
    this.msg.textContent = job.message || job.stage_label;

    this.steps.forEach((li) => {
      const stage = li.getAttribute("data-stage");
      const idx = this._stageIndex(stage);
      li.classList.remove("active", "done");
      if (job.stage === stage) li.classList.add("active");
      else if (idx !== -1 && idx < job.stage_index) li.classList.add("done");
      if (job.status === "completed") li.classList.add("done");
    });

    if (job.status === "failed") {
      this.errEl.hidden = false;
      this.errEl.textContent = job.error || _("job_fail_msg");
      const ph = document.getElementById("placeholder-text");
      if (ph) ph.textContent = _("job_failed");
      const spin = document.querySelector(".spinner");
      if (spin) spin.style.display = "none";
    }

    if (job.status === "completed" && job.has_output) {
      this.showResult();
    }
  },

  showResult() {
    const wrap = document.getElementById("video-wrap");
    const actions = document.getElementById("result-actions");
    const playUrl = `/api/jobs/${this.jobId}/download`;
    const dlUrl = `/api/jobs/${this.jobId}/download?dl=1`;
    wrap.innerHTML = `<video controls autoplay muted playsinline src="${playUrl}"></video>`;
    if (actions) {
      actions.hidden = false;
      document.getElementById("download-btn").href = dlUrl;
    }
    const note = document.getElementById("auto-dl-note");
    if (note) note.hidden = false;
    this._autoDownload(dlUrl);
  },

  _autoDownload(url) {
    // Chi tu dong 1 lan / job; can giu tab trang job dang mo.
    if (this._autoDownloaded) return;
    this._autoDownloaded = true;
    try {
      const a = document.createElement("a");
      a.href = url;
      a.download = `dyflow_${this.jobId.slice(0, 12)}.mp4`;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (_) {
      // Fallback: iframe an (mot so trinh duyet chan a.click khong gesture)
      const iframe = document.createElement("iframe");
      iframe.style.display = "none";
      iframe.src = url;
      document.body.appendChild(iframe);
      setTimeout(() => iframe.remove(), 60_000);
    }
  },

  _stages: [
    "queued", "starting", "downloading", "trimming",
    "transforming", "transcribing", "synthesizing", "composing", "completed",
  ],
  _stageIndex(stage) {
    return this._stages.indexOf(stage);
  },
};
