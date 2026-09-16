#!/usr/bin/env bash
# Run STTM standalone locally (backend + Vite frontend).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
STTM_BACKEND="$ROOT/backend"
STTM_FRONTEND="$ROOT/frontend"

echo "==> STTM standalone"
echo "    Backend:  $STTM_BACKEND"
echo "    Frontend: $STTM_FRONTEND"

if [[ ! -f "$STTM_BACKEND/.env" && -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env" "$STTM_BACKEND/.env"
  echo "    Copied .env -> backend/.env"
fi

if [[ ! -f "$STTM_BACKEND/.env" && -f "$STTM_BACKEND/.env.example" ]]; then
  cp "$STTM_BACKEND/.env.example" "$STTM_BACKEND/.env"
  echo "    Created backend/.env from .env.example — set LLM keys in Settings if needed"
fi

cleanup() {
  echo ""
  echo "==> Shutting down..."
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Starting backend on http://127.0.0.1:8000"
(
  cd "$STTM_BACKEND"
  if [[ ! -d .venv ]]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
  fi
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
) &
BACKEND_PID=$!

echo "==> Starting frontend on http://127.0.0.1:5173"
(
  cd "$STTM_FRONTEND"
  if [[ ! -d node_modules ]]; then
    npm install
  fi
  npm run dev -- --host 127.0.0.1 --port 5173
) &
FRONTEND_PID=$!

echo ""
echo "    Open http://127.0.0.1:5173 to use STTM (no login required)."
echo "    Press Ctrl+C to stop."
echo ""

wait
