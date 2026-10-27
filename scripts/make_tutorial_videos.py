# -*- coding: utf-8 -*-
"""Capture VideosCreator UI (VI) and build 2 tutorial MP4s."""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(r"C:\Github\dyflow")
OUT = ROOT / "docs" / "tutorial_guide"
SHOTS = OUT / "shots"
BASE = "http://127.0.0.1:8765"
DEMO_URL = "https://www.douyin.com/video/7671972624104197391"
FFMPEG = "ffmpeg"


def run(cmd: list[str]) -> None:
    print("+", " ".join(str(x) for x in cmd[:8]), "...")
    subprocess.run(cmd, check=True)


def shot(page, name: str) -> Path:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(450)
    path = SHOTS / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print("shot", path.name)
    return path


def set_ui_vi(page) -> None:
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    page.evaluate(
        """async () => {
          const d = new FormData();
          d.set('lang', 'vi');
          await fetch('/api/ui-lang', { method: 'POST', body: d });
        }"""
    )
    page.reload(wait_until="networkidle", timeout=60000)
    page.wait_for_selector("#url", timeout=30000)


def add(scenes: list, name: str, seconds: float, sub: str) -> None:
    scenes.append({"file": f"{name}.png", "seconds": seconds, "sub": " ".join(sub.split())})


