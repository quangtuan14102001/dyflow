@echo off
cd /d "%~dp0"
set NO_PROXY=127.0.0.1,localhost,::1
set no_proxy=127.0.0.1,localhost,::1
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=

echo Dang mo khoa file Windows (Unblock) neu tai tu internet...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File" >nul 2>&1

echo.
echo VideosCreator dang mo tren trinh duyet. Lan dau doi 1-3 phut.
echo Neu SmartScreen: More info -^> Run anyway.
echo Giai nen o thu muc nao cung duoc (Downloads, D:, USB...).
echo.
start "" "%~dp0VideosCreator.exe"
