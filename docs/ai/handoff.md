# Handoff

## Last Updated: 2026-07-06 (session 6)

## Goal

Make the mcp-context-forge gateway genuinely usable on Chad's machine: federate his real remote MCP servers behind one URL (OAuth centralized), and auto-propagate future additions from Claude Desktop — effortlessly, for a non-technical solo operator.

## Summary (key decisions + why)

- **Pivot from s5:** dropped "execute Plan 1 (local models)" — it's download-gated/slow (architect + skeptic + reasoning-analysis all flagged local-models-first as wrong). Did the mcp-wrapper remote subset instead (subscription-safe fast win).
- **CourtListener federated via OAuth+DCR** — proved the gateway can wrap an OAuth MCP server with one browser login. MCP wrapping is orthogonal to LLM auth: clients keep their Claude Max subscription; the gateway only proxies tools. (Gateway's own LLM-provider layer is API-key-only — not used.)
- **`desktop-mcp-sync` built** (launchd auto-propagation): add a remote server in Claude Desktop → auto-federate to the hub; skip stdio; notify (manual auth) for OAuth incl. periodic re-auth.
- **Doubt-driven review (skeptic + devils-advocate) caught the tool would silently fail** — validated every finding vs source, wrote + user-approved a fix plan, implemented F1-F13.
- **Security decision:** exclude supabase (prod DB) + github (source write) from the unauth loopback hub (blast radius). supabase gateway deleted; denylist added. Rejected federate-all and add-gateway-auth.
- **F4 (re-auth signal) is the live open thread:** the plan's `POST /gateways/{id}/tools/refresh` mechanism was empirically disproven (dead no-op for authorization_code gateways). An Opus agent is rebuilding it as an active probe.

## Current task

**IN FLIGHT — Opus agent `a585486247cf457d3`** building the active-probe re-auth health signal in `tools/desktop-mcp-sync/sync.py`. Design: only probe when `oauth_tokens.expires_at` is past; dynamically pick a safe read-only zero-arg tool per OAuth server; invoke it via the hub MCP endpoint to force token use; auth error → notify with login link; success → quiet. Must empirically prove Case A (expired access + valid refresh → auto-refresh → ok) and Case B (dead refresh → auth error → notify) on CourtListener's token with snapshot+restore. **On resume: check this agent's completion (its result was NOT in context at handoff)** — read the completion notification / `SendMessage(to:'a585486247cf457d3')`, verify Case A/B evidence, confirm the token row was restored, then have the plan's remaining verification satisfied.

## State (verified this session)

- **Gateway** `http://127.0.0.1:4444`, always-on launchd `com.chadkuisel.mcp-gateway`. Unauthenticated on loopback (all API calls with `MCPGATEWAY_BEARER_TOKEN` UNSET). Restart: `launchctl kickstart -k gui/$(id -u)/com.chadkuisel.mcp-gateway`.
- **Hub virtual server** `2bdbd4fbb65f4bdf9c072ea2db37d9eb`, renamed **"gateway-hub"**, **29 tools** (courtlistener 16 + descrybe 13). Client entries `courtlistener-gw` point at `http://127.0.0.1:4444/servers/2bdbd4fbb65f4bdf9c072ea2db37d9eb/mcp` in: `~/.claude.json`, `~/.config/opencode/opencode.json`, `~/.zcode/v2/config.json`, `~/.codex/config.toml` (each backed up `.bak-<ts>`).
- **Federated gateways:** courtlistener (`6fd2933c91eb418881e8c02f8c852700`, OAuth, live token under `admin@example.com`), descrybe (`a57dd4d4747c4b0f9410df2edc177048`, no-auth, 13 tools), midpage (`69e38eb77d924ca682b598c4050d1a29`, OAuth **pending login**), midpage-legal-research (`7762399fb4364b199ea42e9d4bf997c5`, OAuth **pending login**). **supabase DELETED** (security). github never federated (no DCR endpoint).
- **`desktop-mcp-sync`**: `tools/desktop-mcp-sync/` (sync.py + README.md) — **UNTRACKED, uncommitted**. launchd agent `com.chadkuisel.desktop-mcp-sync` installed (`~/Library/LaunchAgents/...plist`, WatchPaths Claude Desktop config + StartInterval 1800, python `/opt/homebrew/bin/python3`). F1-F13 implemented; F4 being rebuilt (see Current task).
- Approved fix plan: `~/.claude/plans/skip-create-a-plan-stateless-rossum.md`.

## Blockers / awaiting user

- **Restart Claude Code** → `gateway-hub` (29 tools) appears under `courtlistener-gw`.
- **2 pending OAuth logins** (open in browser): midpage → `http://127.0.0.1:4444/oauth/authorize/69e38eb77d924ca682b598c4050d1a29` ; midpage-legal-research → `http://127.0.0.1:4444/oauth/authorize/7762399fb4364b199ea42e9d4bf997c5`. After login, run `python3 tools/desktop-mcp-sync/sync.py` (or wait for the timer) → reconcile auto-attaches their tools to the hub.

## References

- Verified API traps: GatewayRead/ServerRead REDACT `auth_type`/`oauth_config`/`associated_tools` → verify via `sqlite3 -readonly mcp.db`. CREATE `/servers` = NESTED body `{"server":{...},"visibility"}`; UPDATE `/servers/{id}` = FLAT body `{"associated_tools":[...],"visibility"}` + full-replace. DCR: omit `client_id` in `oauth_config` → auto-registers (`dcr_enabled` default True).
- Key files: `mcpgateway/main.py` (routes: register_gateway ~7079, create_server ~4202, update_server ~4289, refresh_gateway_tools 7336), `mcpgateway/services/{gateway_service,server_service,token_storage_service,dcr_service}.py`, `mcpgateway/middleware/rbac.py:429`.
- github manual OAuth-app (client_id/secret in oauth_config) if ever wanted — no DCR.

## Constraints (standing, not yet in CLAUDE.md)

- Only SSE/STREAMABLEHTTP federate; stdio needs a per-server `mcpgateway.translate` bridge (deliberately not done — fragile).
- All gateway writes reversible; never edit client configs or the Claude Desktop config destructively.
- Unauth-loopback-admin is safe ONLY while `HOST=127.0.0.1` + the two auth flags hold; re-secure if ever network-bound.
