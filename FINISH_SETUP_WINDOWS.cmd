@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON=py"
) else (
    set "PYTHON=python"
)
%PYTHON% -m pip install --disable-pip-version-check -r requirements-github.txt
if errorlevel 1 goto fail
%PYTHON% -m scripts.finish_setup
if errorlevel 1 goto fail
echo.
echo Open the GitHub Actions link above and check the connection test result.
pause
exit /b 0
:fail
echo.
echo Setup needs attention. Read the message above and rerun this file after fixing it.
pause
exit /b 1
