@echo off
rem Starts the BACKUP single-file Streamlit version on Windows.
rem Usage: double-click this file, or run start_backup.bat in a terminal.

cd /d "%~dp0"

echo Installing requirements (safe to re-run)...
python -m pip install --quiet "streamlit>=1.50" pandas
if errorlevel 1 (
  echo Could not install requirements. Is Python 3.10+ installed and on PATH?
  pause
  exit /b 1
)

echo Starting GigPilot (backup version)...
python -m streamlit run app.py
