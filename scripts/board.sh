#!/usr/bin/env bash
#
# board.sh — ensure the always-on MCP gateway service is running, then open
# the Work Board admin UI in the browser. Personal-workflow convenience that
# mirrors the old `npm run dashboard` ergonomics: one command, service up,
# browser open. The gateway itself now lives in launchd (com.chadkuisel.mcp-gateway,
# see scripts/gateway.sh) so it survives reboots and respawns on crash — this
# script no longer runs uvicorn in the foreground.
#
# Usage:  ./scripts/board.sh
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

wait_for_health() {
  local tries=0
  until curl -sf "$HEALTH" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [[ $tries -ge 60 ]]; then
      echo "⚠️  Gateway did not become healthy after 30s — check: bash scripts/gateway.sh status" >&2
      return 1
    fi
    sleep 0.5
  done
}

if curl -sf "$HEALTH" >/dev/null 2>&1; then
  echo "✅ Gateway already running on ${HOST}:${PORT} — opening Work Board."
else
  echo "🚀 Gateway not running — starting the always-on service…"
  bash scripts/gateway.sh on
  echo "⏳ Waiting for ${HEALTH}…"
  wait_for_health
  echo "✅ Gateway up."
fi

echo "🌐 Opening Work Board…"
open_board
