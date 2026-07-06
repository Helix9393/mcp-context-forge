# AI Application Config Paths: Primary and Fallback Reference

Verified: 2026-07-05  
Platform focus: macOS / Unix

Use the **primary** location first. Use a **secondary** location only when its stated scope or compatibility behavior is wanted. A secondary path is not necessarily merged with the primary path.

## Path reference

| Application | Primary (safest documented path) | Secondary / fallback | MCP shape | Selection and conflict rule |
|---|---|---|---|---|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | In-app **Settings > Developer > Edit Config** | `mcpServers` | The menu opens the same user file; do not invent a project-level Claude Desktop file. |
| Claude Code | User settings: `~/.claude/settings.json`; project MCP: `<project>/.mcp.json` | Project settings: `<project>/.claude/settings.json`; local project settings: `<project>/.claude/settings.local.json`; managed settings when deployed by an administrator | `mcpServers` in `.mcp.json`; MCP can also be stored in `~/.claude.json` | These files have different scopes. Prefer `claude mcp add` over manually choosing an internal storage file. Local settings should remain uncommitted. |
| OpenAI Codex CLI / IDE | `~/.codex/config.toml` | Project: `<project>/.codex/config.toml`; system: `/etc/codex/config.toml`; managed configuration when deployed | `[mcp_servers.<name>]` | Use the user file for personal defaults and the project file only for trusted project-specific overrides. `AGENTS.md` contains instructions, not MCP transport configuration. |
| OpenCode | User: `~/.config/opencode/opencode.json` or `.jsonc` | Project: `<project>/opencode.json` or `.jsonc`; managed: `/Library/Application Support/opencode/` | `mcp` | Project configuration is project scope, not a backup copy. Use the managed path only when organization policy supplies it. |
| Gemini CLI | `~/.gemini/settings.json` | Project: `<project>/.gemini/settings.json`; system: `/Library/Application Support/GeminiCli/settings.json` | `mcpServers` | More-specific scopes can override broader settings. `GEMINI.md` is an instruction/context file, not the main settings file. |
| VS Code / GitHub Copilot | User MCP file opened by **MCP: Open User Configuration** | Workspace: `<project>/.vscode/mcp.json` | `servers` | Prefer the command-palette action for the user file because profile storage can vary. Workspace configuration is repository-scoped and should contain no secrets. |
| Cline CLI / extension | CLI: `~/.cline/mcp.json`; extension: open **MCP Servers > Configure MCP Servers** | Extension-managed VS Code global storage; project configuration only where the installed Cline version exposes it | `mcpServers` | For the extension, the UI-opened file is primary because extension storage paths and identifiers can change. Do not copy the CLI path into extension storage by assumption. |
| Roo Code | Open **Roo Code > MCP Servers > Edit Global MCP** | Project: `<project>/.roo/mcp.json` when supported by the installed version | `mcpServers` | Treat the extension UI as authoritative for global storage. Project scope is secondary and may be subject to workspace trust. |
| Windsurf / Cascade / Devin Desktop | `~/.codeium/windsurf/mcp_config.json` | In-app MCP settings editor | `mcpServers` | The UI and file are two access routes to the same user configuration. Avoid guessed VS Code paths. |
| Zed | macOS user settings: `~/.zed/settings.json`; open with **zed: open settings file** | Project: `<project>/.zed/settings.json`; Linux user settings: `~/.config/zed/settings.json` | `context_servers` | On macOS, `~/.zed/settings.json` is primary. `~/.config/zed/settings.json` is the Linux location, not a macOS fallback. Prefer **agent: open settings** for UI-managed MCP setup. |
| Aider | User: `~/.aider.conf.yml` | Repository root or current directory: `.aider.conf.yml`; environment: `.env` | No documented native MCP block | Aider searches multiple scopes for its own YAML configuration. Do not add an MCP schema borrowed from another client. |
| Cursor | Global: `~/.cursor/mcp.json` | Project: `<project>/.cursor/mcp.json`; in-app MCP settings | `mcpServers` | Both paths are officially documented. Use global for personal servers and project scope only for shareable, secret-free configuration. |
| Z.ai ZCode Desktop | User: `~/.zcode/cli/config.json` | Workspace: `<project>/.zcode/config.json`; compatibility fallback: `~/.agents/mcp.json` or `<project>/.agents/mcp.json` | Native: `mcp.servers`; compatibility: `mcpServers` | **Native `.zcode` is primary.** Within the same scope, if `.zcode` contains any MCP servers, ZCode skips the entire `.agents/mcp.json`; it does not merge them. UI edits always write native `.zcode`. Use `enable`, not `enabled`. |
| Google Antigravity 2.0 / IDE / CLI | Shared MCP: `~/.gemini/config/mcp_config.json` | Product-specific documented candidate: `~/.gemini/antigravity/mcp_config.json`; safest discovery: **MCP Servers > Manage MCP Servers > View raw config** | `mcpServers` | **Shared `~/.gemini/config/` is primary** because current Antigravity Codelabs describe it as the central IDE/CLI file and installed Antigravity 2.2.1 uses it. Some Google Workspace product guides instead name `~/.gemini/antigravity/`; treat that as secondary unless **View raw config** opens it. Do not maintain both independently. |

