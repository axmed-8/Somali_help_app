@echo off
REM Alternative HTTPS tunnel via localtunnel (npm).
REM Requires GurmadNet running on PORT (default 5000).
setlocal
cd /d "%~dp0.."
if "%PORT%"=="" set PORT=5000
echo.
echo === GurmadNet HTTPS tunnel (localtunnel) ===
echo Forwarding https://... -^> http://127.0.0.1:%PORT%
echo Keep this window open. Copy the https URL to your phone.
echo.
npx --yes localtunnel --port %PORT%
endlocal
