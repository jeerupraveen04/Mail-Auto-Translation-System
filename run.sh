#!/usr/bin/env bash
# Start the Mail Auto-Translation System backend + frontend (single host).
# Frontend is served by FastAPI at http://localhost:8000/
# Requires PostgreSQL (DATABASE_URL) and optionally Redis (REDIS_URL).

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt
fi

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://edunexa:edunexa@localhost:55432/mail_translation}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend "$@"
