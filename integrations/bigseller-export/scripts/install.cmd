@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo  WELIVE BigSeller Automation - Installation
echo ============================================================
where node >nul 2>nul
if errorlevel 1 (
  echo ERROR: Node.js 20 or newer is not installed.
  pause
  exit /b 1
)

node -e "const major=Number(process.versions.node.split('.')[0]); if(major<20) process.exit(1)"
if errorlevel 1 (
  echo ERROR: Node.js 20 or newer is required.
  pause
  exit /b 1
)

call npm ci --ignore-scripts
if errorlevel 1 goto :failed
call npx playwright install chromium
if errorlevel 1 goto :failed

echo.
echo Installation completed successfully.
echo Next, run scripts\setup-login.cmd and log in to BigSeller once.
pause
exit /b 0

:failed
echo.
echo Installation failed. Check the messages above and your internet connection.
pause
exit /b 1
