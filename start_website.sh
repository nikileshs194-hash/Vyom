#!/bin/bash
# Starts the GigPilot website with one command. The backend serves both
# the API and the site itself.
# Usage:  bash start_website.sh
# Stop it with Ctrl+C.

set -e
cd "$(dirname "$0")/website/backend"

# On Windows "python3" is often a Microsoft Store stub, so fall back to "python".
PY=python3
if ! $PY --version > /dev/null 2>&1; then PY=python; fi

echo "Installing backend requirements (safe to re-run)..."
$PY -m pip install --quiet fastapi uvicorn httpx

echo ""
echo "============================================================"
echo "  Open this in your browser once the server says it is up:"
echo "  http://localhost:8000/"
echo "============================================================"
echo ""
echo "Press Ctrl+C to stop."
echo ""

$PY -m uvicorn main:app --port 8000
