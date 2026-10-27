# -*- coding: utf-8 -*-
"""Generate a complete, high-definition Vietnamese tutorial video for VideosCreator.
Features:
- Expressive, conversational Vietnamese male voiceover (vi-VN-NamMinhNeural) with +10% speed.
- Engaging, tutor-style narration guiding viewers step-by-step.
- Clean 1080p Full HD UI without bulky subtitles at the bottom.
- Prominent pointer arrows & glowing highlight frames pointing directly to each feature in every step!
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import edge_tts
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(r"C:\Github\dyflow")
OUT = ROOT / "output" / "tutorial_video"
SHOTS = OUT / "shots"
AUDIO = OUT / "audio"
TMP = OUT / "tmp"
BASE = "http://127.0.0.1:8765"
VOICE = "vi-VN-NamMinhNeural"  # Natural, expressive Vietnamese male voice
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

SCENES_DEF = [
    {
        "id": "01_tong_quan",
        "badge": "TỔNG QUAN · VIDEOSCREATOR",
        "action": "home",
        "target_sel": ".studio-rail",
        "arrow_dir": "right",
        "callout": "Menu tính năng chính",
        "say": (
            "Chào mừng các bạn đã đến với VideosCreator! "
            "Trong clip này, mình sẽ hướng dẫn chi tiết từng bước để các bạn làm chủ trọn bộ công cụ tạo video tự động cực kỳ nhanh chóng. "
            "Nhìn sang thanh menu bên trái, chúng ta có đầy đủ các tính năng: Tạo video mới, Quản lý hàng chờ, Thư viện thành phẩm và Đổi mã bản quyền rất tiện lợi nhé!"
        ),
    },
    {
        "id": "02_dan_link_douyin",
        "badge": "BƯỚC 1 · DÁN LINK DOUYIN",
        "action": "fill_link",
        "target_sel": "#url",
        "arrow_dir": "right",
        "callout": "Dán link Douyin tại đây",
        "say": (
            "Bước đầu tiên là nạp nguồn video. Nếu bạn muốn dịch clip từ Douyin, chỉ cần dán đường link vào ô này thôi nhé. "
            "Các bạn có thể dán một video, nhiều video mỗi dòng một link, hoặc dán thẳng link cả kênh để phần mềm tự động quét trọn gói toàn bộ danh sách!"
        ),
    },
    {
        "id": "03_chon_video_tu_may",
        "badge": "BƯỚC 1 · CHỌN VIDEO TỪ MÁY TÍNH",
        "action": "pick_local_files",
        "target_sel": "#local-pick-files",
        "arrow_dir": "right",
        "callout": "Bấm chọn video có sẵn trên máy",
        "say": (
            "Còn nếu bạn đã có sẵn các file video trên máy tính thì sao? Rất đơn giản, hãy bấm vào nút Chọn video trên máy để nạp các file MP4, "
            "hoặc bấm Chọn thư mục nếu bạn muốn thêm nhanh toàn bộ các tập phim trong folder vào app nhé."
        ),
    },
    {
        "id": "04_chon_tat_ca_bo_chon",
        "badge": "TÍNH NĂNG MỚI · CHỌN LỌC TẬP PHIM",
        "action": "toggle_local_picks",
        "target_sel": "#quote-toolbar",
        "arrow_dir": "right",
        "callout": "Nút Chọn tất cả, Bỏ chọn & Checkbox",
        "say": (
            "Một tính năng mới rất hữu ích ở đây: mỗi tập phim đều có một ô tích chọn riêng. "
            "Các bạn chỉ cần bấm nút Chọn tất cả hoặc Bỏ chọn để thao tác hàng loạt cực nhanh, "
            "hoặc tick chọn đúng những tập phim bạn muốn làm. Tool sẽ chỉ xử lý đúng những video đã được chọn thôi nhé!"
        ),
    },
    {
        "id": "05_thu_muc_luu",
        "badge": "BƯỚC 2 · THƯ MỤC LƯU VIDEO XUẤT",
        "action": "highlight_save_dir",
        "target_sel": "#save-path-input",
        "arrow_dir": "right",
        "callout": "Chọn thư mục lưu video hoàn thành",
        "say": (
            "Sang bước thứ hai, các bạn đừng quên bấm chọn Thư mục lưu video ở thanh này nhé. "
            "Khi render xong, toàn bộ video thành phẩm sẽ được tự động xuất và lưu ngăn nắp ngay tại thư mục bạn đã chỉ định trên máy tính."
        ),
    },
    {
        "id": "06_kieu_chu_phu_de",
        "badge": "BƯỚC 3 · KIỂU CHỮ & PHỤ ĐỀ",
        "action": "preset_and_speed",
        "target_sel": "#preset_name",
        "arrow_dir": "left",
        "callout": "Chọn mẫu chữ phụ đề & Tốc độ",
        "say": (
            "Bước thứ ba là làm đẹp cho video. Ở cột bên phải, mục Hình chữ trên video cho phép bạn chọn các mẫu phụ đề cực bắt mắt "
            "như Cổ điển, Neon hay Hộp đen để video cuốn hút hơn. "
            "Ngay phía dưới, các bạn còn có thể tăng tốc độ phát lại video lên một chấm một lần để thời lượng ngắn gọn và giữ chân người xem tốt nhất."
        ),
    },
    {
        "id": "07_chen_logo_ban_quyen",
        "badge": "BƯỚC 3 · CHÈN LOGO THƯƠNG HIỆU",
        "action": "logo_settings",
        "target_sel": "#logo-box",
        "arrow_dir": "left",
        "callout": "Bật logo bản quyền & Chỉnh kích thước",
        "say": (
            "Để bảo vệ bản quyền kênh và chống reup, bạn chỉ cần tích vào ô Chèn logo tĩnh ở đây. "
            "Sau đó chọn file ảnh logo của bạn, tùy chỉnh vị trí góc hiển thị cùng kích thước sao cho chuyên nghiệp và hài hòa nhất nhé!"
        ),
    },
    {
        "id": "08_ngon_ngu_cong_cu_dich",
        "badge": "BƯỚC 4 · CẤU HÌNH DỊCH THUẬT",
        "action": "lang_and_engine",
        "target_sel": "#translate-engine-field",
        "arrow_dir": "right",
        "callout": "Chọn ngôn ngữ & Công cụ dịch DeepSeek",
        "say": (
            "Bây giờ đến phần quan trọng nhất: Cấu hình dịch thuật. Bạn chọn ngôn ngữ dịch sang Tiếng Việt. "
            "Về công nghệ dịch, mình khuyên các bạn nên chọn DeepSeek AI để lời thoại dịch theo đúng ngữ cảnh phim mượt mà và tự nhiên nhất, "
            "hoặc bạn cũng có thể chọn Google Dịch hoàn toàn miễn phí."
        ),
    },
    {
        "id": "09_the_loai_bat_buoc",
        "badge": "BƯỚC 4 · THỂ LOẠI VIDEO (BẮT BUỘC)",
        "action": "genre_and_prompt",
        "target_sel": "#genre_id",
        "arrow_dir": "right",
        "callout": "Bắt buộc chọn thể loại (*) để AI dịch chuẩn",
        "say": (
            "Đặc biệt lưu ý nha, mục Thể loại video có dấu sao đỏ này là bắt buộc các bạn phải chọn nhé! "
            "Tùy theo thể loại phim như Đô thị, Tiên hiệp hay Ngôn tình, AI sẽ tự động điều chỉnh lời thoại và xưng hô chuẩn phong cách nhân vật. "
            "Bạn cũng có thể mở tùy chỉnh văn phong ở đây nếu muốn thêm prompt sáng tạo theo ý mình."
        ),
    },
    {
        "id": "10_giong_doc_edge_tts",
        "badge": "BƯỚC 4 · CHỌN GIỌNG ĐỌC AI",
        "action": "voice_options",
        "target_sel": "#tts-options",
        "arrow_dir": "right",
        "callout": "Chọn giọng đọc AI Nam / Nữ miễn phí",
        "say": (
            "Về phần lồng tiếng, phần mềm tích hợp sẵn công nghệ Edge TTS chất lượng cao hoàn toàn miễn phí. "
            "Các bạn có thể lựa chọn giọng Nam Minh trầm ấm, truyền cảm, hoặc giọng Nữ Hoài My dịu dàng, ngọt ngào rất tự nhiên."
        ),
    },
    {
        "id": "11_bat_dau_quy_trinh",
        "badge": "BƯỚC 5 · BẮT ĐẦU DỊCH & TIẾN ĐỘ",
        "action": "start_and_pipeline",
        "target_sel": "#submit-btn",
        "arrow_dir": "right",
        "callout": "Bấm để bắt đầu quy trình tự động",
        "say": (
            "Thiết lập xong xuôi rồi, bạn chỉ việc bấm nút Bắt đầu dịch và lồng tiếng ở đây thôi! "
            "Hệ thống sẽ tự động thực hiện trọn gói cả năm bước từ nhận diện giọng nói, dịch thoại, thu âm lồng tiếng cho đến xuất video. "
            "Các bạn có thể theo dõi tiến độ chạy chi tiết ngay tại khung nhật ký bên dưới."
        ),
    },
    {
        "id": "12_hang_cho_dich",
        "badge": "QUẢN LÝ · HÀNG CHỜ DỊCH",
        "action": "goto_queue",
        "target_sel": ".studio-log, table, .btn.danger, h1",
        "arrow_dir": "right",
        "callout": "Quản lý các video đang chạy & Đang chờ",
        "say": (
            "Khi xử lý cùng lúc nhiều video, các bạn hãy vào mục Hàng chờ dịch trên menu nhé. "
            "Tại đây, bạn sẽ dễ dàng kiểm tra danh sách video đang chạy và đang chờ, "
            "hoặc bấm nút Huỷ tất cả nếu muốn dừng lại các video đã thêm nhầm cực kỳ nhanh gọn."
        ),
    },
    {
        "id": "13_thu_vien_video",
        "badge": "QUẢN LÝ · THƯ VIỆN VIDEO",
        "action": "goto_library",
        "target_sel": ".library-grid, .library-card, .btn",
        "arrow_dir": "top",
        "callout": "Xem lại video & Tải file MP4 về máy",
        "say": (
            "Sau khi xử lý xong, mời các bạn vào mục Thư viện để chiêm ngưỡng thành quả! "
            "Bạn có thể xem trước trực tiếp video với phụ đề và giọng đọc mới, "
            "hoặc bấm nút Tải về máy để lấy ngay file MP4 chất lượng cao sẵn sàng đăng tải lên mạng xã hội."
        ),
    },
    {
        "id": "14_doi_key_ban_quyen",
        "badge": "CÀI ĐẶT · ĐỔI KEY BẢN QUYỀN",
        "action": "goto_license",
        "target_sel": "#license-key",
        "arrow_dir": "right",
        "callout": "Dán mã bản quyền vào đây rồi bấm Lưu key",
        "say": (
            "Bên cạnh đó, khi các bạn cần kích hoạt hoặc gia hạn tài khoản, hãy vào mục Đổi key trên thanh menu. "
            "Chỉ cần dán mã bản quyền được cấp vào ô này rồi nhấn Lưu key là bạn đã có thể sử dụng thoải mái mọi tính năng không giới hạn rồi!"
        ),
    },
    {
        "id": "15_tong_ket",
        "badge": "TỔNG KẾT · CHÚC BẠN THÀNH CÔNG",
        "action": "finish_home",
        "target_sel": ".studio-box",
        "arrow_dir": "top",
        "callout": "Chúc bạn tạo video triệu view thành công!",
        "say": (
            "Các bạn thấy đấy, quy trình với VideosCreator cực kỳ đơn giản và nhanh chóng: "
            "chọn video, cài đặt thể loại cùng giọng đọc, bấm Bắt đầu và nhận ngay video hoàn chỉnh trong Thư viện! "
            "Chúc các bạn sáng tạo nên thật nhiều video triệu view và thành công rực rỡ nhé!"
        ),
    },
]


def get_audio_duration(path: Path) -> float:
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


async def generate_voice(text: str, out_path: Path, rate: str = "+10%") -> float:
    if out_path.exists() and out_path.stat().st_size > 1000:
        return get_audio_duration(out_path)

    for attempt in range(1, 5):
        try:
            communicate = edge_tts.Communicate(text, VOICE, rate=rate)
            await communicate.save(str(out_path))
            if out_path.exists() and out_path.stat().st_size > 1000:
                await asyncio.sleep(0.3)
                return get_audio_duration(out_path)
        except Exception as e:
            print(f"    [WARN] Edge-TTS attempt {attempt} failed ({e}), retrying in 1.5s...")
            await asyncio.sleep(1.5)

    # Fallback to alternate voice if needed
    communicate = edge_tts.Communicate(text, "vi-VN-HoaiMyNeural", rate=rate)
    await communicate.save(str(out_path))
    await asyncio.sleep(0.3)
    return get_audio_duration(out_path)


def make_dummy_videos() -> list[Path]:
    TMP.mkdir(parents=True, exist_ok=True)
    paths = []
    names = ["Tap_01_Thu_Hung.mp4", "Tap_02_Quyet_Dau.mp4", "Tap_03_Dai_Ket_Cuc.mp4"]
    for name in names:
        p = TMP / name
        if not p.exists():
            subprocess.run(
                [
                    FFMPEG,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=320x240:d=1",
                    "-c:v",
                    "libx264",
                    str(p),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        paths.append(p)
    return paths


def draw_pointer_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color=(255, 215, 0)):
    """Draw a bold, professional arrow with black border and sharp arrowhead."""
    sx, sy = start
    ex, ey = end

    # Outer black stroke
    draw.line([sx, sy, ex, ey], fill=(0, 0, 0, 255), width=14)
    # Inner bright neon line
    draw.line([sx, sy, ex, ey], fill=color, width=8)

    # Arrowhead geometry
    angle = math.atan2(ey - sy, ex - sx)
    head_len = 32
    head_angle = math.pi / 5.5

    p1 = (ex - head_len * math.cos(angle - head_angle), ey - head_len * math.sin(angle - head_angle))
    p2 = (ex - head_len * math.cos(angle + head_angle), ey - head_len * math.sin(angle + head_angle))

    # Outer black triangle
    p1_out = (ex - (head_len + 5) * math.cos(angle - head_angle), ey - (head_len + 5) * math.sin(angle - head_angle))
    p2_out = (ex - (head_len + 5) * math.cos(angle + head_angle), ey - (head_len + 5) * math.sin(angle + head_angle))
    draw.polygon([(ex, ey), p1_out, p2_out], fill=(0, 0, 0, 255))

    # Inner bright polygon
    draw.polygon([(ex, ey), p1, p2], fill=color, outline=(0, 0, 0, 255))


def decorate_scene_with_pointer(
    img_path: Path,
    badge_text: str,
    target_box: dict | None,
    arrow_dir: str,
    callout_text: str,
) -> None:
    """Draw stylish top-left step pill, glowing highlight border around the element,
    and a bold neon pointer arrow pointing directly to the feature with callout text."""
    img = Image.open(img_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)

    # Fonts
    try:
        font_badge = ImageFont.truetype("arialbd.ttf", 26)
        font_callout = ImageFont.truetype("arialbd.ttf", 22)
    except Exception:
        try:
            font_badge = ImageFont.truetype("segoeuib.ttf", 26)
            font_callout = ImageFont.truetype("segoeuib.ttf", 22)
        except Exception:
            font_badge = ImageFont.load_default()
            font_callout = ImageFont.load_default()

    # 1. Top-Left Step Badge
    bbox = ov_draw.textbbox((0, 0), badge_text, font=font_badge)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    bx, by = 40, 36
    pad_x, pad_y = 22, 12
    bw, bh = tw + pad_x * 2, th + pad_y * 2
    badge_rect = [bx, by, bx + bw, by + bh]

    ov_draw.rounded_rectangle(badge_rect, radius=10, fill=(18, 18, 26, 235))
    ov_draw.rounded_rectangle(badge_rect, radius=10, outline=(254, 44, 85, 255), width=2)
    ov_draw.text((bx + pad_x, by + pad_y - 2), badge_text, fill=(255, 255, 255, 255), font=font_badge)

    # 2. Highlight Box & Pointer Arrow around target
    if target_box:
        tx = int(target_box.get("x", 0))
        ty = int(target_box.get("y", 0))
        tw_box = int(target_box.get("width", 0))
        th_box = int(target_box.get("height", 0))

        # Clamp boundaries inside screen
        tx = max(10, min(tx, 1900))
        ty = max(10, min(ty, 1060))
        tw_box = max(20, min(tw_box, 1920 - tx - 10))
        th_box = max(20, min(th_box, 1080 - ty - 10))

        # Highlight rectangle
        hl_rect = [tx - 6, ty - 6, tx + tw_box + 6, ty + th_box + 6]
        # Soft outer glow
        ov_draw.rounded_rectangle(hl_rect, radius=10, outline=(255, 220, 0, 120), width=6)
        # Sharp inner border
        ov_draw.rounded_rectangle(hl_rect, radius=10, outline=(255, 225, 0, 255), width=3)

        # Arrow calculations
        color_arrow = (255, 220, 0, 255)  # Bright neon golden yellow
        arr_len = 110

        if arrow_dir == "right":
            # Points from right to left into target
            end_pt = (tx + tw_box + 10, ty + th_box // 2)
            start_pt = (min(1900, end_pt[0] + arr_len), end_pt[1])
            callout_pos = (start_pt[0] + 15, start_pt[1] - 22)
            if callout_pos[0] + 300 > 1910:
                # Place callout above if near right edge
                callout_pos = (max(20, end_pt[0] - 100), max(20, end_pt[1] - 70))
        elif arrow_dir == "left":
            # Points from left to right into target
            end_pt = (max(10, tx - 10), ty + th_box // 2)
            start_pt = (max(20, end_pt[0] - arr_len), end_pt[1])
            callout_pos = (max(20, start_pt[0] - 280), start_pt[1] - 22)
        elif arrow_dir == "top":
            # Points from above down into target
            end_pt = (tx + tw_box // 2, max(10, ty - 10))
            start_pt = (end_pt[0], max(20, end_pt[1] - arr_len))
            callout_pos = (max(20, start_pt[0] - 120), max(20, start_pt[1] - 50))
        else:  # bottom
            end_pt = (tx + tw_box // 2, min(1070, ty + th_box + 10))
            start_pt = (end_pt[0], min(1060, end_pt[1] + arr_len))
            callout_pos = (max(20, start_pt[0] - 120), min(1030, start_pt[1] + 15))

        # Draw the Arrow
        draw_pointer_arrow(ov_draw, start_pt, end_pt, color=color_arrow)

        # 3. Callout Box Tag
        if callout_text:
            c_tag = f"👉 {callout_text}"
            c_bbox = ov_draw.textbbox((0, 0), c_tag, font=font_callout)
            cw, ch = c_bbox[2] - c_bbox[0], c_bbox[3] - c_bbox[1]
            cx, cy = callout_pos
            cx = max(20, min(cx, 1920 - cw - 40))
            cy = max(20, min(cy, 1080 - ch - 30))

            c_rect = [cx - 14, cy - 8, cx + cw + 14, cy + ch + 8]
            ov_draw.rounded_rectangle(c_rect, radius=8, fill=(15, 15, 22, 240))
            ov_draw.rounded_rectangle(c_rect, radius=8, outline=(255, 220, 0, 255), width=2)
            ov_draw.text((cx, cy - 2), c_tag, fill=(255, 255, 255, 255), font=font_callout)

    final_img = Image.alpha_composite(img, overlay)
    final_img.convert("RGB").save(img_path, quality=95)


def capture_all_scenes(force: bool = False) -> list[dict]:
    all_exist = all((SHOTS / f"{sc['id']}.png").exists() for sc in SCENES_DEF)
    if all_exist and not force:
        print("[*] Reusing all 15 decorated UI screenshots with arrows...")
        return [
            {
                "id": sc["id"],
                "img": SHOTS / f"{sc['id']}.png",
                "say": sc["say"],
                "badge": sc["badge"],
            }
            for sc in SCENES_DEF
        ]

    dummy_files = make_dummy_videos()
    scenes = []

    print("[*] Launching browser to capture scenes and exact feature coordinates...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1.0,
            locale="vi-VN",
        )
        page = context.new_page()

        # Set UI language to vi
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        page.evaluate(
            """async () => {
              const d = new FormData();
              d.set('lang', 'vi');
              await fetch('/api/ui-lang', { method: 'POST', body: d });
            }"""
        )
        page.reload(wait_until="networkidle", timeout=30000)
        page.wait_for_selector("#url", timeout=20000)

        for sc in SCENES_DEF:
            sc_id = sc["id"]
            action = sc["action"]
            img_file = SHOTS / f"{sc_id}.png"
            target_sel = sc.get("target_sel")

            print(f"[*] Capturing: {sc_id} -> {sc['callout']}")

            if action == "home":
                page.goto(BASE + "/", wait_until="networkidle", timeout=30000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(400)

            elif action == "fill_link":
                page.goto(BASE + "/", wait_until="networkidle", timeout=30000)
                page.fill("#url", "https://www.douyin.com/video/7671972624104197391")
                page.wait_for_timeout(500)

            elif action == "pick_local_files":
                page.fill("#url", "")
                page.set_input_files("#local-files", [str(x) for x in dummy_files])
                page.wait_for_timeout(800)
                page.evaluate("document.getElementById('quote-box').scrollIntoView({behavior: 'smooth', block: 'center'})")
                page.wait_for_timeout(500)

            elif action == "toggle_local_picks":
                cb = page.locator("input[name='local_pick']").nth(1)
                if cb.count() > 0:
                    cb.uncheck()
                page.wait_for_timeout(500)

            elif action == "highlight_save_dir":
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(500)

            elif action == "preset_and_speed":
                page.select_option("#preset_name", "neon")
                page.select_option("#video_speed", "1.1")
                page.evaluate("document.getElementById('preset_name').scrollIntoView({behavior: 'smooth', block: 'center'})")
                page.wait_for_timeout(500)

            elif action == "logo_settings":
                page.check("#logo-enable")
                page.select_option("#logo_position", "top_right")
                page.fill("#logo_size_percent", "12")
                page.wait_for_timeout(500)

            elif action == "lang_and_engine":
                page.select_option("#lang", "vi")
                ds_radio = page.locator("input[name='translate_engine'][value='deepseek']")
                if ds_radio.count():
                    ds_radio.check()
                page.evaluate("document.getElementById('deepseek-key-box').hidden = false")
                page.evaluate("const el = document.getElementById('home-deepseek-key'); if (el) el.value = 'sk-sample-deepseek-api-key-here';")
                page.evaluate("document.getElementById('translate-engine-field').scrollIntoView({behavior: 'smooth', block: 'center'})")
                page.wait_for_timeout(500)

            elif action == "genre_and_prompt":
                page.select_option("#genre_id", "do_thi")
                page.evaluate(
                    """() => {
                  const p = document.getElementById('genre-prompt-panel');
                  const t = document.getElementById('genre-prompt-toggle');
                  if (t) t.hidden = false;
                  if (p) p.hidden = false;
                  const pr = document.getElementById('genre_prompt');
                  if (pr) pr.value = 'Xưng hô: tôi - cậu. Giọng văn hiện đại, hào sảng.';
                }"""
                )
                page.evaluate("document.getElementById('genre_id').scrollIntoView({behavior: 'smooth', block: 'center'})")
                page.wait_for_timeout(500)

            elif action == "voice_options":
                page.select_option("#tts_provider", "edge")
                page.select_option("#edge_voice", "vi-VN-NamMinhNeural")
                page.evaluate("document.getElementById('tts-options').scrollIntoView({behavior: 'smooth', block: 'center'})")
                page.wait_for_timeout(500)

            elif action == "start_and_pipeline":
                page.evaluate("document.getElementById('submit-btn').scrollIntoView({behavior: 'smooth', block: 'center'})")
                page.wait_for_timeout(500)

            elif action == "goto_queue":
                page.goto(BASE + "/tasks", wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(600)

            elif action == "goto_library":
                page.goto(BASE + "/library", wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(600)

            elif action == "goto_license":
                page.goto(BASE + "/license", wait_until="networkidle", timeout=30000)
                page.fill("#license-key", "DYFLOW-PRO-DEMO-TUTORIAL-KEY")
                page.wait_for_timeout(500)

            elif action == "finish_home":
                page.goto(BASE + "/", wait_until="networkidle", timeout=30000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(500)

            # Get target element bbox
            target_box = None
            if target_sel:
                try:
                    for sel in target_sel.split(","):
                        sel = sel.strip()
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible():
                            target_box = loc.bounding_box()
                            if target_box:
                                break
                except Exception as e:
                    print(f"    [WARN] get bbox failed for {target_sel}: {e}")

            # Capture screenshot
            page.screenshot(path=str(img_file), full_page=False)

            # Decorate screenshot with step badge + highlight box + arrow + callout
            decorate_scene_with_pointer(
                img_file,
                sc["badge"],
                target_box,
                sc.get("arrow_dir", "right"),
                sc.get("callout", ""),
            )

            scenes.append(
                {
                    "id": sc_id,
                    "img": img_file,
                    "say": sc["say"],
                    "badge": sc["badge"],
                }
            )

        browser.close()

    return scenes


def main():
    print("=== VIDEOSCREATOR TUTORIAL VIDEO GENERATOR ===")
    OUT.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)

    # 1. Capture/load all scenes with exact pointer arrows
    scenes = capture_all_scenes()

    # 2. Generate voiceover and render
    asyncio.run(build_audio_and_video(scenes))


async def build_audio_and_video(scenes: list[dict]):
    print(f"\n[*] Generating expressive Vietnamese male voiceover ({VOICE}) at rate=+10%...")
    scenes_data = []
    concat_audio_files = []

    for sc in scenes:
        sc_id = sc["id"]
        audio_file = AUDIO / f"{sc_id}_fast_male.mp3"
        print(f"  -> Spoken voice: {sc_id}")
        dur = await generate_voice(sc["say"], audio_file, rate="+10%")
        # Add 0.4s natural pause between scenes
        scene_dur = round(dur + 0.4, 2)
        sc["duration"] = scene_dur
        sc["audio"] = audio_file
        scenes_data.append(sc)
        concat_audio_files.append(audio_file)

    # 3. Create audio concatenation list
    audio_concat_txt = OUT / "audio_concat.txt"
    with audio_concat_txt.open("w", encoding="utf-8") as f:
        for a in concat_audio_files:
            f.write(f"file '{a.resolve().as_posix()}'\n")

    master_audio = OUT / "master_audio_fast_male.mp3"
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(audio_concat_txt),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(master_audio),
        ],
        check=True,
    )
    total_audio_dur = get_audio_duration(master_audio)
    print(f"[+] Master audio generated: {total_audio_dur:.2f}s")

    # 4. Create image concat list
    image_concat_txt = OUT / "image_concat.txt"
    with image_concat_txt.open("w", encoding="utf-8") as f:
        for sc in scenes_data:
            f.write(f"file '{sc['img'].resolve().as_posix()}'\n")
            f.write(f"duration {sc['duration']}\n")
        f.write(f"file '{scenes_data[-1]['img'].resolve().as_posix()}'\n")

    # 5. Final Video Rendering (NO big vietsub at bottom, clean 1080p Full HD!)
    output_video = OUT / "VideosCreator_Huong_Dan_Chi_Tiet.mp4"
    print("\n[*] Rendering Final Clean 1080p Video (No Subtitles at bottom, with Arrow Pointers)...")

    vf_clean = (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
    )

    cmd = [
        FFMPEG,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(image_concat_txt),
        "-i",
        str(master_audio),
        "-vf",
        vf_clean,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_video),
    ]

    subprocess.run(cmd, check=True)
    print(f"\n[SUCCESS] Completed tutorial video!")
    print(f"File: {output_video}")
    print(f"Size: {output_video.stat().st_size / 1024 / 1024:.2f} MB")
    print(f"Duration: {get_audio_duration(output_video):.1f}s")

    dist_dir = ROOT / "dist"
    if dist_dir.exists():
        dist_video = dist_dir / "VideosCreator_Huong_Dan_Chi_Tiet.mp4"
        shutil.copy2(output_video, dist_video)
        print(f"[+] Copied to dist: {dist_video}")


if __name__ == "__main__":
    main()
