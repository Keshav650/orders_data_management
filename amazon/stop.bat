@echo off
setlocal
cd /d "%~dp0"
echo Stopping Amazon Ledger...
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0launch.py" --stop
    goto done
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0launch.py" --stop
    goto done
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0launch.py" --stop
    goto done
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0launch.py" --stop
    goto done
)
:done
echo Amazon Ledger stopped.
exit /b 0
