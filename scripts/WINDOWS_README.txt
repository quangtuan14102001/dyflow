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
2. Double-click VideosCreator.exe. Do la app desktop — khong can file .bat.
   Neu bam .exe ma khong hien cua so: Task Manager (Ctrl+Shift+Esc) →
   End task tat ca VideosCreator.exe → mo lai. (Instance cu co the treo ngam.)
3. Lan dau doi 1-3 phut. Neu bao timeout 127.0.0.1: Settings -> Network -> Proxy
   -> tat proxy cho localhost.
   Neu bao thieu WebView2: cai Evergreen Runtime
   https://go.microsoft.com/fwlink/p/?LinkId=2124703
   (Windows 11 thuong da co san).
4. Dan license key (VC1-...) vao o kich hoat. Het han thi dan key moi.
5. Lay danh sach kenh: KHONG mo cua so Chrome/Edge. Dung Edge/Chrome headless
   trong nen. Neu Preview chi ra 1 clip (vid=): bam Preview lai; dam bao dang
   nhap Douyin tren Edge neu kenh yeu cau login.
6. Video xuat nam o: %LOCALAPPDATA%\VideosCreator\output
7. OPENAI_API_KEY da kem trong file .env (cung thu muc .exe). Khong can dien.
   Muon doi key: sua .env canh exe, hoac %LOCALAPPDATA%\VideosCreator\.env
