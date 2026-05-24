@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo ERROR: venv not found. Run: python -m virtualenv venv ^&^& venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

call venv\Scripts\activate.bat
python run_poller.py
exit /b %ERRORLEVEL%
