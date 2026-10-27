$ErrorActionPreference = "Stop"
# Dong goi ban user — KHONG bundle OPENAI_API_KEY / .env (user tu dan key trong app).
$root = Split-Path -Parent $PSScriptRoot
$distApp = Join-Path $root "dist\VideosCreator"
$zip = Join-Path $root "dist\VideosCreator.zip"

if (-not (Test-Path (Join-Path $distApp "VideosCreator.exe"))) {
  throw "Missing dist\VideosCreator\VideosCreator.exe - run scripts\build_windows.ps1 first"
}

# Dam bao khong ship secret
foreach ($p in @(
  (Join-Path $distApp ".env"),
  (Join-Path $distApp "_internal\.env")
)) {
  if (Test-Path $p) {
    Remove-Item $p -Force
    Write-Host "Removed $p (do not ship secrets)"
  }
}

if (Test-Path $zip) { Remove-Item $zip -Force }
tar.exe -a -cf $zip -C (Join-Path $root "dist") VideosCreator
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

$leaked = tar.exe -tf $zip | Select-String -Pattern '(^|/)\.env$'
if ($leaked) {
  throw "Zip still contains .env - do not ship secrets"
}
Write-Host "zip_no_env_ok"
Get-Content (Join-Path $distApp "build.txt") -ErrorAction SilentlyContinue
Get-Item $zip | Format-List FullName, Length, LastWriteTime
Write-Host "DONE"
