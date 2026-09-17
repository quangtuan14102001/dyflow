# Copy ffmpeg/ffprobe THAT (khong copy Chocolatey shim ~8KB).
# Dung tu GitHub Actions va scripts/build_windows.ps1.
param(
  [Parameter(Mandatory = $true)]
  [string]$DestDir
)

$ErrorActionPreference = "Stop"

function Find-FfmpegDir {
  $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
  if ($cmd) {
    $dir = Split-Path -Parent $cmd.Source
    $ff = Join-Path $dir "ffmpeg.exe"
    $probe = Join-Path $dir "ffprobe.exe"
    if ((Test-Path $ff) -and (Get-Item $ff).Length -gt 100000 -and (Test-Path $probe)) {
      return $dir
    }
  }
  $roots = @(
    "$env:ProgramData\chocolatey\lib\ffmpeg",
    "$env:ProgramData\chocolatey\lib\ffmpeg-full",
    "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
  )
  foreach ($root in $roots) {
    if (-not (Test-Path $root)) { continue }
    $hit = Get-ChildItem $root -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
      Where-Object { $_.Length -gt 100000 } |
      Select-Object -First 1
    if ($hit) {
      $probe = Join-Path $hit.DirectoryName "ffprobe.exe"
      if (Test-Path $probe) { return $hit.DirectoryName }
    }
  }
  throw "Khong tim thay ffmpeg.exe that (>100KB) kem ffprobe.exe"
}

$src = Find-FfmpegDir
New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
Copy-Item (Join-Path $src "ffmpeg.exe") (Join-Path $DestDir "ffmpeg.exe") -Force
Copy-Item (Join-Path $src "ffprobe.exe") (Join-Path $DestDir "ffprobe.exe") -Force
Get-ChildItem $src -Filter *.dll -ErrorAction SilentlyContinue | ForEach-Object {
  Copy-Item $_.FullName (Join-Path $DestDir $_.Name) -Force
}
$ffOut = Get-Item (Join-Path $DestDir "ffmpeg.exe")
$fpOut = Get-Item (Join-Path $DestDir "ffprobe.exe")
if ($ffOut.Length -lt 100000) { throw "ffmpeg.exe copy van la shim ($($ffOut.Length) bytes)" }
Write-Host "Copied ffmpeg $($ffOut.Length) bytes + ffprobe $($fpOut.Length) bytes from $src"
