@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

start "Flow88 FastAPI" /min cmd /c "python -m uvicorn server:app --host 127.0.0.1 --port 8000"
timeout /t 2 /nobreak >nul
start "" "http://localhost:8000"

echo Flow88 Mix Engine started at http://localhost:8000
