# AI Coding Tool Config Path Matrix

Generated: 2026-07-05

This packet collects **documented/default configuration locations and starter config shapes** for common AI coding tools and MCP hosts. It is intended as a practical reference, not an installer.

## Confidence legend

- **High**: backed by official vendor/project documentation found during web research.
- **Medium**: backed by official documentation for the tool family or UI, but exact local path may vary by OS/profile.
- **Low / unverified**: no reliable official source found during this pass; inspect the local app or vendor docs before use.

| Tool | Primary config file(s) | macOS / Unix path | Windows path | Project/workspace path | MCP key shape | Confidence |
|---|---|---:|---:|---:|---|---|
| Claude Desktop | `claude_desktop_config.json` | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` | N/A | `mcpServers` | High |
| Claude Code CLI | `settings.json`, `.mcp.json`, `~/.claude.json`, `CLAUDE.md` | `~/.claude/settings.json`, `~/.claude.json` | `%USERPROFILE%\.claude\settings.json`, `%USERPROFILE%\.claude.json` | `.claude/settings.json`, `.claude/settings.local.json`, `.mcp.json`, `CLAUDE.md` | `mcpServers` in `.mcp.json`; MCP also in `~/.claude.json` | High |
| OpenAI Codex CLI / IDE | `config.toml`, profile configs, `AGENTS.md` | `~/.codex/config.toml`; system `/etc/codex/config.toml` | Docs emphasize shared CLI/IDE layers; confirm Windows app location locally | `.codex/config.toml`, `AGENTS.md` | `[mcp_servers.<id>]` | High |
| OpenCode CLI / TUI / Desktop | `opencode.json`, `opencode.jsonc`, `tui.json` | `~/.config/opencode/opencode.json`; managed `/Library/Application Support/opencode/` | `%ProgramData%\opencode` for managed config | `opencode.json`, `.opencode/` dirs | `mcp` object | High |
| Gemini CLI | `settings.json`, `GEMINI.md`, `.env` | `~/.gemini/settings.json`; system `/Library/Application Support/GeminiCli/settings.json` | `C:\ProgramData\gemini-cli\settings.json` | `.gemini/settings.json`, `GEMINI.md`, `.gemini/.env` | `mcpServers` | High |
| VS Code / GitHub Copilot MCP | `mcp.json`, `settings.json` | User profile MCP config via command; workspace `.vscode/mcp.json` | User profile MCP config via command; workspace `.vscode/mcp.json` | `.vscode/mcp.json` | `servers` | High |
| Cline CLI / Extension | `mcp.json`, extension MCP settings JSON | `~/.cline/mcp.json` for CLI | likely `%USERPROFILE%\.cline\mcp.json` for CLI; confirm locally | Extension opens its MCP settings JSON from UI | `mcpServers` | High for CLI path; Medium for IDE extension location |
| Roo Code VS Code Extension | MCP settings JSON through extension UI | Extension-managed VS Code/global storage; exact path not provided in cited doc | Extension-managed VS Code/global storage; exact path not provided in cited doc | Often managed by extension UI | `mcpServers` | Medium |
| Windsurf / Cascade / Devin Desktop | `mcp_config.json` | `~/.codeium/windsurf/mcp_config.json` | Same logical Codeium/Windsurf config tree; confirm Windows exact path locally | N/A | `mcpServers` | High for macOS/Unix path; Medium for Windows exact path |
| Zed | Zed settings file | Open with `zed: open settings file`; OS-specific path omitted in cited MCP doc | Open with `zed: open settings file` | Zed settings file | `context_servers` | Medium |
| Aider | `.aider.conf.yml`, `.env` | `~/.aider.conf.yml`, repo root/current dir `.aider.conf.yml` | Home/repo/current dir `.aider.conf.yml` | repo root or current dir `.aider.conf.yml` | No native MCP config found in cited Aider config page | High |
| Cursor | MCP config in Cursor settings / `.cursor/mcp.json` in current ecosystem docs, but source page was not parseable in this run | Need verify in Cursor docs/app | Need verify in Cursor docs/app | Often `.cursor/mcp.json` | `mcpServers` | Low / unverified in this packet |
| Z.ai ZCode Desktop | Unknown | Not found in reliable official source during this pass | Not found | Unknown | likely MCP-compatible, but unverified | Low / unverified |
| Google Antigravity IDE/CLI | Unknown; likely VS Code-derived settings plus app-specific state, but no official local config path found in this pass | Inspect app docs/settings/storage locally | Inspect locally | likely workspace settings if VS Code-derived | Unknown | Low / unverified |

## Notes

1. **Do not paste real API keys into shared configs.** Prefer environment variables or OS keychains.
2. **MCP config formats are similar but not identical.** Claude/Cline/Roo/Windsurf use `mcpServers`; VS Code uses `servers`; Codex uses TOML tables; OpenCode uses `mcp`; Zed uses `context_servers`.
3. **For VS Code forks** such as Cursor, Windsurf, Antigravity, and Zed-like editors, local storage may depend on the app’s Electron/VS Code profile directories. Prefer the app’s command palette action to open the authoritative settings file.
