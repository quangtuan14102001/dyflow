$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$distApp = Join-Path $root "dist\VideosCreator"
$iss = Join-Path $root "packaging\installer.iss"

if (-not (Test-Path (Join-Path $distApp "VideosCreator.exe"))) {
  throw "Missing dist\VideosCreator\VideosCreator.exe - run scripts\build_windows.ps1 first"
}

# Clean secrets before packaging
foreach ($p in @(
  (Join-Path $distApp ".env"),
  (Join-Path $distApp "_internal\.env")
)) {
  if (Test-Path $p) {
    Remove-Item $p -Force
    Write-Host "Removed $p (do not ship secrets)"
  }
}

# Find Inno Setup Compiler
$isccPaths = @(
  "C:\Users\Admin\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$iscc = $null
foreach ($path in $isccPaths) {
  if (Test-Path $path) {
    $iscc = $path
    break
  }
}

if (-not $iscc) {
  $cmd = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
  if ($cmd) { $iscc = $cmd.Source }
}

if (-not $iscc) {
  throw "Cannot find Inno Setup compiler (ISCC.exe). Please install Inno Setup 6."
}

Write-Host "Compiling VideosCreator_Setup.exe using $iscc..."
& $iscc $iss
if ($LASTEXITCODE -ne 0) { throw "ISCC compilation failed" }

$setupExe = Join-Path $root "dist\VideosCreator_Setup.exe"
if (Test-Path $setupExe) {
  Write-Host "SUCCESS: VideosCreator_Setup.exe created!"
  Get-Item $setupExe | Format-List FullName, Length, LastWriteTime
} else {
  throw "Output file $setupExe was not found after compilation."
}
