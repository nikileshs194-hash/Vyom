#!/bin/bash
# Starts BOTH the backend and frontend of the GigPilot website with one command.
# Usage:  bash start_website.sh
# Stop everything with Ctrl+C.

set -e
cd "$(dirname "$0")/website"

# On Windows "python3" is often a Microsoft Store stub, so fall back to "python".
PY=python3
if ! $PY --version > /dev/null 2>&1; then PY=python; fi

echo "Installing backend requirements (safe to re-run)..."
$PY -m pip install --quiet fastapi uvicorn

echo "Starting backend on http://localhost:8000 ..."
cd backend
$PY -m uvicorn main:app --port 8000 > ../backend.log 2>&1 &
BACKEND_PID=$!
cd ..

# Make sure the backend process is killed when this script exits.
trap "echo 'Stopping backend...'; kill $BACKEND_PID 2>/dev/null" EXIT

sleep 2

echo "Starting frontend on http://localhost:5500 ..."
echo ""
echo "============================================================"
echo "  Open this in your browser now:"
echo "  http://localhost:5500/index.html"
echo "============================================================"
echo ""
echo "Press Ctrl+C to stop both servers."
echo ""

cd frontend
$PY -m http.server 5500
