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
    const msg = d.detail || d.msg || d.message || d.error;
    if (typeof msg === "string" && msg.trim()) return msg;
  }
  return fallback;
}

function _apiErrorMeta(body) {
  const d = body && body.detail;
  if (d && typeof d === "object" && !Array.isArray(d)) {
    return {
      error_code: d.error_code || "",
      offer_chrome: !!d.offer_chrome,
      detail: d.detail || d.message || "",
    };
  }
  return { error_code: "", offer_chrome: false, detail: "" };
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

function _escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
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
    this._deepseekKey();
    this._qwenKey();
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
          const roleLabel = body.role
            ? (_("license_role_" + body.role) || body.role)
            : "";
          const msg = _("license_mint_ok")
            .replace("{role}", roleLabel)
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
          const roleLabel = body.role
            ? (_("license_role_" + body.role) || body.role)
            : "";
          ok.textContent = _("license_mint_ok")
            .replace("{role}", roleLabel)
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
    const btn = document.getElementById("openai-tts-btn");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (err) err.textContent = "";
      if (ok) { ok.hidden = true; ok.textContent = ""; }
      if (btn) btn.disabled = true;
      if (err) err.textContent = _("openai_tts_key_checking");
      try {
        const data = new FormData();
        data.set("key", (input && input.value) || "");
        const res = await fetch("/api/openai-tts-key", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("openai_translate_key_invalid")));
        if (err) err.textContent = "";
        if (ok) {
          ok.hidden = !body.has_openai_tts_key;
          ok.textContent = body.has_openai_tts_key ? _("openai_tts_key_ok") : "";
        }
        if (input) input.value = "";
      } catch (ex) {
        if (ok) { ok.hidden = true; ok.textContent = ""; }
        if (err) err.textContent = ex.message || _("openai_translate_key_invalid");
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  },

  _deepseekKey() {
    const form = document.getElementById("deepseek-key-form");
    if (!form) return;
    const input = document.getElementById("deepseek-key");
    const err = document.getElementById("deepseek-key-error");
    const ok = document.getElementById("deepseek-key-ok");
    const btn = document.getElementById("deepseek-key-btn");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (err) err.textContent = "";
      if (ok) { ok.hidden = true; ok.textContent = ""; }
      if (btn) btn.disabled = true;
      if (err) err.textContent = _("deepseek_key_checking");
      try {
        const data = new FormData();
        data.set("key", (input && input.value) || "");
        const res = await fetch("/api/deepseek-key", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("deepseek_key_invalid")));
        if (err) err.textContent = "";
        if (ok) {
          ok.hidden = !body.has_deepseek_key;
          ok.textContent = body.has_deepseek_key ? _("deepseek_key_ok") : "";
        }
        if (input) input.value = "";
      } catch (ex) {
        if (ok) { ok.hidden = true; ok.textContent = ""; }
        if (err) err.textContent = ex.message || _("deepseek_key_invalid");
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  },

  _qwenKey() {
    const form = document.getElementById("qwen-key-form");
    if (!form) return;
    const input = document.getElementById("qwen-key");
    const err = document.getElementById("qwen-key-error");
    const ok = document.getElementById("qwen-key-ok");
    const btn = document.getElementById("qwen-key-btn");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (err) err.textContent = "";
      if (ok) { ok.hidden = true; ok.textContent = ""; }
      if (btn) btn.disabled = true;
      if (err) err.textContent = _("qwen_key_checking");
      try {
        const data = new FormData();
        data.set("key", (input && input.value) || "");
        const res = await fetch("/api/qwen-key", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("qwen_key_invalid")));
        if (err) err.textContent = "";
        if (ok) {
          ok.hidden = !body.has_qwen_key;
          ok.textContent = body.has_qwen_key ? _("qwen_key_ok") : "";
        }
        if (input) input.value = "";
      } catch (ex) {
        if (ok) { ok.hidden = true; ok.textContent = ""; }
        if (err) err.textContent = ex.message || _("qwen_key_invalid");
      } finally {
        if (btn) btn.disabled = false;
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

  // Global background merge progress tracker
  if (typeof DyflowMergeGlobal !== "undefined") {
    DyflowMergeGlobal.init();
  }
  if (typeof DyflowQueueGlobal !== "undefined") {
    DyflowQueueGlobal.init();
  }
});

const DyflowMergeGlobal = {
  _timer: null,

  init() {
    if (window.location.pathname.startsWith("/merge")) return;
    this._poll();
  },

  async _poll() {
    try {
      const res = await fetch("/api/merge/status");
      if (!res.ok) return;
      const data = await res.json();
      this._updateUI(data);
      if (data && data.has_task && data.status === "running") {
        if (!this._timer) {
          this._timer = setInterval(() => this._poll(), 2500);
        }
      } else {
        if (this._timer) {
          clearInterval(this._timer);
          this._timer = null;
        }
      }
    } catch (_) {}
  },

  _updateUI(data) {
    const badge = document.getElementById("rail-merge-badge");
    let pill = document.getElementById("global-merge-pill");

    if (!data || !data.has_task || data.status === "cancelled") {
      if (badge) badge.hidden = true;
      if (pill) pill.hidden = true;
      return;
    }

    if (data.status === "running") {
      const pct = Math.max(5, Math.min(99, Math.round(data.progress || 10)));
      if (badge) {
        badge.hidden = false;
        badge.textContent = `${pct}%`;
        badge.style.background = "#fe2c55";
      }
      if (!pill) {
        pill = document.createElement("div");
        pill.id = "global-merge-pill";
        pill.style.cssText =
          "position: fixed; bottom: 20px; right: 24px; z-index: 9999; " +
          "background: rgba(18, 22, 34, 0.95); border: 1px solid rgba(254, 44, 85, 0.5); " +
          "box-shadow: 0 8px 32px rgba(0,0,0,0.45); border-radius: 12px; padding: 10px 14px; " +
          "display: flex; align-items: center; gap: 12px; color: #fff; font-size: 13px; backdrop-filter: blur(8px);";
        document.body.appendChild(pill);
      }
      pill.hidden = false;
      pill.innerHTML = `
        <span style="font-size: 16px;">🎬</span>
        <div>
          <div style="font-weight: 600; font-size: 13px; color: #fff;">Đang ghép video (${pct}%)</div>
          <div style="font-size: 11.5px; color: #9da2b8; max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${data.message || "Đang xử lý..."}</div>
        </div>
        <a href="/merge" style="margin-left: 6px; padding: 4px 10px; background: #fe2c55; color: #fff; text-decoration: none; border-radius: 6px; font-size: 12px; font-weight: 600;">Xem</a>
        <button type="button" style="background: none; border: none; color: #9da2b8; cursor: pointer; font-size: 16px; padding: 0 4px;" title="Đóng">×</button>
      `;
      const closeBtn = pill.querySelector("button");
      if (closeBtn) {
        closeBtn.onclick = () => { pill.hidden = true; };
      }
    } else if (data.status === "completed") {
      if (badge) {
        badge.hidden = false;
        badge.textContent = "✓";
        badge.style.background = "#22c55e";
      }
      if (pill) {
        pill.hidden = false;
        pill.innerHTML = `
          <span style="font-size: 16px;">🎉</span>
          <div>
            <div style="font-weight: 600; font-size: 13px; color: #4ade80;">Ghép video hoàn tất!</div>
            <div style="font-size: 11.5px; color: #9da2b8;">Bấm Xem để mở video</div>
          </div>
          <a href="/merge" style="margin-left: 6px; padding: 4px 10px; background: #22c55e; color: #fff; text-decoration: none; border-radius: 6px; font-size: 12px; font-weight: 600;">Xem</a>
          <button type="button" style="background: none; border: none; color: #9da2b8; cursor: pointer; font-size: 16px; padding: 0 4px;" title="Đóng">×</button>
        `;
        const closeBtn = pill.querySelector("button");
        if (closeBtn) {
          closeBtn.onclick = () => { pill.hidden = true; };
        }
      }
    } else {
      if (badge) badge.hidden = true;
      if (pill) pill.hidden = true;
    }
  },
};

const DyflowQueueGlobal = {
  _timer: null,

  init() {
    if (window.location.pathname === "/" || window.location.pathname.startsWith("/queue")) {
      return;
    }
    this._poll();
    this._timer = setInterval(() => this._poll(), 3500);
  },

  async _poll() {
    try {
      const res = await fetch("/api/jobs?active_only=1&limit=1");
      if (!res.ok) return;
      const data = await res.json();
      const badge = document.getElementById("rail-queue-badge");
      if (badge) {
        const count = data.active_count || 0;
        if (count > 0) {
          badge.hidden = false;
          badge.textContent = count > 999 ? "999+" : String(count);
        } else {
          badge.hidden = true;
        }
      }
    } catch (_) {}
  },
};

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

