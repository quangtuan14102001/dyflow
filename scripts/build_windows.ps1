# Build VideosCreator.exe — chay TREN WINDOWS (khong dung duoc tu Mac).
param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

& $Python -m pip install -r requirements.txt -r requirements-desktop.txt
& $Python -m PyInstaller --noconfirm --clean VideosCreator.spec

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
  Copy-Item $ffmpeg.Source "dist\VideosCreator\ffmpeg.exe" -Force
  Write-Host "Copied ffmpeg.exe"
} else {
  Write-Host "WARN: ffmpeg not on PATH. Copy ffmpeg.exe next to VideosCreator.exe"
}

$readme = Join-Path $PSScriptRoot "WINDOWS_README.txt"
if (Test-Path $readme) {
  Copy-Item $readme "dist\VideosCreator\README.txt" -Force
}

Write-Host "Output: dist\VideosCreator\VideosCreator.exe"
Write-Host "Zip CA thu muc dist\VideosCreator roi gui khach (khong chi 1 file .exe)."
