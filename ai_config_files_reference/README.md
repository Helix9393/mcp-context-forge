# AI Config Files Reference Packet

This zip contains:

- `CONFIG_PATH_MATRIX.md` — tool-by-tool config path and schema matrix.
- `FALLBACK_CONFIG_PATHS.md` — primary-versus-secondary path list with conflict and precedence rules.
- `templates/` — starter config examples for each tool where docs support one.
- `notes/unverified_zcode_antigravity_cursor.md` — unresolved tools and local inspection commands.
- `scripts/find_ai_config_files_macos.sh` — macOS helper script to locate existing config files.
- `SOURCES.md` — URLs used for the research.
- `AGENT_VERIFICATION_REPORT.md` — deeper pass on ZCode, Antigravity, and Cursor.
- `scripts/deep_find_unverified_ai_configs_macos.sh` — targeted macOS discovery for unverified tools.

## Recommended use

1. Start with `CONFIG_PATH_MATRIX.md`.
2. Copy only the relevant template into the relevant app’s actual config path.
3. Replace placeholders like `/absolute/path`, `USERNAME`, and `${env:TOKEN}`.
4. Avoid committing credentials.
5. Restart the relevant app after changing MCP config unless that app explicitly supports hot reload.

## Important

The templates are safe starter shapes, not guaranteed drop-in final configs for your machine. MCP servers execute local commands; only install servers you trust.
