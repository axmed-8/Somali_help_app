@echo off
REM Temporary PUBLIC HTTPS tunnel via localhost.run (SSH)
REM Requires GurmadNet already running on 127.0.0.1:5000
setlocal
cd /d "%~dp0.."

if not exist "%USERPROFILE%\.ssh\id_ed25519" (
  echo Creating SSH key for localhost.run...
  ssh-keygen -t ed25519 -N "" -f "%USERPROFILE%\.ssh\id_ed25519"
)

echo.
echo === GurmadNet PUBLIC HTTPS tunnel ===
echo Keep this window open while testers use the link.
echo Copy the https://....lhr.life URL from the output below.
echo.

ssh -i "%USERPROFILE%\.ssh\id_ed25519" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -R 80:127.0.0.1:5000 ssh.localhost.run
endlocal
