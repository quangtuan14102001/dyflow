# Build VideosCreator.exe - run on Windows.
param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

& $Python -m pip install -r requirements.txt -r requirements-desktop.txt

# Required: WebView2 host DLL next to the exe
$webviewScript = Join-Path $PSScriptRoot "..\packaging\build_win_webview.ps1"
$dllOut = Join-Path $PSScriptRoot "..\dist\native\VideosCreatorWebView.dll"
if (-not (Test-Path $dllOut) -and (Test-Path $webviewScript)) {
  try {
    & $webviewScript
  } catch {
    Write-Host "WARN: WebView DLL build failed; will try fallback copy."
  }
}

if (-not (Test-Path $dllOut)) {
  $fallback = Get-ChildItem -Path "dist" -Recurse -Filter "VideosCreatorWebView.dll" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($fallback) {
    New-Item -ItemType Directory -Force -Path "dist\native" | Out-Null
    Copy-Item $fallback.FullName $dllOut -Force
    Write-Host "Copied WebView DLL fallback from $($fallback.FullName)"
  }
}

if (-not (Test-Path $dllOut)) {
  throw "Missing VideosCreatorWebView.dll - cannot package. Run packaging\build_win_webview.ps1 first."
}

& $Python -m PyInstaller --noconfirm --clean VideosCreator.spec

$ffmpegScript = Join-Path $PSScriptRoot "copy_ffmpeg_win.ps1"
if (Test-Path $ffmpegScript) {
  if (Test-Path "dist\VideosCreator\_internal") {
    & $ffmpegScript -DestDir "dist\VideosCreator\_internal"
  } else {
    & $ffmpegScript -DestDir "dist\VideosCreator"
  }
} else {
  Write-Host "WARN: missing copy_ffmpeg_win.ps1"
}

Copy-Item $dllOut "dist\VideosCreator\_internal\VideosCreatorWebView.dll" -Force -ErrorAction SilentlyContinue
Copy-Item $dllOut "dist\VideosCreator\VideosCreatorWebView.dll" -Force

# Copy full Microsoft Visual C++ runtime DLLs (vcruntime140_threads.dll, vcomp140.dll, etc.)
# de may client khong cai san VC++ Redistributable 2022 van chay duoc 100% PyTorch va khong bao loi WinError 126
$vcDlls = @(
  "vcruntime140.dll",
  "vcruntime140_1.dll",
  "vcruntime140_threads.dll",
  "msvcp140.dll",
  "msvcp140_1.dll",
  "msvcp140_2.dll",
  "msvcp140_atomic_wait.dll",
  "msvcp140_codecvt_ids.dll",
  "vcomp140.dll",
  "concrt140.dll",
  "vcamp140.dll",
  "ucrtbase.dll"
)
foreach ($dll in $vcDlls) {
  $src = Join-Path $env:WINDIR "System32\$dll"
  if (Test-Path $src) {
    Copy-Item $src "dist\VideosCreator\_internal\$dll" -Force -ErrorAction SilentlyContinue
    Copy-Item $src "dist\VideosCreator\$dll" -Force -ErrorAction SilentlyContinue
    if (Test-Path "dist\VideosCreator\_internal\torch\lib") {
      Copy-Item $src "dist\VideosCreator\_internal\torch\lib\$dll" -Force -ErrorAction SilentlyContinue
    }
  }
}

# Sync templates va static vao _internal
if (Test-Path "dist\VideosCreator\_internal\dyflow\web") {
  Copy-Item "dyflow\web\templates" "dist\VideosCreator\_internal\dyflow\web\templates" -Recurse -Force
  Copy-Item "dyflow\web\static" "dist\VideosCreator\_internal\dyflow\web\static" -Recurse -Force
}

$readme = Join-Path $PSScriptRoot "WINDOWS_README.txt"
if (Test-Path $readme) {
  Copy-Item $readme "dist\VideosCreator\_internal\README.txt" -Force
}

$vcRedist = Join-Path $PSScriptRoot "..\packaging\VC_redist.x64.exe"
if (Test-Path $vcRedist) {
  Copy-Item $vcRedist "dist\VideosCreator\Cai-Dat-Visual-C++-Neu-Loi.exe" -Force
}

$guideTxt = @"
=============================================================
             HƯỚNG DẪN KHỞI ĐỘNG VIDEOSCREATOR
=============================================================

