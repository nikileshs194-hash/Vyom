@echo off
rem Starts the GigPilot website on Windows.
rem Usage: double-click this file, or run start_website.bat in a terminal.
rem Close the server window it opens to stop everything.

cd /d "%~dp0website"

echo Installing backend requirements (safe to re-run)...
python -m pip install --quiet fastapi uvicorn httpx
if errorlevel 1 (
  echo Could not install requirements. Is Python 3.10+ installed and on PATH?
  pause
  exit /b 1
)

echo Starting GigPilot on http://localhost:8000 ...
start "GigPilot server" /d "%~dp0website\backend" python -m uvicorn main:app --port 8000

rem the backend builds its order history on startup, give it a moment
timeout /t 8 /nobreak > nul
start "" http://localhost:8000/

echo.
echo GigPilot is running at http://localhost:8000/
echo Close the "GigPilot server" window to stop it.
