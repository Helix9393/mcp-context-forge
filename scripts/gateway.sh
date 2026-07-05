#!/usr/bin/env bash
#
# gateway.sh — on/off/status/restart control for the always-on MCP gateway
# launchd service (com.chadkuisel.mcp-gateway). Wraps `launchctl` for the
# modern per-user GUI domain so the gateway survives reboots and respawns
# on crash (RunAtLoad + KeepAlive in the plist).
#
# Usage: scripts/gateway.sh {on|off|status|restart}
#
set -euo pipefail

LABEL="com.chadkuisel.mcp-gateway"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
HEALTH_URL="http://127.0.0.1:4444/health"

usage() {
  echo "Usage: $0 {on|off|status|restart}" >&2
  exit 1
}

health_line() {
  if curl -sf -o /dev/null -w '' "$HEALTH_URL" 2>/dev/null; then
    echo "health: UP (${HEALTH_URL} -> 200)"
  else
    echo "health: DOWN (${HEALTH_URL} not responding)"
  fi
}

cmd_on() {
  if [[ ! -f "$PLIST" ]]; then
    echo "Plist not found at ${PLIST}" >&2
    exit 1
  fi
  # bootstrap is idempotent in effect for us: tolerate "already loaded" (bootstrap
  # failure code 5/EEXIST-ish messages from launchctl) rather than treating it as fatal.
  out=$(launchctl bootstrap "$DOMAIN" "$PLIST" 2>&1) && rc=0 || rc=$?
  if [[ $rc -ne 0 ]] && ! grep -qi "already\|bootstrap failed: 5" <<<"$out"; then
    echo "$out" >&2
    exit "$rc"
  fi
  echo "Gateway service bootstrapped (${LABEL})."
}

cmd_off() {
  out=$(launchctl bootout "${DOMAIN}/${LABEL}" 2>&1) && rc=0 || rc=$?
  if [[ $rc -ne 0 ]] && ! grep -qi "no such process\|not loaded\|could not find" <<<"$out"; then
    echo "$out" >&2
    exit "$rc"
  fi
  echo "Gateway service booted out (${LABEL})."
}

cmd_status() {
  echo "--- launchctl print ${DOMAIN}/${LABEL} ---"
  launchctl print "${DOMAIN}/${LABEL}" 2>&1 | grep -E "state|pid|program|path =" || echo "(service not loaded)"
  echo "--- ${HEALTH_URL} ---"
  health_line
}

cmd_restart() {
  launchctl kickstart -k "${DOMAIN}/${LABEL}"
  echo "Gateway service kickstarted (${LABEL})."
}

case "${1:-}" in
  on) cmd_on ;;
  off) cmd_off ;;
  status) cmd_status ;;
  restart) cmd_restart ;;
  *) usage ;;
esac
