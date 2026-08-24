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
    const lang = window.DYFLOW_UI_LANG || "en";
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
        btn.textContent = _("topup_creating");
        try {
          const data = new FormData();
          data.append("package", btn.dataset.package);
          const res = await fetch("/api/topup", { method: "POST", body: data });
          const body = await res.json();
          if (!res.ok) throw new Error(body.detail || _("topup_create_fail"));
          payInput.value = body.payment_id;
          summary.textContent = _("topup_summary")
            .replace("{pkg}", String(body.package || "").toUpperCase())
            .replace("{amount}", String(body.amount_usd))
            .replace("{asset}", body.wallet.asset)
            .replace("{network}", body.wallet.network)
            .replace("{credits}", String(body.credits))
            .replace("{id}", body.payment_id);
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
          if (!res.ok) throw new Error(body.detail || _("topup_submit_fail"));
          if (body.auto_credited) {
            ok.textContent = body.message || _("topup_auto_ok");
          } else {
            ok.textContent = body.message || _("topup_pending_ok");
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

const DyflowAdminUsers = {
  init() {
    const err = document.getElementById("admin-error");
    const postForm = async (url, data) => {
      const body = new FormData();
      Object.entries(data).forEach(([k, v]) => body.set(k, String(v)));
      const res = await fetch(url, { method: "POST", body });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(json.detail || _("generic_error"));
      return json;
    };
    document.querySelectorAll(".admin-credit-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        err.textContent = "";
        try {
          const json = await postForm(`/api/admin/users/${btn.dataset.id}/credits`, {
            amount: btn.dataset.delta,
            note: "ui",
          });
          const cell = btn.closest("tr")?.querySelector(".u-credits");
          if (cell) cell.textContent = String(json.credits);
        } catch (ex) {
          err.textContent = ex.message;
        }
      });
    });
    document.querySelectorAll(".admin-role-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(_("admin_role_q"))) return;
        err.textContent = "";
        try {
          await postForm(`/api/admin/users/${btn.dataset.id}/admin`, {
            is_admin: btn.dataset.admin,
          });
          window.location.reload();
        } catch (ex) {
          err.textContent = ex.message;
        }
      });
    });
    document.querySelectorAll(".admin-block-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(_("admin_block_q"))) return;
        err.textContent = "";
        try {
          await postForm(`/api/admin/users/${btn.dataset.id}/block`, {
            blocked: btn.dataset.blocked,
          });
          window.location.reload();
        } catch (ex) {
          err.textContent = ex.message;
        }
      });
    });
  },
};

