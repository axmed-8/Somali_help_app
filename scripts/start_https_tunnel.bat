@echo off
REM Opens a public HTTPS URL to local GurmadNet (for phone mic/GPS/WebRTC).
REM Requires: GurmadNet already running on PORT (default 5000)
setlocal
cd /d "%~dp0.."

if "%PORT%"=="" set PORT=5000

set CF=
if exist "tools\cloudflared-win.exe" set CF=tools\cloudflared-win.exe
if exist "tools\cloudflared.exe" set CF=tools\cloudflared.exe
where cloudflared >nul 2>nul && set CF=cloudflared

if "%CF%"=="" (
  echo cloudflared not found.
  echo Download: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/
  echo Or place cloudflared-win.exe in tools\
  exit /b 1
)

echo.
echo === GurmadNet HTTPS tunnel ===
echo Forwarding https://... -^> http://127.0.0.1:%PORT%
echo Keep THIS window open while testing on your phone.
echo Copy the https://....trycloudflare.com URL into your phone browser.
echo.

"%CF%" tunnel --url http://127.0.0.1:%PORT%
endlocal