const DyflowSavePath = {
  init() {
    const row = document.getElementById("save-path-row");
    if (!row || row._savePathBound) return;
    row._savePathBound = true;
    row.hidden = false;
    const browse = document.getElementById("save-path-browse");
    const input = document.getElementById("save-path-input");
    if (browse) {
      if (!window.DYFLOW_DESKTOP) browse.hidden = true;
      else {
        browse.hidden = false;
        browse.addEventListener("click", (ev) => {
          ev.preventDefault();
          this.pickFolder();
        });
      }
    }
    if (input) {
      const persist = () => this.persist(input.value);
      input.addEventListener("change", persist);
      input.addEventListener("blur", persist);
    }
    this.refresh();
  },

  apply(dir) {
    const input = document.getElementById("save-path-input");
    if (!input) return;
    const d = String(dir || "").trim();
    if (!d) return;
    input.value = d;
    input.placeholder = d;
    input.title = d;
  },

  currentDir() {
    const input = document.getElementById("save-path-input");
    const dir = input && String(input.value || "").trim();
    if (
      !dir
      || dir === "Downloads"
      || dir.startsWith("Default:")
      || dir.startsWith("Mặc định:")
    ) {
      return "";
    }
    return dir;
  },

  setStatus(text, ok) {
    const el = document.getElementById("save-path-status")
      || document.getElementById("auto-dl-note");
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    el.style.color = ok === false ? "#ff8a8a" : "";
    el.style.fontWeight = ok === false || ok === true ? "600" : "";
  },

  async refresh() {
    try {
      const res = await fetch("/api/desktop/save-prefs");
      const data = await res.json().catch(() => ({}));
      this.apply(data.dir || data.default_dir || "");
    } catch (_) {
      const input = document.getElementById("save-path-input");
      if (input && !input.value) this.apply("Downloads");
    }
  },

  async persist(raw) {
    const dir = String(raw || "").trim();
    if (!dir || !window.DYFLOW_DESKTOP) return;
    if (dir === "Downloads" || dir.startsWith("Default:") || dir.startsWith("Mặc định:")) {
      return;
    }
    try {
      await fetch("/api/desktop/save-prefs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dir }),
      });
    } catch (_) {}
  },

  async pickFolder() {
    const browse = document.getElementById("save-path-browse");
    if (!window.DYFLOW_DESKTOP) {
      this.setStatus(_("save_folder_fail") || "Folder picker unavailable", false);
      return;
    }
    if (browse) browse.disabled = true;
    this.setStatus(_("save_folder_picking") || "Opening folder picker…", true);
    try {
      const res = await fetch("/api/desktop/pick-folder", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "";
        this.setStatus(detail || _("save_folder_fail"), false);
        return;
      }
      if (data.cancelled) {
        this.setStatus(_("save_download_cancelled") || "Cancelled", true);
        if (data.dir) this.apply(data.dir);
        return;
      }
      if (data.dir) {
        this.apply(data.dir);
        // Path already shown in the input — don't echo it again under the hint.
        this.setStatus("");
      } else {
        this.setStatus(_("save_folder_fail"), false);
      }
    } catch (e) {
      this.setStatus((e && e.message) || _("save_folder_fail"), false);
    } finally {
      if (browse) browse.disabled = false;
    }
  },
};
window.DyflowSavePath = DyflowSavePath;