1. BẮT BUỘC GIẢI NÉN TRƯỚC KHI DÙNG:
   - Chuột phải vào file 'VideosCreator.zip'
   - Chọn 'Extract All...' (Giải nén tất cả) rồi bấm Extract.
   - TUYỆT ĐỐI KHÔNG mở trực tiếp bên trong WinRAR hoặc 7-Zip.

2. CÁCH MỞ ỨNG DỤNG:
   - Nhấp đúp vào: VideosCreator.exe
   - Hoặc nhấp đúp vào: Chay-VideosCreator.bat (sẽ tự động mở khóa file nếu Windows chặn).

3. LƯU Ý VỀ GIAO DIỆN:
   - Ứng dụng sẽ mở ở dạng cửa sổ phần mềm độc lập.
   - Nếu máy tính của bạn chưa có Microsoft Edge WebView2 hoặc bị xung đột,
     ứng dụng sẽ TỰ ĐỘNG mở trên trình duyệt web mặc định để bạn sử dụng bình thường.

4. THƯ VIỆN MICROSOFT VISUAL C++:
   - Toàn bộ các file thư viện Microsoft Visual C++ 2015-2022 (vcruntime140, msvcp140, vcomp140...)
     đã được đóng gói trực tiếp 100% bên trong thư mục phần mềm.
   - Bạn có thể sử dụng ngay mà không cần cài đặt thêm bất kỳ phần mềm phụ nào!
=============================================================
"@
Set-Content -Path "dist\VideosCreator\Huong-Dan-Su-Dung.txt" -Value $guideTxt -Encoding utf8

$launcherBat = @"
@echo off
chcp 65001 >nul
title Khoi dong VideosCreator...
echo ======================================================
echo    Đang khởi động VideosCreator...
echo    Vui lòng đợi vài giây để cửa sổ ứng dụng mở lên.
echo ======================================================
powershell -NoProfile -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File -ErrorAction SilentlyContinue" 2>nul
start "" "%~dp0VideosCreator.exe"
timeout /t 3 >nul
exit
"@
Set-Content -Path "dist\VideosCreator\Chay-VideosCreator.bat" -Value $launcherBat -Encoding utf8

# Xoa cac file rac/trung lap o thu muc goc de nguoi dung khong bi roi mat
Remove-Item "dist\VideosCreator\dyflow" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "dist\VideosCreator\ffmpeg.exe" -Force -ErrorAction SilentlyContinue
Remove-Item "dist\VideosCreator\ffprobe.exe" -Force -ErrorAction SilentlyContinue
Remove-Item "dist\VideosCreator\Chay-Trinh-Duyet.bat" -Force -ErrorAction SilentlyContinue
Remove-Item "dist\VideosCreator\Kiem-Tra-Loi.bat" -Force -ErrorAction SilentlyContinue
Remove-Item "dist\VideosCreator\README.txt" -Force -ErrorAction SilentlyContinue
if (-not (Test-Path "dist\VideosCreator\VideosCreatorWebView.dll") -and -not (Test-Path "dist\VideosCreator\_internal\VideosCreatorWebView.dll")) {
  throw "Build finished but VideosCreatorWebView.dll is missing"
}
if (-not (Test-Path "dist\VideosCreator\VideosCreator.exe")) {
  throw "Build finished but VideosCreator.exe is missing"
}

# NEVER bundle OPENAI_API_KEY into the zip (users can extract .env).
# Edge TTS + Google translate work without a shared owner key.
# Users who want OpenAI TTS/LLM paste their own key in the app.
if (Test-Path "dist\VideosCreator\.env") {
  Remove-Item "dist\VideosCreator\.env" -Force
  Write-Host "Removed dist\VideosCreator\.env (do not ship secrets)"
}
if (Test-Path "dist\VideosCreator\_internal\.env") {
  Remove-Item "dist\VideosCreator\_internal\.env" -Force
  Write-Host "Removed dist\VideosCreator\_internal\.env (do not ship secrets)"
}

$tag = Get-Date -Format "yyMMdd-HHmm"
$buildTxt = "dist\VideosCreator\build.txt"
Set-Content -Path $buildTxt -Value $tag -Encoding utf8
Write-Host "build.txt = $tag"

Write-Host "Output: dist\VideosCreator\VideosCreator.exe"
Write-Host "DLL OK: dist\VideosCreator\VideosCreatorWebView.dll"
Write-Host "Zip the whole dist\VideosCreator folder for users."
