@echo off
REM GurmadNet mobile testing helper (LAN + optional HTTPS tunnel)
setlocal
cd /d "%~dp0.."

echo.
echo === GurmadNet mobile test launcher ===
echo.

REM Prefer LAN bind for phone access
if "%HOST%"=="" set HOST=0.0.0.0
if "%PORT%"=="" set PORT=5000
set FLASK_DEBUG=

echo Host=%HOST% Port=%PORT%
echo.
echo Starting Flask/SocketIO...
echo Keep this window open. Press CTRL+C to stop.
echo.

python app.py
endlocal
