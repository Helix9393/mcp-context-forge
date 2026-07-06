# Agent-Style Verification Report: Previously Unverified Targets

Generated: 2026-07-05

## Scope

This follow-up targeted the entries previously marked unverified:

- Z.ai ZCode Desktop
- Google Antigravity IDE/CLI
- Cursor MCP config path

## Result summary

| Tool | Result | Confidence | Practical next step |
|---|---|---:|---|
| Z.ai ZCode Desktop | Public search confirmed ZCode exists and is a new AI coding tool, but did not surface an official config path, MCP config file name, or documented local settings location. | Low | Inspect local app support directories and app command palette/settings UI. |
| Google Antigravity IDE/CLI | Public search confirmed Antigravity exists and appears VS Code-derived, but did not surface an official local config path or MCP file format. | Low | Treat VS Code-style locations as a hypothesis only; inspect the installed app. |
| Cursor | Existing packet kept Cursor unverified because the docs page was reachable but not parseable in the prior browsing pass. Common MCP examples reference `.cursor/mcp.json`, but this packet does not treat that as confirmed without a parseable official source. | Low/Medium | Open Cursor’s MCP docs or command palette locally; inspect `.cursor/mcp.json` and Application Support/Cursor. |

## Z.ai ZCode Desktop

### Searches attempted

- `Z.ai ZCode config file path MCP config zcode desktop`
- `site:z.ai ZCode MCP config file path`
- `Z Code desktop MCP config JSON file z.ai`
- `"ZCode" "mcpServers" config`
- `"Z.ai Code" MCP config`
- `"Z.ai" "Z Code" desktop MCP`
- `"Z.ai" "zcode" "MCP"`
- `"Z.ai Code" "mcpServers"`
- `Z.ai Z Code desktop documentation`
- `Z.ai code editor documentation MCP`
- `site:docs.z.ai code IDE MCP`
- `site:z.ai/code MCP`

### What was found

Public results found news coverage describing ZCode as a newly released AI coding tool by Z.ai, but not a reliable official documentation page for local config files or MCP configuration.

### Working conclusion

Do **not** assume that ZCode uses Cursor, VS Code, Claude Desktop, or Codex config locations. Until local inspection confirms otherwise, treat ZCode as undocumented for config-file purposes.

## Google Antigravity IDE/CLI

### Searches attempted

- `Google Antigravity IDE MCP config file path`
- `Antigravity IDE config file mcpServers`
- `Google Antigravity CLI config location settings.json`
- `site:antigravity.google MCP config Antigravity IDE`
- `Antigravity documentation MCP servers config`
- `"Antigravity" "mcpServers"`
- `"Antigravity" ".antigravity" settings json`
- `"Google Antigravity" "settings.json"`
- `antigravity.google documentation settings JSON MCP`
- `antigravity.google docs MCP server`
- `antigravity google ide mcp server configuration`
- `Google Antigravity IDE documentation mcp servers`

### What was found

Public results describe Antigravity as an AI-assisted IDE and a VS Code-derived / VS Code-like platform, but did not surface an authoritative config file path or MCP config schema.

### Working conclusion

Use VS Code compatibility only as a **hypothesis**, not as a confirmed config rule. On macOS, likely candidate locations to inspect are:

```bash
find "$HOME/Library/Application Support" -maxdepth 3 \
  \( -iname '*Antigravity*' -o -iname '*Google*Antigravity*' \) -print 2>/dev/null

find "$HOME/.config" -maxdepth 3 -iname '*antigravity*' -print 2>/dev/null
```

Also try the in-app command palette:

- Open User Settings (JSON)
- Open Workspace Settings (JSON)
- MCP / Agent settings commands
- Developer: Open Logs Folder
- Developer: Open Extensions Folder

## Cursor

### Searches attempted

- `Cursor IDE MCP config file path mcp.json official docs`
- `Cursor MCP config .cursor/mcp.json documentation`
- `docs.cursor.com MCP configuration mcp.json`
- `Cursor settings mcpServers config file`
- `site:cursor.com/docs MCP .cursor/mcp.json`
- `site:docs.cursor.com "mcp.json" "Cursor"`
- `site:cursor.com/docs "mcp.json"`
- `".cursor/mcp.json"`
- `".cursor/mcp.json" "mcpServers"`
- `"~/.cursor/mcp.json"`
- `"Cursor" "mcpServers" ".cursor"`
- `"mcp.json" "Cursor" "global"`

### What was found

The Cursor docs page was reachable during browsing but did not provide parseable content in this environment. Because of that, the packet still marks Cursor as not source-verified.

### Working conclusion

The practical candidate paths remain:

```bash
# Project-local candidate
./.cursor/mcp.json

# macOS app support candidates
find "$HOME/Library/Application Support/Cursor" -maxdepth 4 -iname '*mcp*' -print 2>/dev/null
```

Prefer opening Cursor’s MCP settings through the app UI rather than writing directly to an assumed path.

## Bottom line

The deeper pass did **not** verify ZCode or Antigravity default config files from reliable public documentation. The updated packet therefore adds stronger local-forensics scripts instead of promoting guessed paths to “confirmed.”
