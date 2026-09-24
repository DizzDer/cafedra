@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 app.py
    pause
    exit /b
)
python --version >nul 2>&1
if not errorlevel 1 (
    python app.py
    pause
    exit /b
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
    pause
    exit /b
)
echo Python не найден. Установите Python 3.10 или новее и повторите запуск.
pause