const DyflowHome = {
  init() {
    const form = document.getElementById("job-form");
    if (!form) return;
    DyflowSavePath.init();
    const btn = document.getElementById("submit-btn");
    const err = document.getElementById("form-error");
    const langSel = document.getElementById("lang");
    const urlInput = document.getElementById("url");

    this._languages = window.DYFLOW_LANGUAGES || [];
    this._ttsChoices = window.DYFLOW_TTS || [];
    this._hasOpenaiTts = !!window.DYFLOW_HAS_OPENAI_TTS;
    this._hasDeepseek = !!window.DYFLOW_HAS_DEEPSEEK;
    this._hasQwen = !!window.DYFLOW_HAS_QWEN;
    this._audio = null;
    this._quoteTimer = null;
    this._quoteSeq = 0;
    this._quoteAbort = null;
    this._filePicks = [];
    this._folderPicks = [];
    this._localUnchecked = new Set();
    this._liveTimer = null;
    this._cancelling = false;
    this._lastQuoteUrl = "";
    this._lastQuoteItems = [];
    this._lastQuoteKind = "video";
    this._wantChromeFallback = false;
    this._lastBatchIds = [];
    this._lastBatchTotal = 0;
    try {
      const raw = sessionStorage.getItem("dyflow_batch");
      if (raw) {
        const bag = JSON.parse(raw);
        if (bag && Array.isArray(bag.ids) && bag.ids.length) {
          this._lastBatchIds = bag.ids;
          this._lastBatchTotal = Number(bag.total) || bag.ids.length;
        }
      }
    } catch (_) { /* ignore */ }
    this._lastErrorMeta = null;
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
    this._bindDeepseekKey();
    this._bindTranslateEngine();
    this._applyDefaultTranslateEngine();
    this._bindGenre();
    this._syncOpenaiKeyBox();
    this._bindStudio();
    this._bindAntiCopyright();
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
      // Luu thu muc truoc khi tao job — job xong se auto-export vao day.
      try {
        const input = document.getElementById("save-path-input");
        await DyflowSavePath.persist(input && input.value);
      } catch (_) {}
      const urlVal = ((document.getElementById("url") || {}).value || "").trim();
      const allLocals = this._localFiles();
      const locals = this._selectedLocalFiles();
      if (!urlVal && !allLocals.length) {
        err.textContent = _("home_src_needed");
        const u = document.getElementById("url");
        if (u) u.focus();
        return;
      }
      if (urlVal && /^https?:\/\/(www\.)?(douyin|tiktok)\.com\/?$/i.test(urlVal)) {
        err.textContent = "Vui lòng dán link video cụ thể (ví dụ https://v.douyin.com/... hoặc https://www.douyin.com/video/...) hoặc link kênh tác giả, không dán link trang chủ.";
        const u = document.getElementById("url");
        if (u) {
          u.focus();
          u.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        return;
      }
      if (allLocals.length && !locals.length && !urlVal) {
        err.textContent = _("channel_need_pick");
        err.scrollIntoView({ block: "nearest", behavior: "smooth" });
        return;
      }
      const langVal = ((document.getElementById("lang") || {}).value || "").trim();
      if (this._quoteBusy) {
        err.textContent = _("quote_channel");
        return;
      }
      const selectable = this._lastQuoteKind === "channel";
      let quotedForSubmit = this._lastQuoteItems || [];
      if (selectable) {
        quotedForSubmit = this._selectedQuoteItems();
        if (!quotedForSubmit.length && urlVal && !locals.length) {
          err.textContent = _("channel_need_pick");
          err.scrollIntoView({ block: "nearest", behavior: "smooth" });
          btn.disabled = false;
          btn.textContent = _("start");
          return;
        }
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
      const genreSel = document.getElementById("genre_id");
      let genreVal = (genreSel ? genreSel.value : "").trim();
      if (!genreVal) {
        if (genreSel && genreSel.options.length > 0) {
          for (let i = 0; i < genreSel.options.length; i++) {
            if (genreSel.options[i].value) {
              genreSel.selectedIndex = i;
              genreVal = genreSel.options[i].value;
              break;
            }
          }
        }
        if (!genreVal) genreVal = "do_thi";
      }
      let teVal = this._selectedTranslateEngine();
      if (teVal === "deepseek" && !this._hasDeepseek) {
        teVal = "google";
        const gRadio = document.querySelector('input[name="translate_engine"][value="google"]');
        if (gRadio) gRadio.checked = true;
        this._syncOpenaiKeyBox();
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
        data.set("translate_engine", teVal);
        const vmInput = document.querySelector('input[name="voice_mode"]:checked');
        data.set("voice_mode", vmInput ? vmInput.value : "single");
        const genreSel = document.getElementById("genre_id");
        const genrePrompt = document.getElementById("genre_prompt");
        const genrePanel = document.getElementById("genre-prompt-panel");
        if (genreSel && genreSel.value) data.set("genre_id", genreSel.value);
        // Chi gui prompt khi user mo tuy chinh (tranh dump mau len form mac dinh)
        if (
          genrePanel && !genrePanel.hidden
          && genrePrompt && (genrePrompt.value || "").trim()
        ) {
          data.set("genre_prompt", genrePrompt.value.trim());
        }
        const preset = document.getElementById("preset_name");
        const speed = document.getElementById("video_speed");
        const logoPos = document.getElementById("logo_position");
        const logoSize = document.getElementById("logo_size_percent");
        if (preset) data.set("preset_name", preset.value || "classic");
        if (speed) data.set("video_speed", speed.value || "1.0");
        if (logoPos) data.set("logo_position", logoPos.value || "top_right");
        if (logoSize) data.set("logo_size_percent", logoSize.value || "10");
        const savePathInput = document.getElementById("save-path-input");
        if (savePathInput && (savePathInput.value || "").trim()) {
          data.set("export_dir", savePathInput.value.trim());
        }

        const acRadio = document.querySelector('input[name="anti_copyright_mode"]:checked');
        const acMode = (acRadio && acRadio.value) || "off";
        data.set("anti_copyright_mode", acMode);
        const acMirror = document.getElementById("ac_mirror");
        const acNoise = document.getElementById("ac_noise");
        const acColor = document.getElementById("ac_color");
        const acVignette = document.getElementById("ac_vignette");
        const acBlurSub = document.getElementById("ac_blur_sub");
        const acPitch = document.getElementById("ac_pitch");
        const acZoom = document.getElementById("ac_zoom");
        if (acMirror && acMirror.checked) data.set("ac_mirror", "1");
        if (acNoise && acNoise.checked) data.set("ac_noise", "1");
        if (acColor && acColor.checked) data.set("ac_color", "1");
        if (acVignette && acVignette.checked) data.set("ac_vignette", "1");
        if (acBlurSub && acBlurSub.checked) data.set("ac_blur_sub", "1");
        const acBlurSubMode = document.getElementById("ac_blur_sub_mode");
        if (acBlurSubMode && acBlurSubMode.value) data.set("ac_blur_sub_mode", acBlurSubMode.value);
        if (acPitch && acPitch.checked) data.set("ac_pitch", "1");
        if (acZoom && acZoom.checked) data.set("ac_zoom", "1");

        const ch = this._channelFields();
        data.set("channel_mode", ch.channel_mode);
        data.set("channel_count", ch.channel_count);
        locals.forEach((f) => data.append("videos", f, f.name));
        const logoOn = document.getElementById("logo-enable");
        const logoInput = document.getElementById("logo");
        if (logoOn && logoOn.checked && logoInput && logoInput.files && logoInput.files[0]) {
          data.set("logo", logoInput.files[0], logoInput.files[0].name);
        }
        const quoted = quotedForSubmit;
        if (quoted.length) {
          data.set("quoted_json", JSON.stringify(quoted));
          data.set("quoted_urls", quoted.map((it) => it.url).filter(Boolean).join("\n"));
        }
        if (this._wantChromeFallback) {
          data.set("chrome_fallback", "1");
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
        if (!res.ok) {
          const raw = await res.text();
          let bodyFail = {};
          try { bodyFail = raw ? JSON.parse(raw) : {}; } catch (_) { bodyFail = {}; }
          this._lastErrorMeta = _apiErrorMeta(bodyFail);
          const msg = _apiDetail(bodyFail, "") || _("job_create_fail");
          this._showQuoteChromeOffer(this._lastErrorMeta);
          throw new Error(msg);
        }
        const body = await res.json().catch(() => ({}));
        const ids = body.ids || (body.id ? [body.id] : []);
        this._wantChromeFallback = false;
        this._lastBatchIds = ids.slice();
        this._lastBatchTotal = ids.length;
        try {
          sessionStorage.setItem(
            "dyflow_batch",
            JSON.stringify({ ids: this._lastBatchIds, total: this._lastBatchTotal })
          );
        } catch (_) { /* ignore */ }
        if (ids.length > 1) {
          window.location.href = "/queue";
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
        err.scrollIntoView({ behavior: "smooth", block: "center" });
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
        if (this._localUnchecked) this._localUnchecked.clear();
        this._syncLocalCount();
      });
    }
    if (pickFolder && folderInput) {
      pickFolder.addEventListener("click", () => folderInput.click());
      folderInput.addEventListener("change", () => {
        this._folderPicks = this._videoFiles(folderInput.files);
        if (this._localUnchecked) this._localUnchecked.clear();
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
      quoteList.addEventListener("change", (ev) => {
        const target = ev.target;
        if (target && target.name === "local_pick") {
          const val = target.getAttribute("data-local-key") || target.value;
          if (!this._localUnchecked) this._localUnchecked = new Set();
          if (target.checked) {
            this._localUnchecked.delete(val);
          } else {
            this._localUnchecked.add(val);
          }
          this._syncQuoteSelectedLabel();
        }
      });
      quoteList.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-local-key].local-file-x");
        if (btn) {
          ev.preventDefault();
          this._removeLocalFile(btn.getAttribute("data-local-key"));
          return;
        }
        const textTarget = ev.target.closest(".quote-url, .quote-idx");
        if (textTarget) {
          const row = textTarget.closest("li.quote-pick-local");
          if (row) {
            const cb = row.querySelector('input[name="local_pick"]');
            if (cb) {
              cb.checked = !cb.checked;
              cb.dispatchEvent(new Event("change", { bubbles: true }));
            }
          }
        }
      });
    }
    this._bindChannelOpts();
    const selAll = document.getElementById("quote-select-all");
    const selNone = document.getElementById("quote-select-none");
    if (selAll) selAll.addEventListener("click", () => this._setQuotePicks(true));
    if (selNone) selNone.addEventListener("click", () => this._setQuotePicks(false));
    const chromeBtn = document.getElementById("quote-chrome-btn");
    if (chromeBtn) {
      chromeBtn.addEventListener("click", () => {
        this._wantChromeFallback = true;
        const err = document.getElementById("form-error");
        if (err) err.textContent = _("err_try_chrome") + " — " + _("start");
        const submit = document.getElementById("submit-btn");
        if (submit) submit.focus();
      });
    }
    const cancelBtn = document.getElementById("cancel-btn");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", () => this._cancelActive());
    }
    const cancelAllLog = document.getElementById("cancel-all-log-btn");
    if (cancelAllLog) {
      cancelAllLog.addEventListener("click", () => this._cancelAllWithConfirm());
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

  _bindAntiCopyright() {
    const radios = document.querySelectorAll('input[name="anti_copyright_mode"]');
    if (!radios || !radios.length) return;
    const customPanel = document.getElementById("ac-custom-panel");
    const mirror = document.getElementById("ac_mirror");
    const noise = document.getElementById("ac_noise");
    const color = document.getElementById("ac_color");
    const vignette = document.getElementById("ac_vignette");
    const blurSub = document.getElementById("ac_blur_sub");
    const blurSubMode = document.getElementById("ac_blur_sub_mode");
    const blurSubRow = document.getElementById("ac-blur-sub-mode-row");
    const blurSubHint = document.getElementById("ac-blur-mode-hint");
    const pitch = document.getElementById("ac_pitch");
    const zoom = document.getElementById("ac_zoom");

    const updateBlurSubUI = () => {
      const on = !!(blurSub && blurSub.checked);
      if (blurSubRow) {
        blurSubRow.style.opacity = on ? "1" : "0.4";
        blurSubRow.style.pointerEvents = on ? "auto" : "none";
      }
      if (blurSubHint) {
        blurSubHint.style.opacity = on ? "1" : "0.4";
      }
    };

    const syncUI = (mode) => {
      if (customPanel) customPanel.hidden = (mode !== "custom");
      if (mode === "off") {
        if (mirror) mirror.checked = false;
        if (noise) noise.checked = false;
        if (color) color.checked = false;
        if (vignette) vignette.checked = false;
        if (blurSub) blurSub.checked = false;
        if (pitch) pitch.checked = false;
        if (zoom) zoom.checked = false;
      } else if (mode === "standard") {
        if (mirror) mirror.checked = false;
        if (noise) noise.checked = true;
        if (color) color.checked = true;
        if (vignette) vignette.checked = false;
        if (blurSub) blurSub.checked = true;
        if (pitch) pitch.checked = true;
        if (zoom) zoom.checked = false;
      } else if (mode === "advanced") {
        if (mirror) mirror.checked = true;
        if (noise) noise.checked = true;
        if (color) color.checked = true;
        if (vignette) vignette.checked = true;
        if (blurSub) blurSub.checked = true;
        if (pitch) pitch.checked = true;
        if (zoom) zoom.checked = true;
      }
      updateBlurSubUI();
      try {
        localStorage.setItem("dyflow_ac_mode", mode);
      } catch (_) {}
    };

    radios.forEach((r) => {
      r.addEventListener("change", () => {
        if (r.checked) syncUI(r.value);
      });
    });

    if (blurSubMode) {
      blurSubMode.addEventListener("change", () => {
        try {
          localStorage.setItem("dyflow_ac_blur_sub_mode", blurSubMode.value);
        } catch (_) {}
      });
    }

    [mirror, noise, color, vignette, blurSub, pitch, zoom].forEach((cb) => {
      if (cb) {
        cb.addEventListener("change", () => {
          updateBlurSubUI();
          try {
            const customState = {
              mirror: !!(mirror && mirror.checked),
              noise: !!(noise && noise.checked),
              color: !!(color && color.checked),
              vignette: !!(vignette && vignette.checked),
              blur_sub: !!(blurSub && blurSub.checked),
              pitch: !!(pitch && pitch.checked),
              zoom: !!(zoom && zoom.checked),
            };
            localStorage.setItem("dyflow_ac_custom", JSON.stringify(customState));
          } catch (_) {}
        });
      }
    });

    try {
      const savedBsMode = localStorage.getItem("dyflow_ac_blur_sub_mode") || "bottom";
      if (blurSubMode) blurSubMode.value = savedBsMode;

      const savedMode = localStorage.getItem("dyflow_ac_mode") || "standard";
      const targetRadio = document.querySelector(`input[name="anti_copyright_mode"][value="${savedMode}"]`);
      if (targetRadio) {
        targetRadio.checked = true;
        syncUI(savedMode);
      }
      if (savedMode === "custom") {
        const rawCustom = localStorage.getItem("dyflow_ac_custom");
        if (rawCustom) {
          const cState = JSON.parse(rawCustom);
          if (mirror) mirror.checked = !!cState.mirror;
          if (noise) noise.checked = !!cState.noise;
          if (color) color.checked = !!cState.color;
          if (vignette) vignette.checked = !!cState.vignette;
          if (blurSub) blurSub.checked = !!cState.blur_sub;
          if (pitch) pitch.checked = !!cState.pitch;
          if (zoom) zoom.checked = !!cState.zoom;
        }
      }
      updateBlurSubUI();
    } catch (_) {}
  },

  async _cancelAllWithConfirm() {
    if (this._cancelling) return;
    const msg = _("cancel_all_confirm") || "Bạn có chắc muốn huỷ toàn bộ video đang chạy và trong hàng đợi không?";
    if (!window.confirm(msg)) return;
    await this._cancelActive();
  },

  async _cancelActive() {
    const btn = document.getElementById("cancel-btn");
    const logBtn = document.getElementById("cancel-all-log-btn");
    if (this._cancelling) return;
    this._cancelling = true;
    if (btn) {
      btn.disabled = true;
      btn.textContent = _("cancelling");
    }
    if (logBtn) {
      logBtn.disabled = true;
      logBtn.textContent = _("cancelling");
    }
    try {
      const res = await fetch("/api/jobs/cancel-active", { method: "POST" });
      if (!res.ok) throw new Error(_("cancel_fail"));
      this._lastBatchIds = [];
      try { sessionStorage.removeItem("dyflow_batch"); } catch (_) {}
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
      if (logBtn) {
        logBtn.disabled = false;
        logBtn.textContent = _("queue_cancel_all") || "Huỷ tất cả";
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
    // /user/ = kênh (kể cả ?vid=). Chỉ ẩn khi có modal_id= (đang mở 1 clip).
    const t = text || "";
    if (/[?&]modal_id=\d{10,}/i.test(t)) return false;
    return /douyin\.com\/(user|channel|share\/user)\//i.test(t)
      || /iesdouyin\.com\/share\/user\//i.test(t);
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
        const max = Number(window.DYFLOW_CHANNEL_MAX || count.max || 100000);
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

  _selectedLocalFiles() {
    const unchecked = this._localUnchecked || new Set();
    return this._localFiles().filter((f) => !unchecked.has(this._fileKey(f)));
  },

  _removeLocalFile(key) {
    if (!key) return;
    this._filePicks = (this._filePicks || []).filter((f) => this._fileKey(f) !== key);
    this._folderPicks = (this._folderPicks || []).filter((f) => this._fileKey(f) !== key);
    if (this._localUnchecked) this._localUnchecked.delete(key);
    if (!this._localFiles().length) this._resetLocalInputs();
    this._syncLocalCount();
  },

  _clearLocalFiles() {
    this._filePicks = [];
    this._folderPicks = [];
    if (this._localUnchecked) this._localUnchecked.clear();
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
    const rawKey = this._fileKey(f);
    const key = this._escHtml(rawKey);
    const n = idx + 1;
    const isChecked = !(this._localUnchecked && this._localUnchecked.has(rawKey));
    return (
      `<li class="quote-pick-local">` +
      `<input type="checkbox" name="local_pick" data-local-key="${key}" value="${key}" ${isChecked ? "checked" : ""} />` +
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
    const clearBtn = document.getElementById("local-clear-btn");
    const files = this._localFiles();
    const n = files.length;
    if (el) el.textContent = n ? _("studio_local_n").replace("{n}", String(n)) : "";
    if (clearBtn) clearBtn.hidden = !n;
    if (!this._quoteBusy) this._fillQuoteBox();
  },

  _selectedQuoteItems() {
    const list = document.getElementById("quote-list");
    if (!list || this._lastQuoteKind !== "channel") return this._lastQuoteItems || [];
    const checked = new Set(
      Array.from(list.querySelectorAll('input[name="quote_pick"]:checked')).map((el) => el.value)
    );
    return (this._lastQuoteItems || []).filter((it) => checked.has(it.url || it.id));
  },

  _syncQuoteSelectedLabel() {
    const el = document.getElementById("quote-selected");
    const totalEl = document.getElementById("quote-total");
    const statusEl = document.getElementById("quote-status");
    const files = this._localFiles();
    const urls = this._lastQuoteItems || [];
    const selectable = this._lastQuoteKind === "channel";

    if (files.length && !urls.length) {
      const selectedFiles = this._selectedLocalFiles();
      const n = selectedFiles.length;
      const m = files.length;
      if (el) {
        el.textContent = _("channel_selected_n").replace("{n}", String(n)).replace("{m}", String(m));
      }
      if (statusEl && !this._quoteError) {
        statusEl.textContent = _("studio_local_files").replace("{n}", String(n));
      }
      if (totalEl) {
        totalEl.textContent = _("studio_local_n").replace("{n}", String(n));
      }
      return;
    }

    if (!selectable) {
      if (el) el.textContent = "";
      return;
    }
    const all = (this._lastQuoteItems || []).length;
    const n = this._selectedQuoteItems().length;
    if (el) {
      el.textContent = _("channel_selected_n").replace("{n}", String(n)).replace("{m}", String(all));
    }
    if (totalEl && all) {
      const cred = this._selectedQuoteItems().reduce((s, it) => s + (Number(it.credits) || 1), 0);
      totalEl.textContent = _("quote_total")
        .replace("{n}", String(cred))
        .replace("{m}", String(n));
    }
  },

  _setQuotePicks(all) {
    const list = document.getElementById("quote-list");
    if (!list) return;
    list.querySelectorAll('input[name="quote_pick"]').forEach((el) => {
      el.checked = !!all;
    });
    if (!this._localUnchecked) this._localUnchecked = new Set();
    const files = this._localFiles();
    if (all) {
      this._localUnchecked.clear();
      list.querySelectorAll('input[name="local_pick"]').forEach((el) => {
        el.checked = true;
      });
    } else {
      files.forEach((f) => this._localUnchecked.add(this._fileKey(f)));
      list.querySelectorAll('input[name="local_pick"]').forEach((el) => {
        el.checked = false;
      });
    }
    this._syncQuoteSelectedLabel();
  },

  _showQuoteChromeOffer(meta) {
    const wrap = document.getElementById("quote-chrome");
    if (!wrap) return;
    const offer = !!(meta && meta.offer_chrome);
    wrap.hidden = !offer;
  },

  _mapErrorMessage(meta, fallback) {
    const code = (meta && meta.error_code) || "";
    if (code === "not_found") return _("err_not_found");
    if (code === "need_login") return _("err_need_login");
    if (code === "network") return _("err_network");
    if (code === "invalid_url") return _("err_invalid_url");
    if (code === "empty_channel") return _("channel_empty");
    return fallback || (meta && meta.detail) || _("quote_fail");
  },

  _fillQuoteBox() {
    const box = document.getElementById("quote-box");
    const list = document.getElementById("quote-list");
    const statusEl = document.getElementById("quote-status");
    const totalEl = document.getElementById("quote-total");
    const toolbar = document.getElementById("quote-toolbar");
    if (!box || !list) return;
    const files = this._localFiles();
    const urls = this._lastQuoteItems || [];
    const selectable = this._lastQuoteKind === "channel";
    const hasLocal = files.length > 0;
    const hasChannel = selectable && urls.length > 0;
    if (toolbar) toolbar.hidden = !((hasLocal || hasChannel) && !this._quoteBusy);
    if (this._quoteBusy) return;
    if (!urls.length && !files.length) {
      if (this._quoteError) {
        box.hidden = false;
        list.innerHTML = "";
        if (statusEl) statusEl.textContent = this._quoteError;
        if (totalEl) totalEl.textContent = "";
        this._showQuoteChromeOffer(this._lastErrorMeta);
      } else {
        box.hidden = true;
        list.innerHTML = "";
        if (totalEl) totalEl.textContent = "";
        this._showQuoteChromeOffer(null);
      }
      return;
    }
    box.hidden = false;
    this._showQuoteChromeOffer(null);
    const urlHtml = urls
      .map((it, idx) => {
        const dur = it.unknown
          ? _("quote_unknown_dur")
          : it.duration_label || "—";
        const cred = _("quote_line_credits").replace("{n}", String(it.credits));
        const tip = it.unknown ? ` title="${_("quote_unknown_tip")}"` : "";
        if (selectable) {
          const title = this._escHtml(it.title || it.id || this._shortSrc(it.url));
          const thumb = it.thumb
            ? `<img class="quote-thumb" src="${this._escHtml(it.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`
            : `<span class="quote-thumb"></span>`;
          const val = this._escHtml(it.url || "");
          return (
            `<li class="quote-pick"${tip}>` +
            `<input type="checkbox" name="quote_pick" value="${val}" />` +
            thumb +
            `<span><span class="quote-title">${title}</span>` +
            `<span class="quote-meta"><em>${dur}</em> · <strong>${cred}</strong></span></span>` +
            `</li>`
          );
        }
        const short = this._escHtml((it.url || "").replace(/^https?:\/\//, ""));
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
    if (selectable) {
      list.querySelectorAll('input[name="quote_pick"]').forEach((el) => {
        el.addEventListener("change", () => this._syncQuoteSelectedLabel());
      });
    }
    if (selectable || files.length) {
      this._syncQuoteSelectedLabel();
    }
    if (statusEl) {
      if (this._quoteError) statusEl.textContent = this._quoteError;
      else if (files.length && !urls.length) {
        const selCount = this._selectedLocalFiles().length;
        statusEl.textContent = _("studio_local_files").replace("{n}", String(selCount));
      } else if (selectable) {
        statusEl.textContent = this._lastQuoteStatus || _("quote_pick_title");
      } else {
        statusEl.textContent = this._lastQuoteStatus || _("quote_title");
      }
    }
    if (totalEl) {
      if (files.length && !urls.length) {
        const selCount = this._selectedLocalFiles().length;
        totalEl.textContent = _("studio_local_n").replace("{n}", String(selCount));
      } else if (selectable) {
        this._syncQuoteSelectedLabel();
      } else if (urls.length && this._lastQuoteTotal) {
        totalEl.textContent = this._lastQuoteTotal;
      } else {
        totalEl.textContent = "";
      }
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
      const res = await fetch("/api/jobs?active_only=1");
      if (!res.ok) return;
      const body = await res.json();
      this._renderLive(body.jobs || [], body);
    } catch (_) { /* ignore */ }
  },

  _renderLive(jobs, meta) {
    const el = document.getElementById("live-log");
    if (!el) return;
    const all = jobs || [];
    const active = all.filter(
      (j) => j.status === "queued" || j.status === "running"
    );
    const totalActive = (meta && typeof meta.active_count === "number") ? meta.active_count : active.length;
    const totalQueued = (meta && typeof meta.queued_count === "number") ? meta.queued_count : active.filter(j => j.status === "queued").length;
    const totalRunning = (meta && typeof meta.running_count === "number") ? meta.running_count : active.filter(j => j.status === "running").length;

    // Update rail badge for queue
    const queueBadge = document.getElementById("rail-queue-badge");
    if (queueBadge) {
      if (totalActive > 0) {
        queueBadge.hidden = false;
        queueBadge.textContent = totalActive > 999 ? "999+" : String(totalActive);
      } else {
        queueBadge.hidden = true;
      }
    }

    // Update active jobs banner above the form
    const activeBanner = document.getElementById("active-jobs-banner");
    const activeText = document.getElementById("active-banner-text");
    if (activeBanner) {
      if (totalActive > 0) {
        activeBanner.style.display = "flex";
        if (activeText) {
          activeText.innerHTML = `Đang có <strong>${totalRunning}</strong> video đang xử lý và <strong>${totalQueued}</strong> video trong hàng chờ.`;
        }
      } else {
        activeBanner.style.display = "none";
      }
    }

    const cancelBtn = document.getElementById("cancel-btn");
    if (cancelBtn && !this._cancelling) cancelBtn.hidden = !totalActive;
    const cancelAllLogBtn = document.getElementById("cancel-all-log-btn");
    if (cancelAllLogBtn && !this._cancelling) cancelAllLogBtn.hidden = !totalActive;
    this._jobsBusy = !!totalActive;
    this._syncStudioBusy();

    let batchHtml = "";
    let failHtml = "";
    const batchIds = this._lastBatchIds || [];
    if (batchIds.length > 1) {
      const set = new Set(batchIds);
      const related = all.filter((j) => set.has(j.id));
      const failed = related.filter((j) => j.status === "failed");
      const total = this._lastBatchTotal || batchIds.length;
      const done = Math.max(0, total - totalActive);
      const label = _("batch_progress").replace("{x}", String(done)).replace("{y}", String(total));
      batchHtml = `<li class="live-batch">${label}</li>`;
      failHtml = failed
        .map((j) => {
          const src = this._shortSrc(j.source_url);
          const msg = this._escHtml(j.error || j.message || _("err_need_login"));
          return (
            `<li class="live-fail">` +
            `<strong>${_("status_failed") || "Failed"}</strong>` +
            (src ? `<span>${this._escHtml(src)}</span>` : "") +
            `<span>${msg}</span>` +
            `</li>`
          );
        })
        .join("");
      if (done >= total && !totalActive) {
        if (!failed.length) {
          this._lastBatchIds = [];
          try { sessionStorage.removeItem("dyflow_batch"); } catch (_) {}
        }
      }
    }

    if (!totalActive && !failHtml) {
      el.innerHTML = batchHtml || `<li class="empty">${_("studio_log_empty")}</li>`;
      return;
    }

    // Summary header for live-log
    const summaryHtml = totalActive > 0 ? (
      `<li class="live-summary" style="display:flex; justify-content:space-between; align-items:center; padding: 10px 14px; background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.2); border-radius: 8px; font-size: 13px; margin-bottom: 8px;">` +
      `<span>⚡ <strong>Tiến độ:</strong> ${totalRunning} video đang dịch · ${totalQueued} video trong hàng chờ</span>` +
      `<a href="/queue" style="font-size: 12px; color: #38bdf8; text-decoration: none; font-weight:600;">Xem hàng chờ chi tiết →</a>` +
      `</li>`
    ) : "";

    const runningJobs = active.filter((j) => j.status === "running");
    const queuedJobs = active.filter((j) => j.status === "queued");
    const displayQueued = queuedJobs.slice(0, 5);
    const toRender = [...runningJobs, ...displayQueued];

    let itemsHtml = toRender
      .map((j) => {
        const pct = Math.max(0, Math.min(100, Number(j.progress) || 0));
        const label =
          j.status === "queued"
            ? _("status_queued")
            : j.stage_label || _("status_running");
        const extra = j.status === "running" && pct ? ` · ${pct}%` : "";
        const src = this._shortSrc(j.source_url);
        const msg = j.message || "";
        const saveDest = j.export_dir ? `<span class="queue-dest" title="${this._escHtml(j.export_dir)}" style="font-size:0.83em;color:#38bdf8;display:block;margin-top:2px;">📁 ${this._escHtml(this._shortSrc(j.export_dir))}</span>` : "";
        return (
          `<li>` +
          `<div class="live-row">` +
          `<span class="live-row-title">` +
          (j.status === "running" ? `<i class="quote-spin"></i>` : "") +
          `<strong>${label}${extra}</strong>` +
          `</span>` +
          `<button type="button" class="btn ghost danger btn-xs" data-cancel-id="${j.id}">${_("cancel")}</button>` +
          `</div>` +
          (src ? `<span>${this._escHtml(src)}</span>` : "") +
          (msg ? `<span>${this._escHtml(msg)}</span>` : "") +
          saveDest +
          `<span class="live-bar"><i style="width:${pct}%"></i></span>` +
          `</li>`
        );
      })
      .join("");

    let moreHtml = "";
    if (totalQueued > displayQueued.length) {
      const remaining = totalQueued - displayQueued.length;
      moreHtml = (
        `<li class="live-more" style="text-align: center; padding: 12px; background: rgba(56, 189, 248, 0.04); border: 1px dashed rgba(56, 189, 248, 0.25); border-radius: 8px; margin-top: 6px;">` +
        `<span style="font-size: 13px; color: #9da2b8;">... và <strong>${remaining}</strong> video khác đang trong hàng chờ</span> ` +
        `<a href="/queue" class="btn ghost btn-sm" style="margin-left: 10px; color: #38bdf8; font-weight:600;">Xem tất cả trong Hàng chờ →</a>` +
        `</li>`
      );
    }

    el.innerHTML = batchHtml + failHtml + summaryHtml + itemsHtml + moreHtml;
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
      }, 55000);
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
      if (!res.ok) {
        this._lastErrorMeta = _apiErrorMeta(body);
        throw new Error(this._mapErrorMessage(this._lastErrorMeta, _apiDetail(body, _("quote_fail"))));
      }

      const items = body.items || body.videos || [];
      this._lastQuoteKind = body.kind === "channel" ? "channel" : "video";
      this._lastQuoteUrl = items.length ? text : "";
      this._lastQuoteItems = items;
      this._quoteError = "";
      this._lastErrorMeta = null;
      this._wantChromeFallback = false;
      if (this._lastQuoteKind === "channel") {
        this._lastQuoteStatus = _("quote_channel_n").replace("{n}", String(items.length));
        if (body.channel_incomplete && items.length <= 1) {
          this._lastQuoteStatus = _("quote_channel_one");
        }
      } else if (body.channel_incomplete) {
        this._lastQuoteStatus = items.length > 1
          ? _("quote_channel_short").replace("{n}", String(items.length))
          : _("quote_channel_one");
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
      this._lastQuoteKind = "video";
      this._lastQuoteStatus = "";
      this._lastQuoteTotal = "";
      const aborted = ex && (ex.name === "AbortError" || /aborted/i.test(String(ex.message || "")));
      this._quoteError = aborted
        ? (_("quote_channel_timeout") || "Channel listing timed out (~55s). Close Edge/Chrome, then Preview again.")
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
      if (status) status.textContent = _("openai_tts_key_checking");
      try {
        const data = new FormData();
        data.set("key", input.value || "");
        const res = await fetch("/api/openai-tts-key", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("openai_translate_key_invalid")));
        this._hasOpenaiTts = !!body.has_openai_tts_key;
        window.DYFLOW_HAS_OPENAI_TTS = this._hasOpenaiTts;
        if (status) {
          status.textContent = this._hasOpenaiTts
            ? _("openai_tts_key_ok")
            : _("openai_tts_key_needed");
        }
        input.value = "";
        this._renderTts();
        try {
          sessionStorage.removeItem("dyflow_te_picked");
        } catch (_) { /* ignore */ }
        this._applyDefaultTranslateEngine();
        this._syncOpenaiKeyBox();
      } catch (ex) {
        this._hasOpenaiTts = !!window.DYFLOW_HAS_OPENAI_TTS;
        if (status) status.textContent = ex.message || _("openai_translate_key_invalid");
      } finally {
        btn.disabled = false;
      }
    });
  },

  _bindDeepseekKey() {
    const btn = document.getElementById("home-deepseek-save");
    const input = document.getElementById("home-deepseek-key");
    const status = document.getElementById("home-deepseek-status");
    if (!btn || !input) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (status) status.textContent = _("deepseek_key_checking");
      try {
        const data = new FormData();
        data.set("key", input.value || "");
        const res = await fetch("/api/deepseek-key", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("deepseek_key_invalid")));
        this._hasDeepseek = !!body.has_deepseek_key;
        window.DYFLOW_HAS_DEEPSEEK = this._hasDeepseek;
        if (status) {
          status.textContent = this._hasDeepseek
            ? _("deepseek_key_ok")
            : _("deepseek_key_needed");
        }
        input.value = "";
        try {
          sessionStorage.removeItem("dyflow_te_picked");
        } catch (_) { /* ignore */ }
        this._applyDefaultTranslateEngine();
        this._syncOpenaiKeyBox();
      } catch (ex) {
        this._hasDeepseek = !!window.DYFLOW_HAS_DEEPSEEK;
        if (status) status.textContent = ex.message || _("deepseek_key_invalid");
      } finally {
        btn.disabled = false;
      }
    });
  },

  _bindQwenKey() {
    const btn = document.getElementById("home-qwen-save");
    const input = document.getElementById("home-qwen-key");
    const status = document.getElementById("home-qwen-status");
    if (!btn || !input) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (status) status.textContent = _("qwen_key_checking");
      try {
        const data = new FormData();
        data.set("key", input.value || "");
        const res = await fetch("/api/qwen-key", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(_apiDetail(body, _("qwen_key_invalid")));
        this._hasQwen = !!body.has_qwen_key;
        window.DYFLOW_HAS_QWEN = this._hasQwen;
        if (status) {
          status.textContent = this._hasQwen
            ? _("qwen_key_ok")
            : _("qwen_key_needed");
        }
        input.value = "";
        try {
          sessionStorage.removeItem("dyflow_te_picked");
        } catch (_) { /* ignore */ }
        this._applyDefaultTranslateEngine();
        this._syncOpenaiKeyBox();
      } catch (ex) {
        this._hasQwen = !!window.DYFLOW_HAS_QWEN;
        if (status) status.textContent = ex.message || _("qwen_key_invalid");
      } finally {
        btn.disabled = false;
      }
    });
  },

  _bindTranslateEngine() {
    document.querySelectorAll('input[name="translate_engine"]').forEach((inp) => {
      inp.addEventListener("change", () => {
        try {
          sessionStorage.setItem("dyflow_te_picked", inp.value || "");
        } catch (_) { /* ignore */ }
        this._syncOpenaiKeyBox();
      });
    });
  },

  _bindGenre() {
    this._genreTemplates = window.DYFLOW_GENRE_TEMPLATES || {};
    this._genreDirty = false;
    this._genreEditorOpen = false;
    const sel = document.getElementById("genre_id");
    const ta = document.getElementById("genre_prompt");
    const reset = document.getElementById("genre-prompt-reset");
    const toggle = document.getElementById("genre-prompt-toggle");
    const panel = document.getElementById("genre-prompt-panel");
    const status = document.getElementById("genre-status");
    const title = document.getElementById("genre-prompt-title");
    const flash = document.getElementById("genre-prompt-flash");
    if (!sel) return;

    const selectedLabel = () => {
      const opt = sel.options[sel.selectedIndex];
      return (opt && opt.value) ? (opt.textContent || opt.value).trim() : "";
    };
    const tmplLen = (id) => ((this._genreTemplates[id] || "").length);

    const syncStatus = () => {
      const id = sel.value || "";
      if (!status) return;
      if (!id) {
        status.hidden = true;
        status.textContent = "";
        if (toggle) toggle.hidden = true;
        return;
      }
      status.hidden = false;
      status.textContent = _("genre_status_using")
        .replace("{label}", selectedLabel())
        .replace("{n}", String(tmplLen(id)));
      if (toggle) {
        toggle.hidden = false;
        toggle.textContent = this._genreEditorOpen
          ? _("genre_prompt_hide")
          : _("genre_prompt_edit");
      }
    };

    const syncMeta = () => {
      const meta = document.getElementById("genre-prompt-meta");
      if (!meta || !ta) return;
      meta.textContent = _("genre_prompt_meta").replace(
        "{n}", String((ta.value || "").length),
      );
    };

    const syncTitle = () => {
      if (!title) return;
      const label = selectedLabel();
      title.textContent = label
        ? _("genre_prompt_for").replace("{label}", label)
        : _("genre_prompt_label");
    };

    const showFlash = (msg) => {
      if (!flash) return;
      flash.hidden = !msg;
      flash.textContent = msg || "";
      if (msg) {
        clearTimeout(this._genreFlashTimer);
        this._genreFlashTimer = setTimeout(() => {
          flash.hidden = true;
        }, 2500);
      }
    };

    const fetchTemplates = async () => {
      try {
        const res = await fetch("/api/genres", { cache: "no-store" });
        if (!res.ok) return;
        const body = await res.json();
        if (body && body.templates) {
          this._genreTemplates = body.templates;
          window.DYFLOW_GENRE_TEMPLATES = body.templates;
        }
      } catch (_) { /* keep cache */ }
    };

    const setEditorOpen = (open) => {
      this._genreEditorOpen = !!open;
      if (panel) panel.hidden = !this._genreEditorOpen;
      if (this._genreEditorOpen && ta) {
        const id = sel.value || "";
        if (!this._genreDirty) {
          ta.value = id ? (this._genreTemplates[id] || "") : "";
        }
        syncMeta();
        syncTitle();
      }
      syncStatus();
    };

    const onGenreChange = async () => {
      this._genreDirty = false;
      showFlash("");
      await fetchTemplates();
      if (ta && this._genreEditorOpen) {
        const id = sel.value || "";
        ta.value = id ? (this._genreTemplates[id] || "") : "";
        syncMeta();
        syncTitle();
      }
      if (!sel.value) setEditorOpen(false);
      else syncStatus();
    };

    sel.addEventListener("change", () => { onGenreChange(); });

    if (toggle) {
      toggle.addEventListener("click", async () => {
        if (!sel.value) return;
        await fetchTemplates();
        setEditorOpen(!this._genreEditorOpen);
      });
    }

    if (ta) {
      ta.addEventListener("input", () => {
        this._genreDirty = true;
        syncMeta();
      });
    }

    if (reset) {
      reset.addEventListener("click", async () => {
        const id = sel.value || "";
        if (!id) {
          showFlash(_("genre_none"));
          return;
        }
        await fetchTemplates();
        const tmpl = this._genreTemplates[id] || "";
        if (ta) ta.value = tmpl;
        this._genreDirty = false;
        syncMeta();
        syncTitle();
        setEditorOpen(true);
        showFlash(
          _("genre_prompt_reset_ok").replace("{n}", String(tmpl.length)),
        );
      });
    }

    fetchTemplates().then(() => syncStatus());
  },

  _applyDefaultTranslateEngine() {
    const deepseek = document.querySelector('input[name="translate_engine"][value="deepseek"]');
    const google = document.querySelector('input[name="translate_engine"][value="google"]');
    if (!deepseek && !google) return;
    const userPicked = sessionStorage.getItem("dyflow_te_picked");
    if (userPicked === "google" || userPicked === "deepseek") {
      const el = document.querySelector(`input[name="translate_engine"][value="${userPicked}"]`);
      if (el) {
        document.querySelectorAll('input[name="translate_engine"]').forEach((inp) => {
          inp.checked = inp === el;
        });
      }
      return;
    }
    const pick = (el) => {
      document.querySelectorAll('input[name="translate_engine"]').forEach((inp) => {
        inp.checked = inp === el;
      });
    };
    if (this._hasDeepseek && deepseek) {
      pick(deepseek);
      return;
    }
    if (google) {
      pick(google);
    }
  },

  _selectedTranslateEngine() {
    const fallback = this._hasDeepseek ? "deepseek" : "google";
    return (
      (document.querySelector('input[name="translate_engine"]:checked') || {}).value
      || fallback
    );
  },

  _ttsNeedsOpenaiKey(ttsId) {
    return false;
  },

  _syncOpenaiKeyBox() {
    const dsBox = document.getElementById("deepseek-key-box");
    const te = this._selectedTranslateEngine();
    if (dsBox) {
      dsBox.hidden = te !== "deepseek";
    }
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
      inp.addEventListener("change", () => {
        this._showTtsError("");
        this._syncOpenaiKeyBox();
      });
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
    this._syncOpenaiKeyBox();
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
    this._resultReady = false;
    this._bindCancel();
    this._bindSaveActions();
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

  _bindSaveActions() {
    if (this._saveBound) return;
    this._saveBound = true;
    DyflowSavePath.init();
    const dl = document.getElementById("download-btn");
    if (dl) {
      dl.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (dl.disabled) return;
        this._saveLocal({ auto: false });
      });
    }
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
      // Chi toan xanh khi co file that — tranh Done gia khi MP4 missing.
      if (job.status === "completed" && job.has_output) li.classList.add("done");
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
      if (ph) {
        ph.innerHTML = `<span style="color:#ef4444;font-weight:600;">${_("job_failed")}</span><br><span style="font-size:0.9em;opacity:0.85;">${this._escHtml(job.error || _("job_fail_msg"))}</span>`;
      }
      const spin = document.querySelector(".spinner");
      if (spin) spin.style.display = "none";
      this._showEditorActions(false);
      const actions = document.getElementById("result-actions");
      if (actions) {
        actions.hidden = false;
        const dl = document.getElementById("download-btn");
        if (dl) dl.hidden = true;
        const ed = document.getElementById("editor-btn");
        if (ed) ed.hidden = true;
        const savePath = document.getElementById("save-path-card");
        if (savePath) savePath.hidden = true;
      }
    }

    if (job.status === "completed" && job.has_output) {
      this.showResult();
      this.showQa(job.qa);
    } else if (job.status === "completed" && !job.has_output) {
      if (ph) ph.textContent = _("job_done_no_file") || "Hoan tat nhung khong co file xuat. Hay tao lai video.";
      const spin = document.querySelector(".spinner");
      if (spin) spin.style.display = "none";
      const actions = document.getElementById("result-actions");
      const dl = document.getElementById("download-btn");
      if (actions) actions.hidden = false;
      if (dl) {
        dl.hidden = true;
        dl.disabled = true;
      }
      const browse = document.getElementById("save-path-browse");
      if (browse) browse.hidden = true;
      const hint = (job.message && String(job.message).trim())
        || _("job_done_no_file")
        || "Hoan tat nhung khong co file xuat. Hay tao lai video.";
      this._setSaveNote(hint, false);
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
    const QA_ALERT = new Set([
      "chua_dich", "qua_dai", "qua_nhanh",
      "tier4_aggressive_cut", "tier5_large_overlap", "chong_doan_severe",
    ]);
    const flagLabel = (f) => {
      if (String(f).startsWith("shortened_x")) return _("qa_flag_shortened") || f;
      return _("qa_flag_" + f) || f;
    };
    const tier45 = rows.filter((r) => {
      const f = r.flags || [];
      return f.includes("tier4_aggressive_cut")
        || f.includes("tier5_large_overlap")
        || f.includes("chong_doan_severe");
    });
    const statsLine = tier45.length
      ? `<p class="qa-tier45-stats">${(_("qa_tier45_stats") || "Tier 4/5: {n}/{total}")
          .replace("{n}", String(tier45.length))
          .replace("{total}", String(rows.length))}</p>
         <p class="qa-tier45-title">${_("qa_tier45_title") || "Tier 4/5 — listen again"}</p>
         <ul class="qa-tier45-list">${tier45.map((r) => {
           const ov = r.tier5_overlap_sec != null ? r.tier5_overlap_sec : r.overlap_sec;
           const xf = r.tier5_crossfade_sec != null ? r.tier5_crossfade_sec : "";
           const br = r.tier3_borrowed_sec || 0;
           const meta = [
             br ? `borrow ${Number(br).toFixed(2)}s` : null,
             ov != null ? `overlap ${Number(ov).toFixed(2)}s` : null,
             xf !== "" && xf != null ? `xfade ${Number(xf).toFixed(2)}s` : null,
           ].filter(Boolean).join(" · ");
           const txt = (r.text || "").replace(/</g, "&lt;");
           return `<li class="qa-bad"><span class="qa-meta">#${r.i + 1} · ${meta}</span><span class="qa-text">${txt}</span></li>`;
         }).join("")}</ul>`
      : `<p class="qa-tier45-stats">${(_("qa_tier45_stats") || "Tier 4/5: {n}/{total}")
          .replace("{n}", "0")
          .replace("{total}", String(rows.length))} — clean</p>`;
    list.innerHTML = statsLine + rows
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
    if (this._resultReady) return;
    this._resultReady = true;
    const wrap = document.getElementById("video-wrap");
    // Inline preview — KHONG attachment; auto-luu ra thu muc da chon.
    const playUrl = `/api/jobs/${this.jobId}/download`;
    wrap.innerHTML = `<video controls playsinline src="${playUrl}"></video>`;
    this._showEditorActions(true);
    this._bindSaveActions();
    DyflowSavePath.refresh();
    const note = document.getElementById("auto-dl-note");
    if (note) {
      note.hidden = true;
      note.textContent = "";
    }
    // Tu dong luu vao thu muc da chon (khong can bam Download).
    if (!this._autoDownloaded) {
      this._autoDownloaded = true;
      this._saveLocal({ auto: true });
    }
  },

  _setSaveNote(text, ok) {
    DyflowSavePath.setStatus(text, ok);
    const note = document.getElementById("auto-dl-note");
    if (!note || note === document.getElementById("save-path-status")) return;
    note.hidden = !text;
    note.textContent = text || "";
    note.style.color = ok === false ? "#ff8a8a" : "";
    note.style.fontWeight = ok === false || ok === true ? "600" : "";
    note.style.fontSize = "0.95rem";
    note.style.marginTop = "10px";
  },

  _applySaveDir(dir) {
    DyflowSavePath.apply(dir);
  },

  async _persistSaveDir(raw) {
    return DyflowSavePath.persist(raw);
  },

  async _initSavePath() {
    DyflowSavePath.init();
    return DyflowSavePath.refresh();
  },

  async _pickSaveFolder() {
    return DyflowSavePath.pickFolder();
  },

  async _saveViaFilePicker(filename) {
    if (typeof window.showSaveFilePicker !== "function") return false;
    const handle = await window.showSaveFilePicker({
      suggestedName: filename || "video.mp4",
      types: [
        {
          description: "MP4",
          accept: { "video/mp4": [".mp4"] },
        },
      ],
    });
    const res = await fetch(`/api/jobs/${this.jobId}/download`);
    if (!res.ok) throw new Error(_("save_download_fail") || "Download failed");
    const blob = await res.blob();
    if (!blob || blob.size < 100) throw new Error(_("save_download_fail") || "Empty file");
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return true;
  },

  async _saveViaExportLocal() {
    const body = {};
    const dir = DyflowSavePath.currentDir();
    if (dir) body.dir = dir;
    const res = await fetch(`/api/jobs/${this.jobId}/export-local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      const detail = data.detail;
      const msg = typeof detail === "string"
        ? detail
        : (_("save_download_fail") || "Save failed");
      throw new Error(msg);
    }
    if (data.dir) DyflowSavePath.apply(data.dir);
    return data;
  },

  async _saveLocal(opts) {
    const auto = !!(opts && opts.auto);
    const btn = document.getElementById("download-btn");
    if (btn && !auto) btn.disabled = true;
    this._setSaveNote(_("save_download_saving") || "Saving…", true);
    try {
      let filename = "video.mp4";
      try {
        const metaRes = await fetch(`/api/jobs/${this.jobId}/export-meta`);
        const meta = await metaRes.json().catch(() => ({}));
        if (meta.filename) filename = meta.filename;
        if (meta.dir) DyflowSavePath.apply(meta.dir);
        if (metaRes.ok && meta.ready === false) {
          throw new Error(_("save_download_fail") || "Output missing");
        }
      } catch (e) {
        if (e && e.message && /Output missing|save_download|Không lưu/.test(e.message)) {
          throw e;
        }
      }

      // Auto-save: luon copy server -> thu muc da chon (khong mo Save As).
      if (auto && window.DYFLOW_DESKTOP) {
        const data = await this._saveViaExportLocal();
        this._setSaveNote(
          (_("saved_to_downloads") || "Saved: {path}").replace(
            "{path}",
            data.path || filename,
          ),
          true,
        );
        return;
      }

      // Manual Download: desktop uu tien copy Save folder (khong mo Save As truoc —
      // Save As + ten Viet dai de VLC loi; export-local dung ten ASCII an toan).
      if (window.DYFLOW_DESKTOP) {
        const data = await this._saveViaExportLocal();
        this._setSaveNote(
          (_("saved_to_downloads") || "Saved: {path}").replace(
            "{path}",
            data.path || filename,
          ),
          true,
        );
        return;
      }

      if (!auto && typeof window.showSaveFilePicker === "function") {
        try {
          await this._saveViaFilePicker(filename);
          this._setSaveNote(
            (_("saved_to_downloads") || "Saved: {path}").replace("{path}", filename),
            true,
          );
          return;
        } catch (e) {
          if (e && e.name === "AbortError") {
            this._setSaveNote(_("save_download_cancelled") || "Cancelled", true);
            return;
          }
        }
      }

      const a = document.createElement("a");
      a.href = `/api/jobs/${this.jobId}/download?dl=1`;
      a.rel = "noopener";
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      this._setSaveNote(_("auto_download_note") || "Downloading…", true);
    } catch (e) {
      const msg = (e && e.message) || _("save_download_fail") || "Save failed";
      this._setSaveNote(msg, false);
      if (!auto) {
        try {
          window.alert(msg);
        } catch (_) {}
      }
    } finally {
      if (btn) btn.disabled = false;
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

  _escHtml(s) {
    return _escHtml(s);
  },

  async _poll() {
    try {
      const res = await fetch("/api/jobs?limit=500");
      if (!res.ok) return;
      const body = await res.json();
      this._render(body.jobs || [], body);
    } catch (err) {
      console.error("Queue poll error:", err);
    }
  },

  _render(jobs, meta) {
    const live = document.getElementById("live-log");
    const recent = document.getElementById("queue-recent");
    const cancelBtn = document.getElementById("cancel-btn");
    const list = jobs || [];
    const active = list.filter((j) => j.status === "queued" || j.status === "running");
    const done = list.filter(
      (j) => j.status === "failed" || j.status === "cancelled"
    );
    const totalActive = (meta && typeof meta.active_count === "number") ? meta.active_count : active.length;
    const queueBadge = document.getElementById("rail-queue-badge");
    if (queueBadge) {
      if (totalActive > 0) {
        queueBadge.hidden = false;
        queueBadge.textContent = totalActive > 999 ? "999+" : String(totalActive);
      } else {
        queueBadge.hidden = true;
      }
    }

    if (cancelBtn && !this._cancelling) cancelBtn.hidden = !totalActive;

    if (live) {
      if (!active.length) {
        live.innerHTML = `<li class="empty">${_("queue_empty")}</li>`;
      } else {
        const showAll = !!this._showAll;
        const displayLimit = showAll ? active.length : 60;
        const visible = active.slice(0, displayLimit);
        let html = visible.map((j) => this._activeRow(j)).join("");
        if (active.length > visible.length) {
          const remaining = active.length - visible.length;
          html += (
            `<li class="live-more" style="text-align: center; padding: 14px; background: rgba(56, 189, 248, 0.05); border: 1px dashed rgba(56, 189, 248, 0.3); border-radius: 8px; margin-top: 10px;">` +
            `<span style="color: #9da2b8; font-size: 13.5px;">Đang hiển thị <strong>${visible.length}</strong> / <strong>${active.length}</strong> video trong hàng chờ.</span> ` +
            `<button type="button" id="queue-show-all-btn" class="btn ghost btn-sm" style="margin-left: 10px; color: #38bdf8; font-weight: 600;">Xem tất cả ${active.length} video</button>` +
            `</li>`
          );
        }
        live.innerHTML = html;
        const showAllBtn = document.getElementById("queue-show-all-btn");
        if (showAllBtn) {
          showAllBtn.onclick = () => {
            this._showAll = true;
            this._render(jobs, meta);
          };
        }
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
        ? (j.queue_pos ? `${_("status_queued")} (#${j.queue_pos})` : _("status_queued"))
        : j.stage_label || _("status_running");
    const extra = j.status === "running" && pct ? ` · ${pct}%` : "";
    const src = this._shortSrc(j.source_url);
    const msg = j.message || "";
    const saveDest = j.export_dir ? `<span class="queue-dest" title="${this._escHtml(j.export_dir)}" style="font-size:0.83em;color:#38bdf8;display:block;margin-top:2px;">📁 ${this._escHtml(this._shortSrc(j.export_dir))}</span>` : "";
    return (
      `<li>` +
      `<div class="live-row">` +
      `<span class="live-row-title">` +
      (j.status === "running" ? `<i class="quote-spin"></i>` : "") +
      `<strong>${this._escHtml(label)}${this._escHtml(extra)}</strong>` +
      `</span>` +
      `<button type="button" class="btn ghost danger btn-xs" data-cancel-id="${j.id}">${_("cancel")}</button>` +
      `</div>` +
      (src ? `<span>${this._escHtml(src)}</span>` : "") +
      (msg ? `<span>${this._escHtml(msg)}</span>` : "") +
      saveDest +
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
      `<span class="live-row-title"><strong>${this._escHtml(label)}</strong></span>` +
      `<a class="btn ghost btn-xs" href="/jobs/${j.id}">${_("queue_open")}</a>` +
      `</div>` +
      (src ? `<span>${this._escHtml(src)}</span>` : "") +
      (msg ? `<span>${this._escHtml(msg)}</span>` : "") +
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
