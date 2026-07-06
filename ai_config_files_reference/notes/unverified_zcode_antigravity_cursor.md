# Unverified Targets: ZCode, Antigravity, Cursor

## Z.ai ZCode Desktop

I did not find a reliable official source for default config files or MCP config paths during this pass. Do not assume a config path based on another Electron/VS Code-like app.

Suggested local inspection commands on macOS:

```bash
find ~/Library/Application\ Support -maxdepth 3 -iname '*zcode*' -o -iname '*z.ai*' 2>/dev/null
find ~/.config -maxdepth 3 -iname '*zcode*' -o -iname '*z.ai*' 2>/dev/null
```

Suggested strings to look for inside candidate app support directories:

```bash
grep -R "mcpServers\|mcp_servers\|context_servers\|mcp_config" ~/Library/Application\ Support 2>/dev/null | head -50
```

## Google Antigravity IDE/CLI

I found general web references that Antigravity is VS Code-derived, but not an authoritative default local config path for MCP/config files. Treat VS Code compatibility as a hypothesis until confirmed in-app.

Suggested local checks:

```bash
find ~/Library/Application\ Support -maxdepth 3 -iname '*Antigravity*' -o -iname '*Google*Antigravity*' 2>/dev/null
find ~/.config -maxdepth 3 -iname '*antigravity*' 2>/dev/null
```

Also use Command Palette actions such as:

- Open User Settings (JSON)
- Open Workspace Settings (JSON)
- MCP / Agent settings commands, if present

## Cursor

Cursor docs were reachable but not parseable in this browsing session. Current ecosystem references commonly use Cursor settings or `.cursor/mcp.json`, but this packet does not cite that as verified.

Suggested local checks:

```bash
find . -maxdepth 3 -path '*/.cursor/*' -o -name 'mcp.json'
find ~/Library/Application\ Support/Cursor -maxdepth 4 -iname '*mcp*' 2>/dev/null
```
