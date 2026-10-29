# Tao file patch.zip sieu nhe (~1-2MB) de phat hanh ban cap nhat tu dong cho VideosCreator
param(
  [string]$Version = "",
  [string]$Changelog = "Bản cập nhật tính năng mới và sửa lỗi tối ưu hóa."
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Lay version tu dyflow/version.py neu khong truyen tham so
if (-not $Version) {
  $verFile = Join-Path $root "dyflow\version.py"
  if (Test-Path $verFile) {
    $m = Select-String -Path $verFile -Pattern '__version__\s*=\s*["'']([^"'']+)["'']'
    if ($m) { $Version = $m.Matches[0].Groups[1].Value }
  }
}
if (-not $Version) { $Version = "1.0.2" }

Write-Host "Dang dong goi ban va (Patch) cho VideosCreator v$Version..."

$dist = Join-Path $root "dist"
if (-not (Test-Path $dist)) { New-Item -ItemType Directory -Path $dist | Out-Null }
$patchZip = Join-Path $dist "patch.zip"
$versionJson = Join-Path $dist "version.json"

if (Test-Path $patchZip) { Remove-Item $patchZip -Force }

# Tao file version.json kem theo
$verData = [ordered]@{
  version = $Version
  release_date = (Get-Date -Format "yyyy-MM-dd HH:mm")
  changelog = $Changelog
  patch_url = "https://raw.githubusercontent.com/quangtuan14102001/dyflow/main/dist/patch.zip"
  installer_url = "https://raw.githubusercontent.com/quangtuan14102001/dyflow/main/dist/VideosCreator_Setup.exe"
}
$verJsonContent = $verData | ConvertTo-Json -Depth 4
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($versionJson, $verJsonContent, $utf8NoBom)
$rootVersionJson = Join-Path $root "version.json"
[System.IO.File]::WriteAllText($rootVersionJson, $verJsonContent, $utf8NoBom)

# Tao thu muc tam de nén chi gom ma nguon sach (loai bo __pycache__, .pyc)
$tmpPatch = Join-Path $dist "_tmp_patch"
if (Test-Path $tmpPatch) { Remove-Item $tmpPatch -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $tmpPatch "dyflow") -Force | Out-Null

# Copy dyflow
Copy-Item (Join-Path $root "dyflow\*") (Join-Path $tmpPatch "dyflow") -Recurse -Force

# Copy profiles (mau prompt van phong cac the loai phim)
if (Test-Path (Join-Path $root "profiles")) {
  Copy-Item (Join-Path $root "profiles") (Join-Path $tmpPatch "profiles") -Recurse -Force
}

# Copy version.json vao trong ban va
Copy-Item $versionJson (Join-Path $tmpPatch "version.json") -Force

# Copy desktop.py vao trong ban va
if (Test-Path (Join-Path $root "desktop.py")) {
  Copy-Item (Join-Path $root "desktop.py") (Join-Path $tmpPatch "desktop.py") -Force
}

# Don dep __pycache__ va file rac
Get-ChildItem -Path $tmpPatch -Include "__pycache__", "*.pyc", "*.pyo" -Recurse -Force | Remove-Item -Recurse -Force

# Nen patch.zip
tar.exe -a -cf $patchZip -C $tmpPatch dyflow profiles version.json desktop.py
if ($LASTEXITCODE -ne 0) {
  # Fallback neu tar khong kha dung
  Compress-Archive -Path (Join-Path $tmpPatch "*") -DestinationPath $patchZip -Force
}

# Xoa thu muc tam
Remove-Item $tmpPatch -Recurse -Force

$zipItem = Get-Item $patchZip
$sizeKb = [math]::Round($zipItem.Length / 1024, 1)

Write-Host "========================================="
Write-Host "THANH CONG: Da tao ban va patch.zip!"
Write-Host "File: $patchZip ($sizeKb KB)"
Write-Host "Version Info: $versionJson"
Write-Host "========================================="
