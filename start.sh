#!/usr/bin/env bash
# Bootstrap and launch the Somewheria web app from a fresh checkout.
#
# What it does:
#   1. Creates (or reuses) a local virtualenv in ./venv
#   2. Installs requirements.txt into it
#   3. Warns if .env is missing (the app reads it via python-dotenv)
#   4. Launches the server non-interactively
#
# Low ports (<1024) are bound without root via authbind, when available:
#   sudo apt install authbind
#   sudo touch /etc/authbind/byport/80
#   sudo chmod 500 /etc/authbind/byport/80
#   sudo chown "$USER" /etc/authbind/byport/80
#
# Usage:
#   ./start.sh                  # venv + deps, then run on $PORT (default 5000)
#   PORT=80 ./start.sh          # bind port 80 (needs authbind)
#   HOST=127.0.0.1 ./start.sh   # localhost only
#   SKIP_INSTALL=1 ./start.sh   # reuse existing venv, skip pip install
#   PYTHON=python3.12 ./start.sh

set -euo pipefail

# Always operate from the repo root (the directory this script lives in).
cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3}
VENV_DIR=${VENV_DIR:-venv}
PORT=${PORT:-5000}
HOST=${HOST:-0.0.0.0}
export PORT HOST

# 1. Virtualenv ------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtualenv in $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# 2. Dependencies ----------------------------------------------------------
if [ "${SKIP_INSTALL:-0}" != "1" ]; then
    echo "==> Installing dependencies from requirements.txt"
    python -m pip install --upgrade pip >/dev/null
    python -m pip install -r requirements.txt
else
    echo "==> SKIP_INSTALL=1, reusing existing venv"
fi

# 3. Environment check -----------------------------------------------------
if [ ! -f .env ]; then
    echo "WARNING: no .env file found. The app will start with default/empty"
    echo "         config (no Google OAuth, default SECRET_KEY, etc.)."
fi

# 4. Launch ----------------------------------------------------------------
echo "==> Starting server on $HOST:$PORT"
if [ "$PORT" -lt 1024 ] && command -v authbind >/dev/null 2>&1; then
    exec authbind --deep python website_app.py --no-interactive
else
    exec python website_app.py --no-interactive
fi
