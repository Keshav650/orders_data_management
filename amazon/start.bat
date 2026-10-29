@echo off
setlocal
cd /d "%~dp0"
echo Starting Amazon Ledger on port 8775...
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0launch.py" %*
    goto finished
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0launch.py" %*
    goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0launch.py" %*
    goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0launch.py" %*
    goto finished
)
echo Python was not found. Install Python 3.11 or newer, then run start.bat.
pause
exit /b 1
:finished
if errorlevel 1 (
    echo.
    echo Startup failed. Please check data\server-error.log.
    pause
    exit /b 1
)
exit /b 0
