#!/bin/bash
# Starts the BACKUP single-file version (only use this if the website
# version in ../website has a problem you can't fix in time).
# Usage:  bash start_backup.sh

set -e
cd "$(dirname "$0")"

# On Windows "python3" is often a Microsoft Store stub, so fall back to "python".
PY=python3
if ! $PY --version > /dev/null 2>&1; then PY=python; fi

echo "Installing requirements (safe to re-run)..."
$PY -m pip install --quiet "streamlit>=1.50" pandas

echo "Starting GigPilot (backup version)..."
$PY -m streamlit run app.py
