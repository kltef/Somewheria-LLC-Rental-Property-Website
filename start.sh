#!/usr/bin/env bash
# Run the app on port 80 (or $PORT) without root.
#
# Requires authbind:
#   sudo apt install authbind
#   sudo touch /etc/authbind/byport/80
#   sudo chmod 500 /etc/authbind/byport/80
#   sudo chown $USER /etc/authbind/byport/80
#
# Usage:
#   ./start.sh          # binds port 80
#   PORT=8080 ./start.sh

set -euo pipefail

PORT=${PORT:-80}
export PORT

if [ "$PORT" -lt 1024 ]; then
    exec authbind --deep python website_app.py
else
    exec python website_app.py
fi