const DyflowAdminJobs = {
  init() {
    const err = document.getElementById("admin-error");
    document.querySelectorAll(".admin-retry-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        err.textContent = "";
        btn.disabled = true;
        try {
          const res = await fetch(`/api/admin/jobs/${btn.dataset.id}/retry`, { method: "POST" });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(body.detail || _("generic_error"));
          window.location.reload();
        } catch (ex) {
          err.textContent = ex.message;
          btn.disabled = false;
        }
      });
    });
    document.querySelectorAll(".admin-refund-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(_("admin_refund_q"))) return;
        err.textContent = "";
        btn.disabled = true;
        try {
          const res = await fetch(`/api/admin/jobs/${btn.dataset.id}/refund`, { method: "POST" });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(body.detail || _("generic_error"));
          window.location.reload();
        } catch (ex) {
          err.textContent = ex.message;
          btn.disabled = false;
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
    const urlInput = document.getElementById("url");

    this._languages = window.DYFLOW_LANGUAGES || [];
    this._ttsChoices = window.DYFLOW_TTS || [];
    this._audio = null;
    this._quoteTimer = null;
    this._quoteSeq = 0;
    this._renderVoices();
    this._renderTts();
    if (langSel) {
      langSel.addEventListener("change", () => this._renderVoices());
    }
    if (urlInput) {
      urlInput.addEventListener("input", () => this._scheduleQuote());
      urlInput.addEventListener("paste", () => {
        setTimeout(() => this._scheduleQuote(true), 50);
      });
      // Neu da co text (browser restore) -> quote 1 lan
      if ((urlInput.value || "").trim()) this._scheduleQuote(true);
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

  _scheduleQuote(immediate) {
    clearTimeout(this._quoteTimer);
    const wait = immediate ? 200 : 700;
    this._quoteTimer = setTimeout(() => this._fetchQuote(), wait);
  },

  async _fetchQuote() {
    const urlInput = document.getElementById("url");
    const box = document.getElementById("quote-box");
    const list = document.getElementById("quote-list");
    const totalEl = document.getElementById("quote-total");
    const statusEl = document.getElementById("quote-status");
    if (!urlInput || !box || !list) return;

    const text = (urlInput.value || "").trim();
    if (!text) {
      box.hidden = true;
      list.innerHTML = "";
      if (totalEl) totalEl.textContent = "";
      return;
    }

    const seq = ++this._quoteSeq;
    box.hidden = false;
    if (statusEl) statusEl.textContent = _("quote_checking");
    if (totalEl) totalEl.textContent = "…";

    try {
      const data = new FormData();
      data.set("url", text);
      const res = await fetch("/api/quote", { method: "POST", body: data });
      const body = await res.json().catch(() => ({}));
      if (seq !== this._quoteSeq) return;
      if (!res.ok) throw new Error(body.detail || _("quote_fail"));

      const items = body.items || [];
      if (!items.length) {
        box.hidden = true;
        list.innerHTML = "";
        if (totalEl) totalEl.textContent = "";
        if (statusEl) statusEl.textContent = _("quote_title");
        return;
      }

      if (statusEl) statusEl.textContent = _("quote_title");
      list.innerHTML = items
        .map((it, idx) => {
          const dur = it.unknown
            ? _("quote_unknown_dur")
            : it.duration_label || "—";
          const cred = _("quote_line_credits").replace("{n}", String(it.credits));
          const short = (it.url || "").replace(/^https?:\/\//, "");
          const tip = it.unknown ? ` title="${_("quote_unknown_tip")}"` : "";
          return (
            `<li${tip}>` +
            `<span class="quote-idx">${idx + 1}</span>` +
            `<span class="quote-url">${short}</span>` +
            `<span class="quote-meta"><em>${dur}</em> · <strong>${cred}</strong></span>` +
            `</li>`
          );
        })
        .join("");

      if (totalEl) {
        totalEl.textContent = _("quote_total")
          .replace("{n}", String(body.total_credits || 0))
          .replace("{m}", String(body.count || items.length));
      }
    } catch (ex) {
      if (seq !== this._quoteSeq) return;
      if (statusEl) statusEl.textContent = _("quote_fail");
      list.innerHTML = "";
      if (totalEl) totalEl.textContent = "";
    }
  },

  _currentLangMeta() {
    const langId = (document.getElementById("lang") || {}).value || "en";
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
      if (tpl) hint.textContent = tpl.replace("{n}", String(voices.length + (this._ttsChoices || []).length));
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

  _selectedTts() {
    return (document.querySelector('input[name="tts"]:checked') || {}).value || "edge";
  },

  _renderTts() {
    const wrap = document.getElementById("tts-options");
    if (!wrap) return;
    const choices = this._ttsChoices || [];
    const prev = (wrap.querySelector('input[name="tts"]:checked') || {}).value;
    wrap.innerHTML = "";
    choices.forEach((t) => {
      const checked = prev ? prev === t.id : t.id === "edge";
      const el = document.createElement("label");
      el.className = "voice-opt";
      el.innerHTML = `
        <input type="radio" name="tts" value="${t.id}" ${checked ? "checked" : ""} />
        <span class="voice-card">
          <strong>${t.label}</strong>
          <button type="button" class="voice-play" data-tts="${t.id}" title="${_("preview")}" aria-label="${_("preview")}">▶</button>
        </span>`;
      wrap.appendChild(el);
    });
    wrap.querySelectorAll(".voice-play").forEach((b) => {
      b.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const voiceEl = document.querySelector('input[name="voice"]:checked');
        const voiceId = (voiceEl && voiceEl.value) || "male";
        this._preview(voiceId, b, b.dataset.tts);
      });
    });
  },

  async _preview(voiceId, btn, ttsId) {
    const lang = (document.getElementById("lang") || {}).value || "en";
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
    const tts = ttsId || this._selectedTts();
    const url = `/api/voices/preview?lang=${encodeURIComponent(lang)}&voice=${encodeURIComponent(voiceId)}&tts=${encodeURIComponent(tts)}`;
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
    const queued = job.status === "queued";
    let msg = job.message || job.stage_label || "";
    if (queued && job.queue_pos) {
      msg = _("queue_wait")
        .replace("{pos}", String(job.queue_pos))
        .replace("{total}", String(job.queue_total || job.queue_pos));
    }
    this.fill.style.width = `${queued ? 0 : job.progress}%`;
    this.msg.textContent = msg;

    const ph = document.getElementById("placeholder-text");
    if (ph && job.status !== "failed" && job.status !== "completed") {
      ph.textContent = queued ? msg : _("job_processing");
    }

    this.steps.forEach((li) => {
      const stage = li.getAttribute("data-stage");
      const idx = this._stageIndex(stage);
      li.classList.remove("active", "done");
      if (queued) return;
      if (job.stage === stage) li.classList.add("active");
      else if (idx !== -1 && idx < job.stage_index) li.classList.add("done");
      if (job.status === "completed") li.classList.add("done");
    });

    if (job.status === "failed") {
      this.errEl.hidden = false;
      this.errEl.textContent = job.error || _("job_fail_msg");
      if (ph) ph.textContent = _("job_failed");
      const spin = document.querySelector(".spinner");
      if (spin) spin.style.display = "none";
    }

    if (job.status === "completed" && job.has_output) {
      this.showResult();
      this.showQa(job.qa);
    } else if (job.qa && job.qa.length) {
      this.showQa(job.qa);
    }
  },

  showQa(rows) {
    const box = document.getElementById("qa-box");
    const list = document.getElementById("qa-list");
    if (!box || !list) return;
    if (!rows || !rows.length) {
      box.hidden = true;
      return;
    }
    const flagLabel = (f) => _("qa_flag_" + f) || f;
    list.innerHTML = rows
      .map((r) => {
        const flags = (r.flags || []).map(flagLabel).join(", ") || _("qa_ok");
        const cls = (r.flags || []).length ? "qa-bad" : "qa-ok";
        const txt = (r.text || "").replace(/</g, "&lt;");
        return `<li class="${cls}"><span class="qa-meta">#${r.i + 1} · ${r.speed}x · ${flags}</span><span class="qa-text">${txt}</span></li>`;
      })
      .join("");
    box.hidden = false;
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
      a.download = `videoscreator_${this.jobId.slice(0, 12)}.mp4`;
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
