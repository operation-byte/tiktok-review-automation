@echo off
setlocal
cd /d "%~dp0.."
if not exist "node_modules\playwright" (
  echo Playwright is not installed. Run scripts\install.cmd first.
  exit /b 60
)
node src\run.js
exit /b %ERRORLEVEL%
