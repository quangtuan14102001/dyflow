# Build VideosCreator.exe — chay TREN WINDOWS (khong dung duoc tu Mac).
param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

& $Python -m pip install -r requirements.txt -r requirements-desktop.txt
& $Python -m PyInstaller --noconfirm --clean VideosCreator.spec

$ffmpegScript = Join-Path $PSScriptRoot "copy_ffmpeg_win.ps1"
if (Test-Path $ffmpegScript) {
  & $ffmpegScript -DestDir "dist\VideosCreator"
  if (Test-Path "dist\VideosCreator\_internal") {
    & $ffmpegScript -DestDir "dist\VideosCreator\_internal"
  }
} else {
  Write-Host "WARN: thieu copy_ffmpeg_win.ps1"
}

$readme = Join-Path $PSScriptRoot "WINDOWS_README.txt"
if (Test-Path $readme) {
  Copy-Item $readme "dist\VideosCreator\README.txt" -Force
}

Write-Host "Output: dist\VideosCreator\VideosCreator.exe"
Write-Host "Zip CA thu muc dist\VideosCreator roi gui khach (khong chi 1 file .exe)."
