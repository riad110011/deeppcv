#!/bin/sh
set -eu

PORT="${PORT:-8080}"
case "$PORT" in
    ''|*[!0-9]*) PORT=8080 ;;
esac

GUNICORN_TIMEOUT_SECONDS="${GUNICORN_TIMEOUT_SECONDS:-120}"
case "$GUNICORN_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) GUNICORN_TIMEOUT_SECONDS=120 ;;
esac

if [ -x /opt/venv/bin/gunicorn ]; then
    exec /opt/venv/bin/gunicorn --bind "0.0.0.0:${PORT}" --timeout "$GUNICORN_TIMEOUT_SECONDS" app:app
fi

exec gunicorn --bind "0.0.0.0:${PORT}" --timeout "$GUNICORN_TIMEOUT_SECONDS" app:app