## Installed-machine resolution for the disputed apps

### Antigravity 2.2.1

- Present and active candidate: `~/.gemini/config/mcp_config.json`
- Absent secondary candidate: `~/.gemini/antigravity/mcp_config.json`
- Effective primary for this Mac: `~/.gemini/config/mcp_config.json`
- If a future release changes paths, **View raw config** is the deciding check.

### ZCode 3.2.5

- Present user configuration: `~/.zcode/cli/config.json`
- Native schema found: `mcp.servers`
- Installed application code identifies user and workspace native paths plus `.agents` compatibility paths.
- Effective enable flag: `enable`. The similarly named `enabled` field is nonstandard and should not be relied upon.

### Cursor

- Official documentation now confirms both `~/.cursor/mcp.json` and `<project>/.cursor/mcp.json`.
- It is no longer classified as unverified.

## Safe fallback policy

1. Prefer an in-app **open/edit raw configuration** command when available; it resolves profiles and version-specific storage.
2. Prefer the vendor's native configuration over compatibility files.
3. Keep global personal servers in user scope and repository-safe servers in project scope.
4. Do not duplicate the same server across primary and secondary files unless the vendor explicitly documents merging and precedence.
5. Never place reusable API keys in committed project configuration. Use environment variables, keychains, OAuth, or vendor-managed secret storage.

## Primary sources

- Claude Desktop MCP: <https://modelcontextprotocol.io/docs/develop/connect-local-servers>
- Claude Code settings and MCP: <https://code.claude.com/docs/en/settings>, <https://code.claude.com/docs/en/mcp>
- OpenAI Codex configuration: <https://developers.openai.com/codex/config-basic>, <https://developers.openai.com/codex/config-reference>
- OpenCode configuration and MCP: <https://opencode.ai/docs/config/>, <https://opencode.ai/docs/mcp-servers/>
- Gemini CLI configuration: <https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/configuration.md>
- VS Code MCP: <https://code.visualstudio.com/docs/agent-customization/mcp-servers>
- Cline MCP: <https://docs.cline.bot/mcp/mcp-overview>
- Roo Code MCP: <https://roocodeinc.github.io/Roo-Code/features/mcp/using-mcp-in-roo/>
- Windsurf / Cascade MCP: <https://docs.devin.ai/desktop/cascade/mcp>
- Zed MCP and settings: <https://zed.dev/docs/ai/mcp>, <https://zed.dev/docs/remote-development>
- Aider configuration: <https://aider.chat/docs/config/aider_conf.html>
- Cursor MCP: <https://docs.cursor.com/context/model-context-protocol>
- ZCode MCP: <https://zcode.z.ai/cn/docs/mcp-services>
- Antigravity central MCP configuration: <https://codelabs.developers.google.com/developer-knowledge-mcp-antigravity?hl=en>
- Conflicting Antigravity product-specific path example: <https://developers.google.com/workspace/drive/api/guides/configure-mcp-server>
