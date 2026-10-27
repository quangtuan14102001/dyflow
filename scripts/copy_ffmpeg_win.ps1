# Copy ffmpeg/ffprobe THAT. Chocolatey shim (~400KB) chi redirect — khong dung.
param(
  [Parameter(Mandatory = $true)]
  [string]$DestDir
)

$ErrorActionPreference = "Stop"
$MinBytes = 5MB

function Test-RealFfmpegDir([string]$dir) {
  if (-not $dir) { return $false }
  if ($dir -match '\\chocolatey\\bin$') { return $false }
  $ff = Join-Path $dir "ffmpeg.exe"
  $probe = Join-Path $dir "ffprobe.exe"
  if (-not (Test-Path $ff) -or -not (Test-Path $probe)) { return $false }
  return ((Get-Item $ff).Length -gt $MinBytes) -and ((Get-Item $probe).Length -gt $MinBytes)
}

function Find-FfmpegDir {
  $explicit = @(
    "$env:ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin",
    "$env:ProgramData\chocolatey\lib\ffmpeg\tools\bin",
    "$env:ProgramData\chocolatey\lib\ffmpeg-full\tools\ffmpeg\bin"
  )
  foreach ($dir in $explicit) {
    if (Test-RealFfmpegDir $dir) { return $dir }
  }
  $roots = @(
    "$env:ProgramData\chocolatey\lib",
    "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
  )
  foreach ($root in $roots) {
    if (-not (Test-Path $root)) { continue }
    $hit = Get-ChildItem $root -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
      Where-Object { $_.Length -gt $MinBytes -and $_.DirectoryName -notmatch '\\chocolatey\\bin$' } |
      Select-Object -First 1
    if ($hit -and (Test-RealFfmpegDir $hit.DirectoryName)) {
      return $hit.DirectoryName
    }
  }
  $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
  if ($cmd -and (Test-RealFfmpegDir (Split-Path -Parent $cmd.Source))) {
    return (Split-Path -Parent $cmd.Source)
  }
  throw "Khong tim thay ffmpeg.exe that (>5MB) kem ffprobe."
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
if ($ffOut.Length -lt $MinBytes) {
  throw ("ffmpeg.exe copy too small ({0} bytes)" -f $ffOut.Length)
}
Write-Host ("Copied ffmpeg {0} bytes + ffprobe {1} bytes from {2}" -f $ffOut.Length, $fpOut.Length, $src)
