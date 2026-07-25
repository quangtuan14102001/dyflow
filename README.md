# dyflow

Pipeline hoàn chỉnh: **Douyin → biến đổi sâu → lồng tiếng Việt → caption chạy trên video → export đa tỉ lệ → tải về máy**. Chạy CPU, batch qua `jobs.json`.

```
dyflow/
├── setup.sh             # cai dat 1 phat
├── config.yaml          # tham so chung
├── jobs.json            # danh sach video can xu ly
├── requirements.txt
└── dyflow/
    ├── downloader.py    # 1. tai Douyin (yt-dlp; f2 cho ca profile)
    ├── transform.py     # 2. BIEN DOI SAU: cat doan + zoom/pan khac nhau + doi mau/nhip
    ├── broll.py         # 2b. (tuy chon) chen clip rieng vao dau/cuoi
    ├── voiceover.py     # 3. edge-tts tieng Viet
    ├── compose.py       # 4. caption + nhac + export 9:16/1:1/16:9
    ├── upload.py        # 5. stub (doc canh bao ben duoi)
    ├── main.py          # orchestrator + CLI + selftest
    ├── telegram_bot.py  # bot Telegram
    └── web/             # giao dien web (FastAPI + worker + SQLite)
```

## Cài đặt nhanh

```bash
bash setup.sh
source .venv/bin/activate
python -m dyflow.main --selftest -v     # chay thu, KHONG can link/mang
```

`--selftest` tạo clip mẫu rồi chạy transform → caption → export. Mở
`output/selftest_9x16.mp4` xem khung hình + chữ có đúng không. Đây là cách kiểm tra
ffmpeg/filter chạy ổn trên máy bạn trước khi dùng link thật.

**Font caption (quan trọng):** để dấu tiếng Việt đẹp, bỏ 1 font Unicode đậm
(Be Vietnam Pro / Roboto-Bold) vào `assets/fonts/` rồi trỏ `paths.font_path` trong config.

## Chạy thật

1. Sửa `jobs.json`: mỗi job cần `id`, `source` (link Douyin), `script` (lời lồng tiếng tự viết).
2. Chạy:

```bash
python -m dyflow.main --jobs jobs.json -v      # ca batch
python -m dyflow.main --only skincare_001 -v   # 1 job
```

Thành phẩm: `./output/<id>_9x16.mp4` (và 1:1, 16:9 nếu bật trong `export.formats`).

## Giao diện web (chạy local)

Ngoài CLI và bot Telegram, có bản web để dán link → chọn giọng → theo dõi tiến độ → tải video.

```bash
source .venv/bin/activate
pip install -r requirements.txt          # lan dau: cai them fastapi/uvicorn/jinja2
uvicorn dyflow.web.app:app --port 8080
```

Mở `http://localhost:8080`. Worker chạy sẵn trong tiến trình web (1 job/lần để tránh
tranh Chrome profile/cookie/model), nên **chỉ cần 1 lệnh**. Muốn tách worker riêng:

```bash
uvicorn dyflow.web.app:app --port 8080   # terminal 1 (API)
python -m dyflow.web.worker              # terminal 2 (worker riêng)
```

Dữ liệu lưu trong `work/dyflow.db` (SQLite). Video hoàn tất nằm ở `./output/`,
tải qua nút trên trang job. Cấu trúc: [`dyflow/web/`](dyflow/web/) — `app.py` (API),
`worker.py` (worker pool), `models.py` (SQLite), `auth.py`, `billing.py`,
`payments.py`, `locks.py`, `templates/` + `static/` (UI).
Clip dài tự cắt 2 phút đầu như bot Telegram.

### Tài khoản + credit + nạp crypto (thủ công)

Web có đăng nhập, ví credit và **nạp crypto tay** (không dùng cổng bên thứ 3):

- **1 video = 1 credit** (chỉnh trong [`dyflow/web/billing.py`](dyflow/web/billing.py)). Đăng ký tặng 2 credit dùng thử.
- Job **thất bại tự hoàn credit**. Trừ credit ngay khi tạo job.
- Nạp: chọn gói → chuyển USDT TRC20 → dán TX hash → hệ thống **tự check Tronscan**; đúng thì **tự cộng credit**. Sai/chưa confirmed thì chờ admin duyệt tại `/admin/payments` + báo Telegram.

Cấu hình trong `.env`:

```bash
CRYPTO_WALLET_ADDRESS=T...     # dia chi nhan USDT TRC20 cua ban
CRYPTO_NETWORK=TRC20
CRYPTO_ASSET=USDT
ADMIN_EMAIL=ban@email.com      # tai khoan nay duoc duyet nap

# Thong bao Telegram khi user gui TX (khuyen nghi)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=123456789
PUBLIC_BASE_URL=http://localhost:8080
```

