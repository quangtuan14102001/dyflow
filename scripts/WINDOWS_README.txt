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
3. Lan dau doi 1-3 phut. Neu bao timeout 127.0.0.1: Settings -> Network -> Proxy
   -> tat proxy cho localhost.
   Neu bao thieu WebView2: cai Evergreen Runtime
   https://go.microsoft.com/fwlink/p/?LinkId=2124703
   (Windows 11 thuong da co san).
4. Dan license key (VC1-...) vao o kich hoat. Het han thi dan key moi.
5. Lay danh sach kenh: khong mo them cua so VideosCreator.
   Co the mo 1 cua so Edge/Chrome ngan roi tu dong dong.
6. Video xuat nam o: %LOCALAPPDATA%\VideosCreator\output
7. OPENAI_API_KEY da kem trong file .env (cung thu muc .exe). Khong can dien.
   Muon doi key: sua .env canh exe, hoac %LOCALAPPDATA%\VideosCreator\.env
