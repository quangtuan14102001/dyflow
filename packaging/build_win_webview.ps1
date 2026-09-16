# Build VideosCreatorWebView.dll (WebView2 host). Run on Windows with MSVC.
$ErrorActionPreference = "Stop"

function Import-VcVars64 {
    if (Get-Command cl.exe -ErrorAction SilentlyContinue) { return }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { throw "Khong tim thay vswhere / MSVC." }
    $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vs) { throw "Khong tim thay Visual Studio C++." }
    $vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
    cmd.exe /c "`"$vcvars`" && set" | ForEach-Object {
        if ($_ -match '^(.*?)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2])
        }
    }
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        throw "cl.exe van khong co trong PATH sau vcvars64."
    }
}

Import-VcVars64
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Src = Join-Path $Root "packaging\win_webview_host.cpp"
$OutDir = Join-Path $Root "dist\native"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$Ver = "1.0.3537.50"
$Tmp = Join-Path $env:TEMP "vc-webview2-$Ver"
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
$Zip = Join-Path $Tmp "sdk.zip"
$Extract = Join-Path $Tmp "sdk"
if (-not (Test-Path (Join-Path $Extract "build\native\include\WebView2.h"))) {
    Write-Host "Downloading Microsoft.Web.WebView2 $Ver"
    Invoke-WebRequest -UseBasicParsing -Uri "https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/$Ver" -OutFile $Zip
    if (Test-Path $Extract) { Remove-Item $Extract -Recurse -Force }
    Expand-Archive -LiteralPath $Zip -DestinationPath $Extract -Force
}

$Include = Join-Path $Extract "build\native\include"
$Lib = Join-Path $Extract "build\native\x64\WebView2LoaderStatic.lib"
if (-not (Test-Path $Lib)) {
    throw "Missing $Lib"
}

$Dll = Join-Path $OutDir "VideosCreatorWebView.dll"
$Obj = Join-Path $OutDir "win_webview_host.obj"
Write-Host "cl -> $Dll"
& cl.exe /nologo /O2 /LD /EHsc /std:c++17 /DUNICODE /D_UNICODE /W3 `
    /I $Include `
    $Src `
    /Fo$Obj `
    /Fe$Dll `
    /link /DLL /INCREMENTAL:NO $Lib `
    ole32.lib oleaut32.lib user32.lib shell32.lib shlwapi.lib version.lib advapi32.lib
if ($LASTEXITCODE -ne 0) {
    throw "cl failed: $LASTEXITCODE"
}
Get-Item $Dll
