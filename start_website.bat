@echo off
rem Starts BOTH the backend and frontend of the GigPilot website on Windows.
rem Usage: double-click this file, or run start_website.bat in a terminal.
rem Close the two server windows it opens to stop everything.

cd /d "%~dp0website"

echo Installing backend requirements (safe to re-run)...
python -m pip install --quiet fastapi uvicorn
if errorlevel 1 (
  echo Could not install requirements. Is Python 3.10+ installed and on PATH?
  pause
  exit /b 1
)

echo Starting backend on http://localhost:8000 ...
start "GigPilot backend" /d "%~dp0website\backend" python -m uvicorn main:app --port 8000

echo Starting frontend on http://localhost:5500 ...
start "GigPilot frontend" /d "%~dp0website\frontend" python -m http.server 5500

timeout /t 3 /nobreak > nul
start "" http://localhost:5500/index.html

echo.
echo GigPilot is running at http://localhost:5500/index.html
echo Close the "GigPilot backend" and "GigPilot frontend" windows to stop it.
