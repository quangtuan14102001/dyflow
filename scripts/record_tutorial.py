#!/usr/bin/env python3
"""Record English VideosCreator tutorial with EN UI + EN overlays + EN voiceover.

Run:
  .venv/bin/python scripts/record_tutorial.py
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://videoscreator.net"
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "tutorial"
AUDIO_DIR = OUT_DIR / "narration_en"
DEMO_URL = "https://www.douyin.com/video/7652940282493690614"
VIEW = {"width": 1440, "height": 900}
REC = {"width": 1280, "height": 720}
VOICE = "en-US-JennyNeural"

# Overlay + spoken narration (English)
STEPS: dict[str, dict[str, str]] = {
    "intro": {
        "badge": "Step 1/10",
        "title": "VideosCreator — dub Douyin videos",
        "sub": "Paste a Douyin link → pick language & voice → download an MP4 with voiceover + captions.",
        "say": "Welcome to VideosCreator. Paste a Douyin link, pick a language and voice, then download an MP4 with dubbing and captions.",
    },
    "how": {
        "badge": "Step 1/10",
        "title": "How it works on the homepage",
        "sub": "Three steps: paste link → choose language → download your video.",
        "say": "On the homepage, you can see the flow: paste a link, choose a language, then download your finished video.",
    },
    "register_go": {
        "badge": "Step 2/10",
        "title": "Start with free sign-up",
        "sub": "Create an account to get free trial credits.",
        "say": "Start by creating a free account so you get trial credits.",
    },
    "register_fill": {
        "badge": "Step 2/10",
        "title": "Create your account",
        "sub": "Enter email and password (at least 6 characters). You get free credits after signup.",
        "say": "Enter your email and a password with at least six characters. After signup, free credits are added to your balance.",
    },
    "register_submit": {
        "badge": "Step 2/10",
        "title": "Submit registration",
        "sub": "Click Create account to enter the app.",
        "say": "Click Create account to enter the app.",
    },
    "ui_lang": {
        "badge": "Step 3/10",
        "title": "Switch the website language",
        "sub": "Top right: English / Vietnamese / more — this does not change the dubbing language on your video.",
        "say": "You can change the website language at the top right. This only changes the interface, not the dubbing language on your exported video.",
    },
    "trial": {
        "badge": "Step 4/10",
        "title": "Free Telegram trial",
        "sub": "Join the channel, then verify to get free short videos every day.",
        "say": "Join our Telegram channel and verify to unlock a free daily trial for short clips.",
    },
    "billing_go": {
        "badge": "Step 5/10",
        "title": "Top up credits in Billing",
        "sub": "Open Billing to see your balance, Trial plan, and USDT wallet.",
        "say": "Open Billing to check your balance, the Trial plan, and the USDT receiving wallet.",
    },
    "wallet": {
        "badge": "Step 5/10",
        "title": "USDT wallet (TRC20)",
        "sub": "Send only USDT on the TRC20 network. Copy the address before transferring.",
        "say": "Send only USDT on the TRC20 network. Copy the wallet address before you transfer.",
    },
    "pkg": {
        "badge": "Step 5/10",
        "title": "Choose the Trial five-dollar plan",
        "sub": "One credit is about two minutes. Longer clips cost more credits, up to thirty minutes.",
        "say": "Choose the Trial five-dollar plan. One credit covers about two minutes. Longer clips use more credits, up to thirty minutes per video.",
    },
    "tx": {
        "badge": "Step 5/10",
        "title": "Paste your transaction ID",
        "sub": "After paying, paste the TX hash from your wallet or exchange. We verify on Tronscan automatically.",
        "say": "After you pay, paste the transaction I D from your wallet or exchange. We verify it on Tronscan and add credits automatically.",
    },
    "paste": {
        "badge": "Step 6/10",
        "title": "Paste Douyin links",
        "sub": "Paste one or more links, one per line. The app quotes credits by duration.",
        "say": "On the create page, paste one or more Douyin links, one per line. The app will estimate credits by video length.",
    },
    "quote": {
        "badge": "Step 6/10",
        "title": "Check the credit quote",
        "sub": "See duration and credit cost before you start. Longer clips cost more.",
        "say": "Check the quote box for duration and credit cost before you start. Longer clips cost more credits.",
    },
    "lang": {
        "badge": "Step 7/10",
        "title": "Pick dubbing language",
        "sub": "This sets the spoken language and captions on the exported video.",
        "say": "Choose the dubbing language. This sets the spoken voice and captions on your exported video. We'll use English here.",
    },
    "voice": {
        "badge": "Step 8/10",
        "title": "Pick a voice and preview",
        "sub": "Choose male or female, then tap play to preview before creating.",
        "say": "Pick a male or female voice, then tap play to preview before creating the video.",
    },
    "start": {
        "badge": "Step 9/10",
        "title": "Start processing",
        "sub": "Credits are charged (or trial is used). Watch download → transcript → translate → dub → export.",
        "say": "Click Start processing. Credits are charged, or your trial is used. Then watch progress: download, transcript, translate, dub, and export.",
    },
    "progress": {
        "badge": "Step 9/10",
        "title": "Follow the progress page",
        "sub": "Keep this tab open. When finished, download the MP4.",
        "say": "Keep this tab open while the job runs. When it finishes, download your MP4.",
    },
    "library": {
        "badge": "Step 10/10",
        "title": "Your video library",
        "sub": "All finished videos are in Library — reopen or download anytime.",
        "say": "All finished videos are saved in Library, so you can reopen or download them anytime.",
    },
    "done": {
        "badge": "Step 10/10",
        "title": "You're ready",
        "sub": "Landing → Sign up → Trial/Billing → Paste link → Quote → Language/Voice → Create → Library.",
        "say": "That's the full flow: sign up, top up if needed, paste a Douyin link, check the quote, pick language and voice, create, then find it in Library. Happy creating!",
    },
}


def wait(page, ms: int) -> None:
    page.wait_for_timeout(ms)


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(out or "0")


async def _gen_one(text: str, path: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, VOICE, rate="+4%")
    await communicate.save(str(path))


def generate_narration() -> dict[str, Path]:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    async def _all() -> None:
        tasks = []
        for key, meta in STEPS.items():
            path = AUDIO_DIR / f"{key}.mp3"
            paths[key] = path
            tasks.append(_gen_one(meta["say"], path))
        await asyncio.gather(*tasks)

    asyncio.run(_all())
    return paths


def inject_overlay(page) -> None:
    page.evaluate(
        """() => {
      if (document.getElementById('vc-tut-overlay')) return;
      const style = document.createElement('style');
      style.textContent = `
        #vc-tut-overlay {
          position: fixed; left: 24px; right: 24px; bottom: 24px; z-index: 99999;
          pointer-events: none; font-family: "Segoe UI", system-ui, sans-serif;
        }
        #vc-tut-card {
          max-width: 780px; margin: 0 auto;
          background: rgba(8,12,20,.92); color: #fff;
          border: 1px solid rgba(255,255,255,.14);
          border-radius: 16px; padding: 14px 18px 16px;
          box-shadow: 0 18px 50px rgba(0,0,0,.45);
          backdrop-filter: blur(8px);
        }
        #vc-tut-step {
          display: inline-block; font-size: 12px; font-weight: 700;
          letter-spacing: .08em; text-transform: uppercase;
          color: #7dd3fc; margin-bottom: 6px;
        }
        #vc-tut-title {
          font-size: 22px; font-weight: 700; line-height: 1.25; margin: 0 0 4px;
        }
        #vc-tut-sub {
          font-size: 14px; line-height: 1.45; color: rgba(255,255,255,.78); margin: 0;
        }
        .vc-tut-pulse {
          outline: 3px solid #38bdf8 !important;
          outline-offset: 4px !important;
          border-radius: 10px !important;
        }
      `;
      document.head.appendChild(style);
      const box = document.createElement('div');
      box.id = 'vc-tut-overlay';
      box.innerHTML = `<div id="vc-tut-card">
        <div id="vc-tut-step"></div>
        <div id="vc-tut-title"></div>
        <p id="vc-tut-sub"></p>
      </div>`;
      document.body.appendChild(box);
      window.__vcTut = (step, title, sub) => {
        const s = document.getElementById('vc-tut-step');
        const t = document.getElementById('vc-tut-title');
        const u = document.getElementById('vc-tut-sub');
        if (s) s.textContent = step || '';
        if (t) t.textContent = title || '';
        if (u) u.textContent = sub || '';
      };
      window.__vcPulse = (sel) => {
        document.querySelectorAll('.vc-tut-pulse').forEach(el => el.classList.remove('vc-tut-pulse'));
        if (!sel) return;
        const el = document.querySelector(sel);
        if (el) {
          el.classList.add('vc-tut-pulse');
          try { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (_) {}
        }
      };
    }"""
    )


def clear_pulse(page) -> None:
    page.evaluate("() => window.__vcPulse && window.__vcPulse(null)")


def set_ui_lang(context, lang: str = "en") -> None:
    context.add_cookies(
        [
            {
                "name": "vc_ui_lang",
                "value": lang,
                "domain": "videoscreator.net",
                "path": "/",
            }
        ]
    )


def safe_click(page, selector: str) -> bool:
    loc = page.locator(selector)
    try:
        if loc.count() == 0:
            return False
        target = loc.filter(has=page.locator(":visible")).first
        if target.count() == 0:
            target = loc.first
        target.click(timeout=5000, force=True)
        return True
    except Exception:
        return False


class Timeline:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.cues: list[tuple[float, Path]] = []

    def now(self) -> float:
        return time.monotonic() - self.t0

    def speak_step(
        self,
        page,
        key: str,
        audio: dict[str, Path],
        *,
        pulse: str | None = None,
        extra_ms: int = 400,
    ) -> None:
        meta = STEPS[key]
        inject_overlay(page)
        page.evaluate(
            """([n, title, sub, pulse]) => {
              window.__vcTut(n, title, sub);
              window.__vcPulse(pulse);
            }""",
            [meta["badge"], meta["title"], meta["sub"], pulse],
        )
        path = audio[key]
        dur = probe_duration(path)
        self.cues.append((self.now(), path))
        wait(page, int(dur * 1000) + extra_ms)


def run_flow(page, email: str, password: str, audio: dict[str, Path], tl: Timeline) -> None:
    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    wait(page, 900)
    tl.speak_step(page, "intro", audio)
    page.evaluate("() => window.scrollTo({top: 480, behavior: 'smooth'})")
    wait(page, 900)
    tl.speak_step(page, "how", audio)
    page.evaluate("() => window.scrollTo({top: 0, behavior: 'smooth'})")
    wait(page, 500)

    tl.speak_step(page, "register_go", audio)
    page.goto(f"{BASE}/register", wait_until="domcontentloaded")
    wait(page, 800)
    tl.speak_step(page, "register_fill", audio, pulse="#auth-form")
    page.fill("#email", email)
    wait(page, 400)
    page.fill("#password", password)
    wait(page, 400)
    tl.speak_step(page, "register_submit", audio, pulse="#auth-btn")
    page.locator("#auth-btn").click()
    try:
        page.wait_for_url(re.compile(r"videoscreator\.net/?(\?|$)|/billing|/library"), timeout=25000)
    except Exception:
        pass
    wait(page, 1000)

    if "/register" in page.url or "/login" in page.url:
        page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        page.fill("#email", email)
        page.fill("#password", password)
        page.locator("#auth-btn").click()
        wait(page, 1800)

    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    wait(page, 900)
    tl.speak_step(page, "ui_lang", audio, pulse=".ui-lang")
    lang_sel = page.locator(".ui-lang select, select#ui-lang")
    if lang_sel.count():
        try:
            lang_sel.first.select_option("en")
            wait(page, 600)
        except Exception:
            pass

    tl.speak_step(page, "trial", audio, pulse=".trial-banner")
    trial_btn = page.locator("#trial-verify-btn")
    if trial_btn.count() and trial_btn.first.is_visible():
        try:
            with page.expect_popup(timeout=4000) as pop_info:
                trial_btn.first.click()
            pop = pop_info.value
            wait(page, 1200)
            pop.close()
        except Exception:
            wait(page, 800)
            if "telegram" in page.url or "t.me" in page.url:
                page.goto(f"{BASE}/", wait_until="domcontentloaded")
                wait(page, 700)

    tl.speak_step(page, "billing_go", audio)
    page.goto(f"{BASE}/billing", wait_until="domcontentloaded")
    wait(page, 900)
    tl.speak_step(page, "wallet", audio, pulse=".wallet-box")
    safe_click(page, "#copy-wallet")
    wait(page, 600)
    tl.speak_step(page, "pkg", audio, pulse=".pkg-hot")
    if safe_click(page, ".topup-btn:not([disabled])"):
        wait(page, 1200)
        tl.speak_step(page, "tx", audio, pulse="#topup-panel")
        page.fill("#tx_hash", "demo_tx_hash_tutorial_only_not_real")
        wait(page, 700)
        if page.locator("#note").count():
            page.fill("#note", "Tutorial demo — do not submit")
            wait(page, 500)
        clear_pulse(page)

    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    wait(page, 800)
    tl.speak_step(page, "paste", audio, pulse="#url")
    page.fill("#url", DEMO_URL)
    wait(page, 400)
    page.locator("#url").dispatch_event("input")
    page.locator("#url").dispatch_event("change")
    wait(page, 2200)
    tl.speak_step(page, "quote", audio, pulse="#quote-box")

    tl.speak_step(page, "lang", audio, pulse="#lang")
    try:
        page.select_option("#lang", "en")
    except Exception:
        pass
    wait(page, 500)

    tl.speak_step(page, "voice", audio, pulse="#voice-options")
    safe_click(page, ".voice-option, [data-voice='female'], [data-voice='male']")
    wait(page, 400)
    if safe_click(page, ".voice-play"):
        wait(page, 1200)

    tl.speak_step(page, "start", audio, pulse="#submit-btn")
    page.locator("#submit-btn").click()
    try:
        page.wait_for_url("**/jobs/**", timeout=20000)
        wait(page, 1000)
        tl.speak_step(page, "progress", audio)
        wait(page, 2500)
    except Exception:
        err = page.locator("#form-error")
        try:
            msg = err.inner_text().strip() if err.count() else ""
        except Exception:
            msg = ""
        if msg:
            inject_overlay(page)
            page.evaluate(
                """(msg) => window.__vcTut('Step 9/10', 'If you need more credits', msg)""",
                msg[:160] + " — open Billing to top up.",
            )
            wait(page, 2800)
        else:
            # still narrate progress tip
            tl.speak_step(page, "progress", audio)

    tl.speak_step(page, "library", audio)
    page.goto(f"{BASE}/library", wait_until="domcontentloaded")
    wait(page, 1000)
    tl.speak_step(page, "done", audio)
    clear_pulse(page)
    wait(page, 1200)


def mux_voiceover(video_in: Path, cues: list[tuple[float, Path]], video_out: Path) -> None:
    """Lay EN narration onto silent screen recording with adelay+amix."""
    if not cues:
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-i", str(video_in),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
                "-an", "-movflags", "+faststart", str(video_out),
            ]
        )
        return

    vdur = probe_duration(video_in)
    # Build filter: each audio delayed then amix
    inputs = ["-i", str(video_in)]
    filters = []
    labels = []
    for i, (start, path) in enumerate(cues):
        inputs += ["-i", str(path)]
        delay_ms = max(0, int(start * 1000))
        lab = f"a{i}"
        # adelay needs channel count; use aformat then adelay
        filters.append(
            f"[{i+1}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms},apad=whole_dur={vdur:.3f}[{lab}]"
        )
        labels.append(f"[{lab}]")

    n = len(labels)
    filters.append(
        f"{''.join(labels)}amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0[aout]"
    )
    fc = ";".join(filters)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", fc,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(video_out),
    ]
    subprocess.check_call(cmd)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Generating English narration…")
    audio = generate_narration()
    stamp = int(time.time())
    email = f"demo_en_{stamp}@example.com"
    password = "DemoPass123!"
    video_raw = None
    tl = Timeline()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEW,
            record_video_dir=str(OUT_DIR),
            record_video_size=REC,
            locale="en-US",
        )
        set_ui_lang(context, "en")
        page = context.new_page()
        page.set_default_timeout(20000)
        try:
            run_flow(page, email, password, audio, tl)
        finally:
            video_raw = Path(page.video.path()) if page.video else None
            context.close()
            browser.close()

    if not video_raw or not video_raw.exists():
        raise SystemExit("No video recorded")

    # Normalize raw webm → temp mp4 silent first for stable mux
    silent = OUT_DIR / "_tutorial_en_silent.mp4"
    final = OUT_DIR / "videoscreator_tutorial_en.mp4"
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-i", str(video_raw),
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-an",
            str(silent),
        ]
    )
    print(f"Muxing {len(tl.cues)} narration cues…")
    mux_voiceover(silent, tl.cues, final)
    # Keep alias updated
    alias = OUT_DIR / "videoscreator_tutorial_full.mp4"
    subprocess.check_call(["cp", "-f", str(final), str(alias)])

    print("EMAIL", email)
    print("VIDEO_RAW", video_raw)
    print("VIDEO_EN", final)
    print("DURATION", round(probe_duration(final), 1), "sec")


if __name__ == "__main__":
    main()
