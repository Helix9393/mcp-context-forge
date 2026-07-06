#!/usr/bin/env bash
set -euo pipefail

# Deep local discovery helper for tools whose public config docs are incomplete:
# Z.ai ZCode, Google Antigravity, Cursor.
# This prints candidate files only. It does not modify anything.

printf '\n== Candidate app support directories ==\n'
find "$HOME/Library/Application Support" -maxdepth 3 \
  \( -iname '*zcode*' -o -iname '*z.ai*' -o -iname '*z ai*' -o -iname '*antigravity*' -o -iname '*cursor*' \) \
  -print 2>/dev/null || true

printf '\n== Candidate dot-config directories ==\n'
find "$HOME/.config" -maxdepth 4 \
  \( -iname '*zcode*' -o -iname '*z.ai*' -o -iname '*antigravity*' -o -iname '*cursor*' \) \
  -print 2>/dev/null || true

printf '\n== Project-local MCP/config candidates under current directory ==\n'
find . -maxdepth 5 \
  \( -path '*/.cursor/mcp.json' -o -path '*/.vscode/mcp.json' -o -name '.mcp.json' -o -name '*mcp*.json' -o -name '*settings*.json' \) \
  -print 2>/dev/null || true

printf '\n== Grep for MCP config keys in likely app support dirs ==\n'
for dir in \
  "$HOME/Library/Application Support/ZCode" \
  "$HOME/Library/Application Support/Z.ai" \
  "$HOME/Library/Application Support/Antigravity" \
  "$HOME/Library/Application Support/Google/Antigravity" \
  "$HOME/Library/Application Support/Cursor" \
  "$HOME/.config"; do
  if [[ -d "$dir" ]]; then
    printf '\n-- %s --\n' "$dir"
    grep -RIl "mcpServers\|mcp_servers\|context_servers\|mcp_config\|Model Context Protocol" "$dir" 2>/dev/null | head -100 || true
  fi
done

printf '\n== macOS plist/domain candidates ==\n'
# These may reveal app bundle identifiers or settings domains.
defaults domains 2>/dev/null | tr ',' '\n' | grep -Ei 'zcode|z\.ai|antigravity|cursor' || true

printf '\nDone. Review candidate files manually before editing.\n'