Khi user gửi mã giao dịch, bot gửi tin cho admin (email, số tiền, TX, link Tronscan + trang duyệt). Admin mở `/admin/payments` → Duyệt.

Cách lấy `TELEGRAM_ADMIN_CHAT_ID`: chat `/start` với bot → mở  
`https://api.telegram.org/bot<TOKEN>/getUpdates` → tìm `"chat":{"id": ...}`.

Sau khi sửa `.env`, khởi động lại server. Đăng ký/đăng nhập bằng `ADMIN_EMAIL` rồi vào `/admin/payments` để duyệt.

### Chạy đa luồng

Cho nhiều job xử lý song song bằng `web.workers` trong [`config.yaml`](config.yaml) hoặc biến `DYFLOW_WEB_WORKERS`:

```yaml
web:
  workers: 2   # 8vCPU/16GB nen de 2
```

Các khâu nặng/mong manh được **khoá riêng** ([`dyflow/web/locks.py`](dyflow/web/locks.py)) nên nhiều job gối lên nhau an toàn:
- Chrome (profile) — 1 job/lần
- Whisper (RAM) — 1 job/lần
- Demucs (CPU/GPU) — 1 job/lần

Trong khi job A transcribe thì job B có thể đang tải/encode, không tranh cùng tài nguyên.

Muốn tách worker sang tiến trình riêng (khuyến nghị khi chạy nhiều luồng trên VPS):

```bash
DYFLOW_WEB_NO_WORKER=1 uvicorn dyflow.web.app:app --port 8080   # web
python -m dyflow.web.worker                                     # worker
```

## Biến đổi sâu (transform) — phần quan trọng nhất

`transform.py` KHÔNG chỉ crop bề mặt. Nó cắt video thành `segments` đoạn, mỗi đoạn
**zoom/pan + mức màu khác nhau** rồi nối lại — đổi cả perceptual hash lẫn dòng chảy
hình ảnh. Cộng đổi nhịp, lật tuỳ chọn, lọc nhiễu + thay tiếng gốc. Chỉnh trong
`config.yaml` (`segments`, `zoom`, `zoom_step`, `speed_factor`, `contrast`...).

**B-roll (mạnh nhất):** bật `transform.broll.enabled` và trỏ `intro`/`outro` tới
clip riêng của bạn (intro brand, cảnh tự quay). Đây là biến đổi **nội dung** thật,
tín hiệu "original" mạnh hơn nhiều so với chỉnh khung hình. Lưu ý nó làm dài video
nên canh độ dài script cho khớp.

> **Sự thật cần nhớ:** TikTok (2025–2026) phát hiện reused content rất giỏi — deep
> learning + audio/visual fingerprint + C2PA metadata + perceptual hash, ~90% chính
> xác kể cả khi đã crop/lật/đổi nhạc/thêm chữ. Douyin và TikTok cùng ByteDance nên
> đối chiếu càng dễ. Mấy bước trong tool này **giảm** rủi ro kỹ thuật chứ KHÔNG bảo
> đảm né được. Muốn kênh sống bền thì phải transform **nội dung**: ghép nhiều clip
> thành bài có kịch bản riêng, thêm b-roll, kể chuyện khác — không phải copy 1:1 rồi
> thay tiếng. Tool lo phần kỹ thuật; phần sáng tạo vẫn cần bạn.

## script = "AUTO" (transcribe + dịch)

Là stub trong `voiceover.auto_script()`. Bật bằng `faster-whisper` (transcribe tiếng
gốc) + 1 dịch vụ dịch. Mình để trống có chủ đích vì dịch tự động hay sai — viết
script tay cho chất lượng tốt nhất.

## Tải cả profile (hàng trăm video)

`yt-dlp` tải lẻ. Cần kéo cả kênh → dùng `f2` (Johnserf-Seed/f2): `pip install f2`,
cấu hình cookie, gọi `downloader.download_profile_f2()`. Pin version vì Douyin đổi
API/signature liên tục.

## Upload — đọc trước khi bật

`upload.py` để **stub có chủ đích**, mặc định tắt. Lý do: TikTok không có API upload
chính thức cho dạng này; tool auto dùng cookie/API lậu → hỏng liên tục, dễ ban acc.
Auto-post hàng loạt nội dung làm lại từ Douyin lại đúng thứ TikTok đang siết. An toàn
hơn: lấy file trong `./output`, review tay, đăng qua app. Muốn auto thì ghép
`social-auto-upload` (GitHub) vào hook trong `upload.py`.

## Pháp lý / bản quyền

Video Douyin có chủ sở hữu — creator Trung Quốc giữ IP, có thể DMCA → strike kênh.
Tải về tái sử dụng thương mại (affiliate) chạm vào bản quyền + điều khoản nền tảng.
Rủi ro thuộc về bạn. Nội dung có người thật → chú ý cả hình ảnh cá nhân/đồng thuận.
