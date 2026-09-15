@echo off
cd /d "%~dp0"
set NO_PROXY=127.0.0.1,localhost,::1
set no_proxy=127.0.0.1,localhost,::1
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
echo.
echo VideosCreator dang mo. Lan dau doi 1-3 phut, dung tat cua so.
echo Neu SmartScreen: More info -^> Run anyway.
echo.
start "" "%~dp0VideosCreator.exe"
