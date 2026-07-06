#!/usr/bin/env bash
set -euo pipefail

printf '\n== Home-level known config files ==\n'
for p in \
  "$HOME/.claude/settings.json" \
  "$HOME/.claude.json" \
  "$HOME/Library/Application Support/Claude/claude_desktop_config.json" \
  "$HOME/.codex/config.toml" \
  "$HOME/.config/opencode/opencode.json" \
  "$HOME/.config/opencode/tui.json" \
  "$HOME/.gemini/settings.json" \
  "$HOME/.cline/mcp.json" \
  "$HOME/.codeium/windsurf/mcp_config.json" \
  "$HOME/.aider.conf.yml"; do
  if [[ -e "$p" ]]; then
    echo "FOUND: $p"
  else
    echo "missing: $p"
  fi
done

printf '\n== Project-level files under current directory ==\n'
find . -maxdepth 4 \( \
  -name '.mcp.json' -o \
  -path '*/.claude/settings.json' -o \
  -path '*/.claude/settings.local.json' -o \
  -path '*/.codex/config.toml' -o \
  -path '*/.gemini/settings.json' -o \
  -path '*/.vscode/mcp.json' -o \
  -path '*/.cursor/mcp.json' -o \
  -name 'opencode.json' -o \
  -name 'opencode.jsonc' -o \
  -name 'AGENTS.md' -o \
  -name 'CLAUDE.md' -o \
  -name 'GEMINI.md' -o \
  -name '.aider.conf.yml' \
\) -print 2>/dev/null

printf '\n== Broad app-support MCP grep, first 100 hits ==\n'
grep -RIl "mcpServers\|mcp_servers\|context_servers\|mcp_config" \
  "$HOME/Library/Application Support" "$HOME/.config" 2>/dev/null | head -100 || true
