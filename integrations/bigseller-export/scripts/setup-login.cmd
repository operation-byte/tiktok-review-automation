@echo off
setlocal
cd /d "%~dp0.."
if not exist "node_modules\playwright" (
  echo Playwright is not installed. Run scripts\install.cmd first.
  pause
  exit /b 60
)
set BIGSELLER_HEADLESS=0
node src\setup-login.js
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" pause
exit /b %CODE%
