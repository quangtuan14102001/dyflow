VideosCreator (Windows)
=======================

0. QUAN TRONG — SmartScreen "Don't run" / "Windows protected your PC":
   Day la app chua ky Authenticode (Microsoft yeu cau tra phi chung chi).
   KHONG bam "Don't run".

   Truoc khi giai nen:
   - Right-click file ZIP -> Properties -> tick Unblock -> Apply -> OK.
   - Roi moi Extract All.

   Neu van hien SmartScreen khi mo .exe:
   - Click "More info" (Thong tin them)
   - Click "Run anyway" (Van chay)
   Windows 11: man hinh xanh chi co nut Don't run — phai bam More info
   o goc trai moi hien Run anyway.

1. Giai nen zip o thu muc nao cung duoc: Downloads, Desktop, o D:, USB...
   Xoa folder giai nen cu truoc — dung de de file moi len folder cu.
2. Double-click VideosCreator.exe (hoặc file Chay-VideosCreator.bat).
   - Nếu muốn dùng trực tiếp trên trình duyệt Web (Chrome/Edge): Double-click Chay-Trinh-Duyet.bat.
   - Nếu bấm mà không hiện cửa sổ:
     + Hãy chạy file Kiem-Tra-Loi.bat để kiểm tra thông báo lỗi chi tiết.
     + Hoặc mở file crash.log trong thư mục để gửi cho bên hỗ trợ.
     + Kiểm tra Task Manager (Ctrl+Shift+Esc) → End task các tiến trình VideosCreator cũ nếu có.
3. Nếu máy chưa cài WebView2 Runtime, app sẽ tự động mở trên Trình Duyệt Web để bạn sử dụng ngay.
   Để dùng dạng cửa sổ phần mềm, bạn có thể tải WebView2 miễn phí:
   https://go.microsoft.com/fwlink/p/?LinkId=2124703
   (Windows 11 thường đã có sẵn).
4. Dan license key (VC1-...) vao o kich hoat. Het han thi dan key moi.
5. Link /user/SEC (±?vid=) = kênh. Link /user/...?modal_id= = 1 video dang mo.
6. Video xuat nam o: %LOCALAPPDATA%\VideosCreator\output (hoac thu muc Save tren app).
7. App mac dinh dung Edge-TTS (mien phi, khong can API key) va DeepSeek/OpenAI cho dich thuat. Co the dan key truc tiep trong phan Cai dat tren giao dien app.

