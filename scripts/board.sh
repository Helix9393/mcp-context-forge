#!/usr/bin/env bash
#
# board.sh — start the MCP gateway (if not already running) and open the Work
# Board admin UI in the browser. Personal-workflow convenience that mirrors the
# old `npm run dashboard` ergonomics: one command, server up, browser open.
#
# Usage:  ./scripts/board.sh
#         (Ctrl-C stops the server when this command started it.)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HOST="127.0.0.1"
PORT="4444"
URL="http://${HOST}:${PORT}/admin"
HEALTH="http://${HOST}:${PORT}/health"

open_board() {
  # Prefer Chrome; fall back to the default browser if Chrome isn't installed.
  open -a "Google Chrome" "$URL" 2>/dev/null || open "$URL"
}

# Already running (e.g. started earlier or in another terminal)? Just open it.
if curl -sf "$HEALTH" >/dev/null 2>&1; then
  echo "✅ Gateway already running on ${HOST}:${PORT} — opening Work Board."
  open_board
  exit 0
fi

echo "🚀 Starting gateway on ${HOST}:${PORT} (Ctrl-C to stop)…"

# Wait for the server to answer health, then open the browser — in the
# background so it doesn't block the foreground server process below.
( until curl -sf "$HEALTH" >/dev/null 2>&1; do sleep 0.5; done
  echo "🌐 Opening Work Board…"
  open_board ) &

# Foreground, single worker (best for the single-user SQLite board). Loads
# .env automatically via pydantic-settings since cwd is the repo root.
exec .venv/bin/uvicorn mcpgateway.main:app --host "$HOST" --port "$PORT"
