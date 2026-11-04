@echo off
echo ========================================================
echo   Starting Khojati Inventory Backend Server
echo ========================================================
cd /d "%~dp0"

echo Checking and installing requirements...
pip install -r requirements.txt

echo.
echo Initialising database and starting server on http://0.0.0.0:8000 ...
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
