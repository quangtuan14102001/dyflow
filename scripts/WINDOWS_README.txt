VideosCreator (Windows)
=======================

1. Giai nen zip o thu muc nao cung duoc: Downloads, Desktop, o D:, USB...
2. Double-click VideosCreator.exe. Do la app desktop — khong can file .bat.
3. Lan dau Windows SmartScreen: More info -> Run anyway.
   Doi 1-3 phut lan dau. Neu bao timeout 127.0.0.1: Settings -> Network -> Proxy
   -> tat proxy cho localhost.
   Neu bao thieu WebView2: cai Evergreen Runtime
   https://go.microsoft.com/fwlink/p/?LinkId=2124703
   (Windows 11 thuong da co san).
4. Dan license key (VC1-...) vao o kich hoat. Het han thi dan key moi.
5. Lay danh sach kenh Douyin khong mo cua so Chrome/Edge.
6. Video xuat nam o: %LOCALAPPDATA%\VideosCreator\output
7. OPENAI_API_KEY da kem trong file .env (cung thu muc .exe). Khong can dien.
   Muon doi key: sua .env canh exe, hoac %LOCALAPPDATA%\VideosCreator\.env