def capture() -> list[dict]:
    scenes: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1.25,
            locale="vi-VN",
        )
        # carry UI lang cookie after set_ui_vi posts it
        page = context.new_page()
        set_ui_vi(page)

        shot(page, "01_tong_quan")
        add(
            scenes,
            "01_tong_quan",
            5.5,
            "Đây là VideosCreator — giao diện tiếng Việt. "
            "Menu trái: Tạo video, Thư viện, Đổi key, Hàng chờ dịch, Nhiều video.",
        )

        page.fill("#url", DEMO_URL)
        shot(page, "02_dan_link")
        add(
            scenes,
            "02_dan_link",
            5.0,
            "Bước 1: Dán link Douyin vào ô giữa (mỗi dòng một link). "
            "Có thể dán link kênh, hoặc bấm «Chọn video trên máy» / «Chọn thư mục».",
        )

        page.locator("#export-dir, input[name='export_dir'], #save-dir").first
        # save folder field — find by label text nearby
        shot(page, "03_thu_muc_luu")
        add(
            scenes,
            "03_thu_muc_luu",
            4.0,
            "Chọn «Thư mục lưu video» trước khi bắt đầu. "
            "Khi job xong, file MP4 sẽ tự lưu vào thư mục này.",
        )

        page.select_option("#preset_name", "neon")
        shot(page, "04_phu_de")
        add(
            scenes,
            "04_phu_de",
            4.0,
            "Cột phải — «Hình · chữ trên video»: chọn kiểu phụ đề "
            "(Cổ điển, Neon, Hộp đen…).",
        )

        page.check("#logo-enable")
        page.select_option("#logo_position", "top_right")
        page.fill("#logo_size_percent", "12")
        page.select_option("#video_speed", "1.0")
        shot(page, "05_logo_toc_do")
        add(
            scenes,
            "05_logo_toc_do",
            4.5,
            "Tuỳ chọn: bật «Chèn logo tĩnh», chọn vị trí và cỡ logo. "
            "Bên dưới chỉnh tốc độ phát lại video nếu cần.",
        )

        page.select_option("#lang", "vi")
        page.locator("input[name='translate_engine'][value='openai']").check()
        page.select_option("#genre_id", "do_thi")
        # ensure Edge TTS visible
        page.locator("#tts-options").scroll_into_view_if_needed()
        shot(page, "06_ngon_ngu_giọng")
        add(
            scenes,
            "06_ngon_ngu_giọng",
            6.5,
            "Chọn ngôn ngữ dịch / lồng tiếng (ví dụ Tiếng Việt). "
            "Dịch thoại: Google miễn phí hoặc OpenAI (mặc định nếu đã lưu key). "
            "Chọn thể loại video cho đúng văn phong. "
            "Engine: Edge TTS miễn phí hoặc OpenAI TTS.",
        )

        # show genre customize
        page.locator("#genre-prompt-toggle").evaluate(
            "el => { el.hidden = false; el.style.display = 'inline-flex'; }"
        )
        if page.locator("#genre-prompt-toggle").is_visible():
            page.click("#genre-prompt-toggle")
            page.wait_for_timeout(400)
        else:
            # force panel open for demo
            page.evaluate(
                """() => {
                  const p = document.getElementById('genre-prompt-panel');
                  const t = document.getElementById('genre-prompt-toggle');
                  if (t) { t.hidden = false; }
                  if (p) { p.hidden = false; }
                }"""
            )
            page.wait_for_timeout(300)
        shot(page, "07_the_loai")
        add(
            scenes,
            "07_the_loai",
            4.5,
            "Chọn thể loại (Đô thị, Tiên hiệp…). "
            "Chỉ mở «Tùy chỉnh văn phong» khi muốn sửa prompt — "
            "ô này chỉ tả văn phong, không dán luật JSON/timecode.",
        )

        page.evaluate(
            """() => {
              const p = document.getElementById('genre-prompt-panel');
              if (p) p.hidden = true;
            }"""
        )
        page.locator("#submit-btn").scroll_into_view_if_needed()
        shot(page, "08_bat_dau")
        add(
            scenes,
            "08_bat_dau",
            5.5,
            "Khi đủ link + thư mục lưu + ngôn ngữ: bấm «Bắt đầu dịch». "
            "Tool chạy 5 bước: Tải clip → Transcript → Dịch + phụ đề → "
            "Tạo giọng → Xuất bản. Theo dõi nhật ký bên dưới.",
        )

        page.goto(BASE + "/queue", wait_until="networkidle", timeout=60000)
        shot(page, "09_hang_cho")
        add(
            scenes,
            "09_hang_cho",
            4.0,
            "Vào «Hàng chờ dịch» để xem video đang chờ / đang chạy, "
            "huỷ từng job hoặc huỷ tất cả.",
        )

        page.goto(BASE + "/batch", wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#url", timeout=20000)
        page.fill(
            "#url",
            "https://www.douyin.com/video/7671972624104197391\n"
            "https://www.douyin.com/video/7644495247610776841",
        )
        shot(page, "10_nhieu_video")
        add(
            scenes,
            "10_nhieu_video",
            4.5,
            "Mục «Nhiều video»: dán nhiều link (mỗi dòng một video) "
            "hoặc một link kênh, chọn ngôn ngữ rồi đưa vào hàng chờ.",
        )

        page.goto(BASE + "/library", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(800)
        shot(page, "11_thu_vien")
        add(
            scenes,
            "11_thu_vien",
            5.0,
            "«Thư viện» chứa video đã xử lý xong. "
            "Mở một video để xem lại, tải MP4 về máy.",
        )

        # open first job detail if links exist
        link = page.locator("a[href*='/j/'], a[href*='/job'], a[href*='web_']").first
        if link.count() == 0:
            link = page.locator(".library a, .job-row a, article a, .card a").first
        if link.count():
            try:
                link.click(timeout=3000)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(700)
            except Exception as e:
                print("open job skip:", e)
        shot(page, "12_chi_tiet_job")
        add(
            scenes,
            "12_chi_tiet_job",
            5.5,
            "Trong chi tiết: theo dõi các bước xử lý, nhật ký, "
            "xem trước kết quả và tải video MP4.",
        )

        page.goto(BASE + "/license", wait_until="networkidle", timeout=60000)
        shot(page, "13_doi_key")
        add(
            scenes,
            "13_doi_key",
            4.0,
            "«Đổi key» để nhập license phần mềm. "
            "Key OpenAI (dịch/TTS) lưu ở màn Tạo video — nút Lưu key.",
        )

        page.goto(BASE + "/", wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#url", timeout=20000)
        shot(page, "14_ket_thuc")
        add(
            scenes,
            "14_ket_thuc",
            5.0,
            "Tóm tắt nhanh: dán link → thư mục lưu → ngôn ngữ & engine → "
            "Bắt đầu dịch → theo dõi Hàng chờ → lấy file ở Thư viện. "
            "Chúc bạn dùng tool thuận lợi!",
        )

        browser.close()

    (OUT / "scenes.json").write_text(
        json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return scenes


def write_srt(scenes: list[dict], path: Path) -> None:
    lines: list[str] = []
    t = 0.0

    def ts(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        if ms == 1000:
            s += 1
            ms = 0
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    for i, sc in enumerate(scenes, 1):
        dur = float(sc["seconds"])
        start, end = t, t + dur - 0.08
        lines.append(str(i))
        lines.append(f"{ts(start)} --> {ts(max(end, start + 0.5))}")
        text = sc["sub"]
        wrap: list[str] = []
        cur = ""
        for w in text.split():
            if len(cur) + len(w) + 1 > 42:
                wrap.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            wrap.append(cur)
        lines.append("\n".join(wrap[:4]))
        lines.append("")
        t += dur
    path.write_text("\n".join(lines), encoding="utf-8")


def write_concat(scenes: list[dict], path: Path) -> None:
    chunks: list[str] = []
    for sc in scenes:
        img = (SHOTS / sc["file"]).resolve().as_posix().replace("'", r"'\''")
        chunks.append(f"file '{img}'")
        chunks.append(f"duration {float(sc['seconds'])}")
    last = (SHOTS / scenes[-1]["file"]).resolve().as_posix().replace("'", r"'\''")
    chunks.append(f"file '{last}'")
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")


def build_videos(scenes: list[dict]) -> tuple[Path, Path]:
    concat = OUT / "concat.txt"
    srt = OUT / "huong_dan_vi.srt"
    write_concat(scenes, concat)
    write_srt(scenes, srt)

    ops = OUT / "VideosCreator_huong_dan_thao_tac.mp4"
    with_subs = OUT / "VideosCreator_huong_dan_co_phu_de_VI.mp4"

    vf_base = (
        "scale=1440:900:force_original_aspect_ratio=decrease,"
        "pad=1440:900:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
    )
    run(
        [
            FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", vf_base,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-movflags", "+faststart", str(ops),
        ]
    )

    # Windows path for subtitles filter
    srt_path = str(srt.resolve()).replace("\\", "/").replace(":", "\\:")
    vf_sub = (
        "scale=1440:900:force_original_aspect_ratio=decrease,"
        "pad=1440:900:(ow-iw)/2:(oh-ih)/2,fps=30,"
        f"subtitles='{srt_path}':force_style='"
        "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H80000000,BorderStyle=3,Outline=2,Shadow=0,"
        "MarginV=40,Alignment=2',format=yuv420p"
    )
    run(
        [
            FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", vf_sub,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-movflags", "+faststart", str(with_subs),
        ]
    )
    return ops, with_subs


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if SHOTS.exists():
        shutil.rmtree(SHOTS)
    SHOTS.mkdir(parents=True, exist_ok=True)
    urllib.request.urlopen(BASE + "/", timeout=5).read(200)
    scenes = capture()
    assert all((SHOTS / s["file"]).is_file() for s in scenes), "missing shots"
    ops, subs = build_videos(scenes)
    print("DONE")
    print(ops, ops.stat().st_size)
    print(subs, subs.stat().st_size)
    print("duration_sec", round(sum(float(s["seconds"]) for s in scenes), 1))


if __name__ == "__main__":
    main()
