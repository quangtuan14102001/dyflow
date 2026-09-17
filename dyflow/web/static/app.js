"use strict";

function _(key, fallback) {
  const bag = window.DYFLOW_I18N || {};
  return bag[key] || fallback || key;
}

function _apiDetail(body, fallback) {
  const d = body && body.detail;
  if (Array.isArray(d)) {
    const msg = d.map((x) => (x && (x.msg || x.message)) || x).filter(Boolean).join(" ");
    return msg || fallback;
  }
  if (typeof d === "string" && d.trim()) return d;
  if (d && typeof d === "object") {
    const msg = d.msg || d.message || d.error;
    if (typeof msg === "string" && msg.trim()) return msg;
  }
  return fallback;
}

async function _readApiError(res, fallback) {
  const raw = await res.text();
  let body = {};
  try {
    body = raw ? JSON.parse(raw) : {};
  } catch (_) {
    body = {};
  }
  const fromJson = _apiDetail(body, "");
  if (fromJson) return fromJson;
  const plain = (raw || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  if (plain && plain.length < 400 && !/^<!doctype|^<html/i.test(plain)) return plain;
  return fallback;
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

const DyflowLicense = {
  init() {
    const form = document.getElementById("license-form");
    const btn = document.getElementById("license-btn");
    const err = document.getElementById("license-error");
    const input = document.getElementById("license-key");
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (err) err.textContent = "";
        if (btn) btn.disabled = true;
        try {
          const data = new FormData();
          data.set("key", (input && input.value) || "");
          const res = await fetch("/api/license", { method: "POST", body: data });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(_apiDetail(body, _("license_invalid")));
          window.location.href = "/";
        } catch (ex) {
          if (err) err.textContent = ex.message || _("generic_error");
          if (btn) btn.disabled = false;
        }
      });
    }
    this._mint();
    this._renew();
    this._openaiTts();
    this._machineCopy();
    if (input && (location.hash === "#license-key" || location.hash === "#license-form")) {
      input.focus();
      try { input.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (_) {}
    }
  },

  _mint() {
    const form = document.getElementById("license-mint-form");
    if (!form) return;
    const btn = document.getElementById("license-mint-btn");
    const err = document.getElementById("license-mint-error");
    const ok = document.getElementById("license-mint-ok");
    const out = document.getElementById("license-mint-out");
    const keyEl = document.getElementById("license-mint-key");
    const copyBtn = document.getElementById("license-mint-copy");
    const roleEl = document.getElementById("license-mint-role");
    const daysEl = document.getElementById("license-mint-days");
    const machineEl = document.getElementById("license-mint-machine");
    document.querySelectorAll(".license-grant").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll(".license-grant").forEach((x) => x.classList.remove("on"));
        b.classList.add("on");
        if (daysEl) daysEl.value = b.dataset.days || "5";
      });
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (err) err.textContent = "";
      if (ok) {
        ok.hidden = true;
        ok.textContent = "";
      }
      if (btn) btn.disabled = true;
      try {
        const data = new FormData();
        data.set("role", (roleEl && roleEl.value) || "user");
        data.set("days", (daysEl && daysEl.value) || "5");
        data.set("machine", (machineEl && machineEl.value) || "");
        const res = await fetch("/api/license/mint", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("license_mint_fail")));
        if (keyEl) keyEl.value = body.key || "";
        if (out) out.hidden = false;
        if (ok) {
          const msg = _("license_mint_ok")
            .replace("{role}", body.role || "")
            .replace("{date}", body.until_label || "")
            .replace("{n}", String(body.days != null ? body.days : ""));
          ok.textContent = msg;
          ok.hidden = false;
        }
      } catch (ex) {
        if (err) err.textContent = ex.message || _("license_mint_fail");
      } finally {
        if (btn) btn.disabled = false;
      }
    });
    if (copyBtn && keyEl) {
      copyBtn.addEventListener("click", async () => {
        const text = keyEl.value || "";
        if (!text) return;
        try {
          await navigator.clipboard.writeText(text);
          copyBtn.textContent = _("copied");
          setTimeout(() => { copyBtn.textContent = _("license_mint_copy"); }, 1400);
        } catch (_) { /* ignore */ }
      });
    }
  },

  _renew() {
    const form = document.getElementById("license-renew-form");
    if (!form) return;
    const btn = document.getElementById("license-renew-btn");
    const err = document.getElementById("license-renew-error");
    const ok = document.getElementById("license-mint-ok");
    const out = document.getElementById("license-mint-out");
    const keyEl = document.getElementById("license-mint-key");
    const daysEl = document.getElementById("license-renew-days");
    const oldEl = document.getElementById("license-renew-key");
    const machineEl = document.getElementById("license-renew-machine");
    document.querySelectorAll(".license-renew-grant").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll(".license-renew-grant").forEach((x) => x.classList.remove("on"));
        b.classList.add("on");
        if (daysEl) daysEl.value = b.dataset.days || "30";
      });
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (err) err.textContent = "";
      if (btn) btn.disabled = true;
      try {
        const data = new FormData();
        data.set("key", (oldEl && oldEl.value) || "");
        data.set("days", (daysEl && daysEl.value) || "30");
        data.set("machine", (machineEl && machineEl.value) || "");
        const res = await fetch("/api/license/renew", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("license_mint_fail")));
        if (keyEl) keyEl.value = body.key || "";
        if (out) out.hidden = false;
        if (ok) {
          ok.textContent = _("license_mint_ok")
            .replace("{role}", body.role || "")
            .replace("{date}", body.until_label || "")
            .replace("{n}", String(body.days != null ? body.days : ""));
          ok.hidden = false;
        }
      } catch (ex) {
        if (err) err.textContent = ex.message || _("license_mint_fail");
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  },

  _openaiTts() {
    const form = document.getElementById("openai-tts-form");
    if (!form) return;
    const input = document.getElementById("openai-tts-key");
    const err = document.getElementById("openai-tts-error");
    const ok = document.getElementById("openai-tts-ok");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (err) err.textContent = "";
      try {
        const data = new FormData();
        data.set("key", (input && input.value) || "");
        const res = await fetch("/api/openai-tts-key", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("openai_tts_key_invalid")));
        if (ok) {
          ok.hidden = !body.has_openai_tts_key;
          ok.textContent = body.has_openai_tts_key ? _("openai_tts_key_ok") : "";
        }
        if (input) input.value = "";
      } catch (ex) {
        if (err) err.textContent = ex.message || _("openai_tts_key_invalid");
      }
    });
  },

  _machineCopy() {
    const btn = document.getElementById("license-machine-copy");
    const code = document.getElementById("license-machine-code");
    if (!btn || !code) return;
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText((code.textContent || "").trim());
        btn.textContent = _("copied");
        setTimeout(() => { btn.textContent = _("copy"); }, 1400);
      } catch (_) { /* ignore */ }
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
  const nav = document.getElementById("site-nav") || document.getElementById("studio-rail");
  const scrim = document.getElementById("studio-scrim");
  if (toggle && nav) {
    const desktopMq = document.body.classList.contains("studio")
      ? "(min-width: 1101px)"
      : "(min-width: 761px)";
    const setOpen = (open) => {
      document.body.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (scrim) scrim.hidden = !open;
    };
    toggle.addEventListener("click", () => {
      setOpen(!document.body.classList.contains("nav-open"));
    });
    if (scrim) {
      scrim.addEventListener("click", () => setOpen(false));
    }
    nav.querySelectorAll("a, button.nav-link").forEach((el) => {
      el.addEventListener("click", () => setOpen(false));
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setOpen(false);
    });
    window.addEventListener("resize", () => {
      if (window.matchMedia(desktopMq).matches) setOpen(false);
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
    this._hasOpenaiTts = !!window.DYFLOW_HAS_OPENAI_TTS;
    this._audio = null;
    this._quoteTimer = null;
    this._quoteSeq = 0;
    this._quoteAbort = null;
    this._filePicks = [];
    this._folderPicks = [];
    this._liveTimer = null;
    this._cancelling = false;
    this._lastQuoteUrl = "";
    this._lastQuoteItems = [];
    this._lastQuoteStatus = "";
    this._lastQuoteTotal = "";
    this._quoteError = "";
    this._quoteBusy = false;
    this._quoteBusyMsg = "";
    this._quoteClock = null;
    this._quoteStarted = 0;
    this._jobsBusy = false;
    this._renderVoices();
    this._renderTts();
    this._bindOpenaiTts();
    this._bindStudio();
    this._pollLiveLog();
    this._liveTimer = setInterval(() => this._pollLiveLog(), 2000);
    if (langSel) {
      langSel.addEventListener("change", () => this._renderVoices());
    }
    if (urlInput) {
      urlInput.addEventListener("input", () => {
        this._syncChannelOpts();
        this._scheduleQuote();
      });
      urlInput.addEventListener("paste", () => {
        setTimeout(() => {
          this._syncChannelOpts();
          this._scheduleQuote(true);
        }, 50);
      });
      // Neu da co text (browser restore) -> quote 1 lan
      this._syncChannelOpts();
      if ((urlInput.value || "").trim()) this._scheduleQuote(true);
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      const urlVal = ((document.getElementById("url") || {}).value || "").trim();
      const locals = this._localFiles();
      if (!urlVal && !locals.length) {
        err.textContent = _("home_src_needed");
        const u = document.getElementById("url");
        if (u) u.focus();
        return;
      }
      const langVal = ((document.getElementById("lang") || {}).value || "").trim();
      if (this._quoteBusy) {
        err.textContent = _("quote_channel");
        return;
      }
      if (!langVal) {
        err.textContent = _("home_lang_needed");
        const l = document.getElementById("lang");
        if (l) l.focus();
        return;
      }
      const voiceVal = (document.querySelector('input[name="voice"]:checked') || {}).value;
      if (!voiceVal) {
        err.textContent = _("home_voice_needed");
        return;
      }
      const ttsVal = (document.querySelector('input[name="tts"]:checked') || {}).value;
      if (!ttsVal) {
        this._showTtsError(_("home_tts_needed"));
        return;
      }
      this._showTtsError("");
      btn.disabled = true;
      btn.textContent = _("sending");
      const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
      const hangTimer = setTimeout(() => {
        if (ac) ac.abort();
        }, 120000);
      try {
        // Khong dung FormData(form): WebView2 Windows gui file rong + mat quoted_json
        // -> server 500 HTML -> UI chi hien "Could not create job".
        const data = new FormData();
        if (urlVal) data.set("url", urlVal);
        data.set("voice", voiceVal);
        data.set("lang", langVal);
        data.set("tts", ttsVal);
        const preset = document.getElementById("preset_name");
        const speed = document.getElementById("video_speed");
        const logoPos = document.getElementById("logo_position");
        const logoSize = document.getElementById("logo_size_percent");
        if (preset) data.set("preset_name", preset.value || "classic");
        if (speed) data.set("video_speed", speed.value || "1.0");
        if (logoPos) data.set("logo_position", logoPos.value || "top_right");
        if (logoSize) data.set("logo_size_percent", logoSize.value || "10");
        const ch = this._channelFields();
        data.set("channel_mode", ch.channel_mode);
        data.set("channel_count", ch.channel_count);
        locals.forEach((f) => data.append("videos", f, f.name));
        const logoOn = document.getElementById("logo-enable");
        const logoInput = document.getElementById("logo");
        if (logoOn && logoOn.checked && logoInput && logoInput.files && logoInput.files[0]) {
          data.set("logo", logoInput.files[0], logoInput.files[0].name);
        }
        const quoted = this._lastQuoteItems || [];
        if (quoted.length) {
          data.set("quoted_json", JSON.stringify(quoted));
          data.set("quoted_urls", quoted.map((it) => it.url).filter(Boolean).join("\n"));
        }
        const res = await fetch("/api/jobs", {
          method: "POST",
          body: data,
          signal: ac ? ac.signal : undefined,
        });
        if (res.status === 402) {
          err.innerHTML = _("no_credit");
          btn.disabled = false;
          btn.textContent = _("start");
          return;
        }
        if (!res.ok) throw new Error(await _readApiError(res, _("job_create_fail")));
        const body = await res.json().catch(() => ({}));
        const ids = body.ids || (body.id ? [body.id] : []);
        if (ids.length > 1) {
          // nhieu job: ve trang chu de xem hang doi (2 luong chay song song)
          window.location.href = "/";
          return;
        }
        window.location.href = `/jobs/${ids[0] || body.id}`;
      } catch (ex) {
        const aborted = ex && (
          ex.name === "AbortError"
          || /aborted|abort|Failed to fetch|networkerror/i.test(ex.message || "")
        );
        err.textContent = aborted
          ? _("job_create_timeout")
          : (ex.message || _("job_create_fail"));
        btn.disabled = false;
        btn.textContent = _("start");
      } finally {
        clearTimeout(hangTimer);
      }
    });
  },

  _bindStudio() {
    const previewBtn = document.getElementById("preview-quote-btn");
    if (previewBtn) {
      previewBtn.addEventListener("click", () => this._scheduleQuote(true));
    }
    const urlInput = document.getElementById("url");
    if (urlInput && urlInput.getAttribute("data-batch") === "1") {
      urlInput.focus();
    }
    const logo = document.getElementById("logo");
    const logoName = document.getElementById("logo-name");
    const logoOn = document.getElementById("logo-enable");
    if (logoName && logo) {
      logoName.addEventListener("click", () => logo.click());
      logo.addEventListener("change", () => {
        const f = logo.files && logo.files[0];
        logoName.value = f ? f.name : "";
        if (f && logoOn) logoOn.checked = true;
      });
    }
    const pickFiles = document.getElementById("pick-files-btn");
    const pickFolder = document.getElementById("pick-folder-btn");
    const fileInput = document.getElementById("local-files");
    const folderInput = document.getElementById("local-folder");
    const localList = document.getElementById("local-file-list");
    const localClear = document.getElementById("local-clear-btn");
    if (pickFiles && fileInput) {
      pickFiles.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        this._filePicks = this._videoFiles(fileInput.files);
        this._syncLocalCount();
      });
    }
    if (pickFolder && folderInput) {
      pickFolder.addEventListener("click", () => folderInput.click());
      folderInput.addEventListener("change", () => {
        this._folderPicks = this._videoFiles(folderInput.files);
        this._syncLocalCount();
      });
    }
    if (localList) {
      localList.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-local-key]");
        if (!btn) return;
        ev.preventDefault();
        this._removeLocalFile(btn.getAttribute("data-local-key"));
      });
    }
    if (localClear) {
      localClear.addEventListener("click", () => this._clearLocalFiles());
    }
    const quoteList = document.getElementById("quote-list");
    if (quoteList) {
      quoteList.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-local-key]");
        if (!btn) return;
        ev.preventDefault();
        this._removeLocalFile(btn.getAttribute("data-local-key"));
      });
    }
    this._bindChannelOpts();
    const cancelBtn = document.getElementById("cancel-btn");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", () => this._cancelActive());
    }
    const live = document.getElementById("live-log");
    if (live) {
      live.addEventListener("click", (ev) => {
        const b = ev.target.closest("[data-cancel-id]");
        if (!b) return;
        ev.preventDefault();
        this._cancelOne(b.getAttribute("data-cancel-id"));
      });
    }
  },

  async _cancelActive() {
    const btn = document.getElementById("cancel-btn");
    if (this._cancelling) return;
    this._cancelling = true;
    if (btn) {
      btn.disabled = true;
      btn.textContent = _("cancelling");
    }
    try {
      const res = await fetch("/api/jobs/cancel-active", { method: "POST" });
      if (!res.ok) throw new Error(_("cancel_fail"));
      await this._pollLiveLog();
    } catch (ex) {
      const err = document.getElementById("form-error");
      if (err) err.textContent = ex.message || _("cancel_fail");
    } finally {
      this._cancelling = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = _("cancel");
      }
    }
  },

  async _cancelOne(jobId) {
    if (!jobId) return;
    try {
      const res = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
      if (!res.ok) throw new Error(_("cancel_fail"));
      await this._pollLiveLog();
    } catch (ex) {
      const err = document.getElementById("form-error");
      if (err) err.textContent = ex.message || _("cancel_fail");
    }
  },

  _isChannelText(text) {
    return /douyin\.com\/(user|channel|share\/user)\//i.test(text || "")
      || /iesdouyin\.com\/share\/user\//i.test(text || "");
  },

  _syncChannelOpts() {
    const box = document.getElementById("channel-opts");
    const urlInput = document.getElementById("url");
    if (!box) return;
    box.hidden = !this._isChannelText((urlInput && urlInput.value) || "");
    this._syncChannelCountEnabled();
  },

  _syncChannelCountEnabled() {
    const count = document.getElementById("channel-count");
    const full = document.querySelector('input[name="channel_mode"][value="full"]');
    if (!count) return;
    count.disabled = !!(full && full.checked);
  },

  _bindChannelOpts() {
    const box = document.getElementById("channel-opts");
    if (!box) return;
    box.querySelectorAll('input[name="channel_mode"]').forEach((el) => {
      el.addEventListener("change", () => {
        this._syncChannelCountEnabled();
        this._scheduleQuote(true);
      });
    });
    const count = document.getElementById("channel-count");
    if (count) {
      count.addEventListener("focus", () => {
        const r = document.querySelector('input[name="channel_mode"][value="count"]');
        if (r) r.checked = true;
        this._syncChannelCountEnabled();
      });
      count.addEventListener("change", () => this._scheduleQuote(true));
      count.addEventListener("input", () => {
        const max = Number(window.DYFLOW_CHANNEL_MAX || count.max || 200);
        let n = parseInt(count.value, 10);
        if (!Number.isFinite(n)) return;
        if (n < 1) n = 1;
        if (n > max) n = max;
        if (String(n) !== count.value) count.value = String(n);
      });
    }
  },

  _channelFields() {
    const modeEl = document.querySelector('input[name="channel_mode"]:checked');
    const countEl = document.getElementById("channel-count");
    return {
      channel_mode: (modeEl && modeEl.value) || "count",
      channel_count: (countEl && countEl.value) || "10",
    };
  },

  _videoFiles(list) {
    return Array.from(list || []).filter((f) =>
      /\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(f.name || "")
    );
  },

  _fileKey(f) {
    return `${f.name}:${f.size}:${f.lastModified}`;
  },

  _fileLabel(f) {
    return (f.webkitRelativePath || f.name || "").trim() || f.name;
  },

  _fileSizeLabel(f) {
    const n = Number(f.size) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  },

  _escHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },

  _localFiles() {
    const seen = new Set();
    const out = [];
    (this._filePicks || []).concat(this._folderPicks || []).forEach((f) => {
      const key = this._fileKey(f);
      if (seen.has(key)) return;
      seen.add(key);
      out.push(f);
    });
    return out;
  },

  _removeLocalFile(key) {
    if (!key) return;
    this._filePicks = (this._filePicks || []).filter((f) => this._fileKey(f) !== key);
    this._folderPicks = (this._folderPicks || []).filter((f) => this._fileKey(f) !== key);
    if (!this._localFiles().length) this._resetLocalInputs();
    this._syncLocalCount();
  },

  _clearLocalFiles() {
    this._filePicks = [];
    this._folderPicks = [];
    this._resetLocalInputs();
    this._syncLocalCount();
  },

  _resetLocalInputs() {
    const fileInput = document.getElementById("local-files");
    const folderInput = document.getElementById("local-folder");
    if (fileInput) fileInput.value = "";
    if (folderInput) folderInput.value = "";
  },

  _localFileRowHtml(f, idx) {
    const name = this._escHtml(this._fileLabel(f));
    const size = this._escHtml(this._fileSizeLabel(f));
    const key = this._escHtml(this._fileKey(f));
    const n = idx + 1;
    return (
      `<li>` +
      `<span class="quote-idx">${n}</span>` +
      `<span class="quote-url" title="${name}">${name}</span>` +
      `<span class="quote-meta"><em>${size}</em>` +
      `<button type="button" class="local-file-x" data-local-key="${key}" title="${_("studio_local_remove")}" aria-label="${_("studio_local_remove")}">×</button>` +
      `</span>` +
      `</li>`
    );
  },

  _syncLocalCount() {
    const el = document.getElementById("local-count");
    const list = document.getElementById("local-file-list");
    const clearBtn = document.getElementById("local-clear-btn");
    const files = this._localFiles();
    const n = files.length;
    if (el) el.textContent = n ? _("studio_local_n").replace("{n}", String(n)) : "";
    if (clearBtn) clearBtn.hidden = !n;
    if (list) {
      list.hidden = !n;
      list.innerHTML = n
        ? files
            .map((f) => {
              const name = this._escHtml(this._fileLabel(f));
              const size = this._escHtml(this._fileSizeLabel(f));
              const key = this._escHtml(this._fileKey(f));
              return (
                `<li>` +
                `<span class="local-file-name" title="${name}">${name}</span>` +
                `<span class="local-file-size">${size}</span>` +
                `<button type="button" class="local-file-x" data-local-key="${key}" title="${_("studio_local_remove")}" aria-label="${_("studio_local_remove")}">×</button>` +
                `</li>`
              );
            })
            .join("")
        : "";
    }
    if (!this._quoteBusy) this._fillQuoteBox();
  },

  _fillQuoteBox() {
    const box = document.getElementById("quote-box");
    const list = document.getElementById("quote-list");
    const statusEl = document.getElementById("quote-status");
    const totalEl = document.getElementById("quote-total");
    if (!box || !list) return;
    const files = this._localFiles();
    const urls = this._lastQuoteItems || [];
    if (this._quoteBusy) return;
    if (!urls.length && !files.length) {
      if (this._quoteError) {
        box.hidden = false;
        list.innerHTML = "";
        if (statusEl) statusEl.textContent = this._quoteError;
        if (totalEl) totalEl.textContent = "";
      } else {
        box.hidden = true;
        list.innerHTML = "";
        if (totalEl) totalEl.textContent = "";
      }
      return;
    }
    box.hidden = false;
    const urlHtml = urls
      .map((it, idx) => {
        const dur = it.unknown
          ? _("quote_unknown_dur")
          : it.duration_label || "—";
        const cred = _("quote_line_credits").replace("{n}", String(it.credits));
        const short = this._escHtml((it.url || "").replace(/^https?:\/\//, ""));
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
    const localHtml = files
      .map((f, i) => this._localFileRowHtml(f, urls.length + i))
      .join("");
    list.innerHTML = urlHtml + localHtml;
    if (statusEl) {
      if (this._quoteError) statusEl.textContent = this._quoteError;
      else if (files.length && !urls.length) {
        statusEl.textContent = _("studio_local_files").replace("{n}", String(files.length));
      } else {
        statusEl.textContent = this._lastQuoteStatus || _("quote_title");
      }
    }
    if (totalEl) {
      if (urls.length && this._lastQuoteTotal) totalEl.textContent = this._lastQuoteTotal;
      else if (files.length) totalEl.textContent = _("studio_local_n").replace("{n}", String(files.length));
      else totalEl.textContent = "";
    }
  },

  _shortSrc(url) {
    const s = url || "";
    if (!s) return "";
    if (/^[A-Za-z]:[\\/]/.test(s) || s.startsWith("/") || s.startsWith("\\\\")) {
      const parts = s.replace(/\\/g, "/").split("/");
      return parts[parts.length - 1] || s;
    }
    return s.replace(/^https?:\/\//, "");
  },

  async _pollLiveLog() {
    const el = document.getElementById("live-log");
    if (!el) return;
    try {
      const res = await fetch("/api/jobs");
      if (!res.ok) return;
      const body = await res.json();
      this._renderLive(body.jobs || []);
    } catch (_) { /* ignore */ }
  },

  _renderLive(jobs) {
    const el = document.getElementById("live-log");
    if (!el) return;
    const active = (jobs || []).filter(
      (j) => j.status === "queued" || j.status === "running"
    );
    const cancelBtn = document.getElementById("cancel-btn");
    if (cancelBtn && !this._cancelling) cancelBtn.hidden = !active.length;
    this._jobsBusy = !!active.length;
    this._syncStudioBusy();
    if (!active.length) {
      el.innerHTML = `<li class="empty">${_("studio_log_empty")}</li>`;
      return;
    }
    el.innerHTML = active
      .map((j) => {
        const pct = Math.max(0, Math.min(100, Number(j.progress) || 0));
        const label =
          j.status === "queued"
            ? _("status_queued")
            : j.stage_label || _("status_running");
        const extra = j.status === "running" && pct ? ` · ${pct}%` : "";
        const src = this._shortSrc(j.source_url);
        const msg = j.message || "";
        return (
          `<li>` +
          `<div class="live-row">` +
          `<span class="live-row-title">` +
          (j.status === "running" ? `<i class="quote-spin"></i>` : "") +
          `<strong>${label}${extra}</strong>` +
          `</span>` +
          `<button type="button" class="btn ghost danger btn-xs" data-cancel-id="${j.id}">${_("cancel")}</button>` +
          `</div>` +
          (src ? `<span>${src}</span>` : "") +
          (msg ? `<span>${msg}</span>` : "") +
          `<span class="live-bar"><i style="width:${pct}%"></i></span>` +
          `</li>`
        );
      })
      .join("");
  },

  _scheduleQuote(immediate) {
    clearTimeout(this._quoteTimer);
    const wait = immediate ? 200 : 700;
    this._quoteTimer = setTimeout(() => this._fetchQuote(), wait);
  },

  _syncStudioBusy() {
    const quoting = !!this._quoteBusy;
    const jobs = !!this._jobsBusy;
    const frame = document.getElementById("studio-preview-frame");
    const spin = document.getElementById("studio-preview-spin");
    const text = document.getElementById("studio-preview-text");
    if (spin) spin.hidden = !(quoting || jobs);
    if (frame) frame.classList.toggle("is-busy", quoting || jobs);
    if (text) {
      text.textContent = quoting
        ? ""
        : jobs
          ? _("job_processing")
          : _("studio_preview_empty");
    }
  },

  _setQuoteLoading(on, msg) {
    const box = document.getElementById("quote-box");
    const spin = document.getElementById("quote-spin");
    const list = document.getElementById("quote-list");
    const prev = document.getElementById("preview-quote-btn");
    const statusEl = document.getElementById("quote-status");
    this._quoteBusy = !!on;
    this._quoteBusyMsg = msg || "";
    if (box) box.classList.toggle("is-loading", !!on);
    if (spin) spin.hidden = !on;
    if (prev) prev.disabled = !!on;
    if (on && list) list.innerHTML = "";
    clearInterval(this._quoteClock);
    this._quoteClock = null;
    if (on) {
      this._quoteStarted = Date.now();
      const tick = () => {
        const sec = Math.max(0, Math.round((Date.now() - this._quoteStarted) / 1000));
        const line = `${msg || _("quote_checking")} · ${sec}s`;
        this._quoteBusyMsg = line;
        if (statusEl) statusEl.textContent = line;
        this._syncStudioBusy();
      };
      tick();
      this._quoteClock = setInterval(tick, 1000);
    }
    this._syncStudioBusy();
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
      this._lastQuoteUrl = "";
      this._lastQuoteItems = [];
      this._lastQuoteStatus = "";
      this._lastQuoteTotal = "";
      this._quoteError = "";
      this._setQuoteLoading(false);
      this._fillQuoteBox();
      return;
    }

    const seq = ++this._quoteSeq;
    if (this._quoteAbort) {
      try { this._quoteAbort.abort(); } catch (_) { /* ignore */ }
    }
    this._quoteAbort = typeof AbortController !== "undefined" ? new AbortController() : null;
    const quoteSignal = this._quoteAbort ? this._quoteAbort.signal : undefined;
    box.hidden = false;
    this._quoteError = "";
    const waitMsg = this._isChannelText(text)
      ? _("quote_channel")
      : _("quote_checking");
    this._setQuoteLoading(true, waitMsg);
    if (totalEl) totalEl.textContent = "…";

    // Channel list must not spin for minutes if Edge hangs.
    let quoteTimer = null;
    if (this._quoteAbort && this._isChannelText(text)) {
      quoteTimer = setTimeout(() => {
        try { this._quoteAbort.abort(); } catch (_) { /* ignore */ }
      }, 45000);
    }

    try {
      const data = new FormData();
      data.set("url", text);
      const ch = this._channelFields();
      data.set("channel_mode", ch.channel_mode);
      data.set("channel_count", ch.channel_count);
      const res = await fetch("/api/quote", {
        method: "POST",
        body: data,
        signal: quoteSignal,
      });
      const body = await res.json().catch(() => ({}));
      if (seq !== this._quoteSeq) return;
      this._setQuoteLoading(false);
      if (!res.ok) throw new Error(_apiDetail(body, _("quote_fail")));

      const items = body.items || [];
      this._lastQuoteUrl = items.length ? text : "";
      this._lastQuoteItems = items;
      this._quoteError = "";
      if (body.channel_incomplete) {
        this._lastQuoteStatus = items.length > 1
          ? _("quote_channel_short")
              .replace("{n}", String(items.length))
          : _("quote_channel_one");
      } else if (ch.channel_mode === "full" && items.length) {
        this._lastQuoteStatus = _("quote_channel_n").replace("{n}", String(items.length));
      } else {
        this._lastQuoteStatus = _("quote_title");
      }
      this._lastQuoteTotal = items.length
        ? _("quote_total")
            .replace("{n}", String(body.total_credits || 0))
            .replace("{m}", String(body.count || items.length))
        : "";
      this._fillQuoteBox();
    } catch (ex) {
      if (seq !== this._quoteSeq) return;
      this._setQuoteLoading(false);
      this._lastQuoteUrl = "";
      this._lastQuoteItems = [];
      this._lastQuoteStatus = "";
      this._lastQuoteTotal = "";
      const aborted = ex && (ex.name === "AbortError" || /aborted/i.test(String(ex.message || "")));
      this._quoteError = aborted
        ? (_("quote_channel_timeout") || "Channel listing timed out (~45s). Close Edge/Chrome, then Preview again.")
        : (ex.message || _("quote_fail"));
      this._fillQuoteBox();
    } finally {
      if (quoteTimer) clearTimeout(quoteTimer);
    }
  },

  _currentLangMeta() {
    const langId = ((document.getElementById("lang") || {}).value || "").trim();
    if (!langId) return null;
    return (this._languages || []).find((l) => l.id === langId) || null;
  },

  _voiceCard(v, checked) {
    const label = v.label_key ? _(v.label_key, v.label) : (v.label || v.id);
    const el = document.createElement("label");
    el.className = "voice-opt";
    el.innerHTML = `
      <input type="radio" name="voice" value="${v.id}" ${checked ? "checked" : ""} />
      <span class="voice-card">
        <span class="voice-copy">
          <strong>${label}</strong>
        </span>
        <span class="voice-gender">${v.gender === "female" ? "♀" : "♂"}</span>
        <button type="button" class="voice-play" data-voice="${v.id}" title="${_("preview")}" aria-label="${_("preview")}">▶</button>
      </span>`;
    return el;
  },

  _renderVoices() {
    const wrap = document.getElementById("voice-options");
    const hint = document.getElementById("voice-hint");
    if (!wrap) return;
    const meta = this._currentLangMeta();
    const voices = (meta && meta.voices) || [];
    if (hint) {
      const tpl = (window.DYFLOW_I18N || {}).voice_count;
      hint.textContent = tpl || _("voice_count", `${voices.length} voices`);
    }
    const prev = (wrap.querySelector('input[name="voice"]:checked') || {}).value;
    wrap.innerHTML = "";
    if (!meta) {
      wrap.innerHTML = `<p class="hint left">${_("home_lang_needed")}</p>`;
      return;
    }
    const groups = [
      { gender: "male", title: _("voice_male") },
      { gender: "female", title: _("voice_female") },
    ];
    groups.forEach((g) => {
      const items = voices.filter((v) => v.gender === g.gender);
      if (!items.length) return;
      const block = document.createElement("div");
      block.className = "voice-group";
      const title = document.createElement("p");
      title.className = "voice-group-label";
      title.textContent = g.title;
      block.appendChild(title);
      const row = document.createElement("div");
      row.className = "voices-row";
      items.forEach((v) => {
        row.appendChild(this._voiceCard(v, prev === v.id));
      });
      block.appendChild(row);
      wrap.appendChild(block);
    });
    wrap.querySelectorAll(".voice-play").forEach((b) => {
      b.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        this._preview(b.dataset.voice, b);
      });
    });
  },

  _bindOpenaiTts() {
    const btn = document.getElementById("home-openai-tts-save");
    const input = document.getElementById("home-openai-tts-key");
    const status = document.getElementById("home-openai-tts-status");
    if (!btn || !input) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const data = new FormData();
        data.set("key", input.value || "");
        const res = await fetch("/api/openai-tts-key", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("openai_tts_key_invalid")));
        this._hasOpenaiTts = !!body.has_openai_tts_key;
        window.DYFLOW_HAS_OPENAI_TTS = this._hasOpenaiTts;
        if (status) {
          status.textContent = this._hasOpenaiTts
            ? _("openai_tts_key_ok")
            : _("openai_tts_key_needed");
        }
        input.value = "";
        this._renderTts();
      } catch (ex) {
        if (status) status.textContent = ex.message || _("openai_tts_key_invalid");
      } finally {
        btn.disabled = false;
      }
    });
  },

  _showTtsError(msg) {
    const box = document.getElementById("tts-error");
    const field = document.getElementById("tts-field");
    if (box) {
      box.hidden = !msg;
      box.textContent = msg || "";
    }
    if (field) field.classList.toggle("is-invalid", !!msg);
    if (msg && field) {
      field.scrollIntoView({ behavior: "smooth", block: "center" });
    }
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
      const needs = !!t.needs_openai_key;
      const locked = needs && !this._hasOpenaiTts;
      const checked = prev === t.id && !locked;
      const el = document.createElement("label");
      el.className = "voice-opt" + (locked ? " is-locked" : "");
      el.innerHTML = `
        <input type="radio" name="tts" value="${t.id}" ${checked ? "checked" : ""} ${locked ? "disabled" : ""} />
        <span class="voice-card">
          <strong>${t.label}</strong>
          ${locked ? `<em class="tts-lock">${_("openai_tts_locked")}</em>` : ""}
          <button type="button" class="voice-play" data-tts="${t.id}" title="${_("preview")}" aria-label="${_("preview")}" ${locked ? "disabled" : ""}>▶</button>
        </span>`;
      wrap.appendChild(el);
    });
    if (!wrap.querySelector('input[name="tts"]:checked')) {
      const edge = wrap.querySelector('input[name="tts"][value="edge"]');
      if (edge) edge.checked = true;
    }
    wrap.querySelectorAll('input[name="tts"]').forEach((inp) => {
      inp.addEventListener("change", () => this._showTtsError(""));
    });
    wrap.querySelectorAll(".voice-play").forEach((b) => {
      b.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const voiceEl = document.querySelector('input[name="voice"]:checked');
        const voiceId = (voiceEl && voiceEl.value) || "male_adult";
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
    this._bindCancel();
    this.connect();
  },

  _bindCancel() {
    const btn = document.getElementById("cancel-job-btn");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = _("cancelling");
      try {
        const res = await fetch(`/api/jobs/${this.jobId}/cancel`, { method: "POST" });
        if (!res.ok) throw new Error(_("cancel_fail"));
      } catch (ex) {
        if (this.errEl) {
          this.errEl.hidden = false;
          this.errEl.textContent = ex.message || _("cancel_fail");
        }
        btn.disabled = false;
        btn.textContent = _("cancel");
      }
    });
  },

  connect() {
    const src = new EventSource(`/api/jobs/${this.jobId}/events`);
    src.onmessage = (ev) => {
      const job = JSON.parse(ev.data);
      this.render(job);
      if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
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
      if (job.status !== "completed" && job.status !== "failed" && job.status !== "cancelled") {
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

    const cancelBtn = document.getElementById("cancel-job-btn");
    if (cancelBtn) {
      const live = job.status === "queued" || job.status === "running";
      cancelBtn.hidden = !live;
    }

    if (job.status === "cancelled") {
      this.msg.textContent = _("job_cancelled");
      if (ph) ph.textContent = _("status_cancelled");
      const spin = document.querySelector(".spinner");
      if (spin) spin.style.display = "none";
      this._showEditorActions(false);
    }

    if (job.status === "failed") {
      this.errEl.hidden = false;
      this.errEl.textContent = job.error || _("job_fail_msg");
      if (ph) ph.textContent = _("job_failed");
      const spin = document.querySelector(".spinner");
      if (spin) spin.style.display = "none";
      this._showEditorActions(false);
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
    const QA_ALERT = new Set(["chua_dich", "qua_dai", "qua_nhanh"]);
    const flagLabel = (f) => {
      if (String(f).startsWith("shortened_x")) return _("qa_flag_shortened") || f;
      return _("qa_flag_" + f) || f;
    };
    list.innerHTML = rows
      .map((r) => {
        const rawFlags = r.flags || [];
        const flags = rawFlags.map(flagLabel).join(", ") || _("qa_ok");
        const cls = rawFlags.some((f) => QA_ALERT.has(f)) ? "qa-bad" : "qa-ok";
        const txt = (r.text || "").replace(/</g, "&lt;");
        return `<li class="${cls}"><span class="qa-meta">#${r.i + 1} · ${r.speed}x · ${flags}</span><span class="qa-text">${txt}</span></li>`;
      })
      .join("");
    box.hidden = false;
  },

  _showEditorActions(hasDownload) {
    const actions = document.getElementById("result-actions");
    const dl = document.getElementById("download-btn");
    if (actions) actions.hidden = false;
    if (dl) dl.hidden = !hasDownload;
  },

  showResult() {
    const wrap = document.getElementById("video-wrap");
    const playUrl = `/api/jobs/${this.jobId}/download`;
    const dlUrl = `/api/jobs/${this.jobId}/download?dl=1`;
    wrap.innerHTML = `<video controls autoplay muted playsinline src="${playUrl}"></video>`;
    this._showEditorActions(true);
    const btn = document.getElementById("download-btn");
    if (btn) {
      btn.href = dlUrl;
      btn.onclick = (ev) => {
        if (!window.DYFLOW_DESKTOP) return;
        ev.preventDefault();
        this._saveLocal();
      };
    }
    const note = document.getElementById("auto-dl-note");
    if (note) note.hidden = false;
    this._autoDownload(dlUrl);
  },

  async _saveLocal() {
    const note = document.getElementById("auto-dl-note");
    try {
      const res = await fetch(`/api/jobs/${this.jobId}/export-local`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.detail || _("save_download_fail"));
      }
      const msg = (_("saved_to_downloads") || "Saved: {path}").replace("{path}", data.path || "");
      if (note) {
        note.hidden = false;
        note.textContent = msg;
      }
    } catch (e) {
      if (note) {
        note.hidden = false;
        note.textContent = _("save_download_fail");
      }
    }
  },

  _autoDownload(url) {
    // pywebview khong co thanh Downloads — copy file thay vi <a download>.
    if (this._autoDownloaded) return;
    this._autoDownloaded = true;
    if (window.DYFLOW_DESKTOP) {
      this._saveLocal();
      return;
    }
    try {
      const a = document.createElement("a");
      a.href = url;
      a.download = `videoscreator_${this.jobId.slice(0, 12)}.mp4`;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (_) {
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

const DyflowQueue = {
  init() {
    if (!document.getElementById("queue-page")) return;
    this._cancelling = false;
    this._poll();
    this._timer = setInterval(() => this._poll(), 2000);
    const cancelAll = document.getElementById("cancel-btn");
    if (cancelAll) {
      cancelAll.addEventListener("click", () => this._cancelActive());
    }
    const live = document.getElementById("live-log");
    if (live) {
      live.addEventListener("click", (ev) => {
        const b = ev.target.closest("[data-cancel-id]");
        if (!b) return;
        ev.preventDefault();
        this._cancelOne(b.getAttribute("data-cancel-id"));
      });
    }
  },

  _shortSrc(url) {
    const s = url || "";
    if (!s) return "";
    if (/^[A-Za-z]:[\\/]/.test(s) || s.startsWith("/") || s.startsWith("\\\\")) {
      const parts = s.replace(/\\/g, "/").split("/");
      return parts[parts.length - 1] || s;
    }
    return s.replace(/^https?:\/\//, "");
  },

  async _poll() {
    try {
      const res = await fetch("/api/jobs");
      if (!res.ok) return;
      const body = await res.json();
      this._render(body.jobs || []);
    } catch (_) { /* ignore */ }
  },

  _render(jobs) {
    const live = document.getElementById("live-log");
    const recent = document.getElementById("queue-recent");
    const cancelBtn = document.getElementById("cancel-btn");
    const list = jobs || [];
    const active = list.filter((j) => j.status === "queued" || j.status === "running");
    const done = list.filter(
      (j) => j.status === "failed" || j.status === "cancelled"
    );
    if (cancelBtn && !this._cancelling) cancelBtn.hidden = !active.length;

    if (live) {
      if (!active.length) {
        live.innerHTML = `<li class="empty">${_("queue_empty")}</li>`;
      } else {
        live.innerHTML = active.map((j) => this._activeRow(j)).join("");
      }
    }
    if (recent) {
      if (!done.length) {
        recent.innerHTML = `<li class="empty">${_("queue_recent_empty")}</li>`;
      } else {
        recent.innerHTML = done.map((j) => this._recentRow(j)).join("");
      }
    }
  },

  _activeRow(j) {
    const pct = Math.max(0, Math.min(100, Number(j.progress) || 0));
    const label =
      j.status === "queued"
        ? _("status_queued")
        : j.stage_label || _("status_running");
    const extra = j.status === "running" && pct ? ` · ${pct}%` : "";
    const src = this._shortSrc(j.source_url);
    const msg = j.message || "";
    return (
      `<li>` +
      `<div class="live-row">` +
      `<span class="live-row-title">` +
      (j.status === "running" ? `<i class="quote-spin"></i>` : "") +
      `<strong>${label}${extra}</strong>` +
      `</span>` +
      `<button type="button" class="btn ghost danger btn-xs" data-cancel-id="${j.id}">${_("cancel")}</button>` +
      `</div>` +
      (src ? `<span>${src}</span>` : "") +
      (msg ? `<span>${msg}</span>` : "") +
      `<span class="live-bar"><i style="width:${pct}%"></i></span>` +
      `</li>`
    );
  },

  _recentRow(j) {
    const src = this._shortSrc(j.source_url);
    const label =
      j.status === "cancelled" ? _("status_cancelled") : _("status_failed");
    const msg = j.error || j.message || "";
    return (
      `<li>` +
      `<div class="live-row">` +
      `<span class="live-row-title"><strong>${label}</strong></span>` +
      `<a class="btn ghost btn-xs" href="/jobs/${j.id}">${_("queue_open")}</a>` +
      `</div>` +
      (src ? `<span>${src}</span>` : "") +
      (msg ? `<span>${msg}</span>` : "") +
      `</li>`
    );
  },

  async _cancelActive() {
    const btn = document.getElementById("cancel-btn");
    if (this._cancelling) return;
    this._cancelling = true;
    if (btn) {
      btn.disabled = true;
      btn.textContent = _("cancelling");
    }
    try {
      const res = await fetch("/api/jobs/cancel-active", { method: "POST" });
      if (!res.ok) throw new Error(_("cancel_fail"));
      await this._poll();
    } catch (ex) {
      const err = document.getElementById("form-error");
      if (err) err.textContent = ex.message || _("cancel_fail");
    } finally {
      this._cancelling = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = _("queue_cancel_all") || _("cancel");
      }
    }
  },

  async _cancelOne(jobId) {
    if (!jobId) return;
    try {
      const res = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
      if (!res.ok) throw new Error(_("cancel_fail"));
      await this._poll();
    } catch (ex) {
      const err = document.getElementById("form-error");
      if (err) err.textContent = ex.message || _("cancel_fail");
    }
  },
};
