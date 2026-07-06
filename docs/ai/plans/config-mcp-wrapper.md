# Config Plan: Wrap Chad's MCP Servers Behind the Gateway

Status: DRAFT — read-only analysis only, nothing registered yet.
Scope: MCP server wrapping & virtual servers on the local mcp-context-forge gateway (`http://127.0.0.1:4444`).

> **Doubt-review applied (adversarial pass, live-tested against the running gateway).** Two corrections folded in: (a) **no login / session / CSRF is needed** — an unauthenticated `POST /admin/gateways` was live-tested and returned 422 (validation), not 401; writes succeed as the platform admin on this instance (`AUTH_REQUIRED=false` + `ALLOW_UNAUTHENTICATED_ADMIN=true`). The old "must log in" prerequisite was a phantom obstacle — removed. (b) The Stage 1 stdio-bridge **verify curl would falsely report "broken"** on a working bridge (StreamableHTTP needs an `initialize` handshake + dual `Accept` header + session id); verify via the Admin UI's Test/Fetch-Tools instead, which does the handshake for you. Corrections are inline below.

---

## Goal

Right now Chad's AI clients (Claude Code, Cursor, Codex) each point at a scattered list of MCP servers directly. The goal is to register those servers **into the one gateway that's already running on this Mac**, so every client can eventually point at a single URL instead of N separate server configs — one place to add, remove, or rotate credentials for a tool.

This plan only covers **getting servers registered in the gateway**. Repointing each client's config at the gateway is a separate, later step (not in scope here).

---

## Current State (verified)

### What's already registered in the gateway

Queried the running gateway directly (read-only GET, no auth needed for this endpoint locally):

```
curl http://127.0.0.1:4444/admin/servers
```

Result: **one Virtual Server** already exists —

| Field | Value |
|---|---|
| id | `c312ad6725614fdb903d7a3ec6c966e7` |
| name | `work-board` |
| tools | 14 tools, all `work-board-*` (launch, pending, get-backlog, refresh-git, add-note, etc.) |
| created via | `api`, by `admin@example.com` |

This matches the `mcp__work-board__*` tools already visible in this session and the `work-board` entry Chad has wired into Codex's `config.toml` (`url = "http://127.0.0.1:4444/servers/c312ad6725614fdb903d7a3ec6c966e7/mcp"`). So the gateway is not empty — Chad has already built and exposed **one custom Virtual Server from his own REST-backed tools**.

**Gateways (federated upstream MCP peers): zero.** `select count(*) from gateways` on `mcp.db` → `0`. Chad has never registered an external MCP server (Supabase, Context7, playwright, etc.) into this gateway. Everything he uses today, every AI client talks to directly.

`select count(*) from tools` → `14` (all work-board tools; no wrapped external tools yet).

### What Chad already runs today (evidence: `~/.claude.json` top-level `mcpServers`, `~/.cursor/mcp.json`, `~/.codex/config.toml`)

De-duplicated candidate list, transport as declared in each client config:

| Server | Transport (as configured) | Where seen | Notes |
|---|---|---|---|
| `chrome-devtools` | stdio (`npx chrome-devtools-mcp@latest`) | Claude Code | |
| `sequential-thinking` | stdio (`npx @modelcontextprotocol/server-sequential-thinking`) | Cursor, Codex | Already a live deferred tool in this session |
| `context7` (upstash) | stdio (`npx @upstash/context7-mcp@1.0.31`) | Claude Code, Cursor, Codex | Needs `CONTEXT7_API_KEY`; Codex config uses `${input:CONTEXT7_API_KEY}` — **not a static value**, needs resolving before it can be wrapped headlessly |
| `markitdown` | stdio (`uvx markitdown-mcp@0.0.1a4`) | Claude Code, Codex | Already a live deferred tool in this session |
| `kraang` | stdio (`uvx kraang serve`) | Claude Code, Cursor, Codex | `KRAANG_DB_PATH` is still the literal placeholder `/YOUR/CUSTOM/PATH/.kraang/kraang.db` in all three configs — **this server looks unconfigured/broken today**, verify before wrapping |
| `playwright` | stdio (`npx @playwright/mcp@latest`) | Cursor only | |
| `frontend-visualqa` | stdio (local `frontend-visualqa serve` binary) | Claude Code only | Project-specific tool |
| `GitKraken` | stdio (local GitLens-bundled binary, `--host=cursor` etc.) | Claude Code, Cursor | Args are Cursor/GitLens-specific (`--scheme=cursor`) — may not function identically once proxied through a generic bridge; low priority |
| `com.supabase/mcp` | http, `https://mcp.supabase.com/mcp` | Claude Code, Cursor, Codex | |
| `courtlistener` | http, `https://mcp.courtlistener.com/`, but config also carries a `command`/`args`/`env` (incl. `COURTLISTENER_API_TOKEN`, `REDIS_URL`) | Cursor, Codex | Config shape is ambiguous — both a URL and a local command are present; verify which one the client actually uses before wrapping |
| `descrybe` | http, `https://mcp.descrybe.com/mcp` | Claude Code, Cursor, Codex | No auth in config |
| `midpage` | http, `https://app.midpage.ai/mcp` | Claude Code, Cursor, Codex (disabled in Codex) | |
| `midpage-legal-research` | http, `https://app.midpage.ai/mcp/v3` | Codex (disabled) | |
| `github-mcp-server` | http, `https://api.githubcopilot.com/mcp/` | Claude Code, Cursor, Codex (disabled in Codex) | |

**14 real candidate servers.** Not counted: `work-board` (already gateway-native), `node_repl` (Codex.app-internal browser-control binary, not a portable MCP server).

Project-scoped Claude Code configs for both `Atlas-Copilot` and `mcp-context-forge` are empty (`{}`) — all of Chad's MCP servers live in the **global** `~/.claude.json` `mcpServers` block, not per-project.

---

## Prerequisites (each verified against this machine)

1. **Gateway is up and reachable.** `curl http://127.0.0.1:4444/health` → `{"status":"healthy", ...}`. Confirmed.
2. **Gateway only federates SSE and StreamableHTTP upstreams — not stdio directly.** `mcpgateway/schemas.py:2826`: `GATEWAY_SUPPORTED_TRANSPORTS: frozenset[str] = frozenset({"SSE", "STREAMABLEHTTP"})`, enforced by `_validate_transport_string` on both `GatewayCreate` and `GatewayUpdate`. `mcpgateway/services/gateway_service.py:4732` raises `GatewayConnectionError` for any other transport. **This means 8 of Chad's 14 servers (all the stdio ones — chrome-devtools, sequential-thinking, context7, markitdown, kraang, playwright, frontend-visualqa, GitKraken) cannot be peer-registered as-is.**
3. **The repo ships a bridge for exactly this gap: `mcpgateway.translate`.** Confirmed by running `python -m mcpgateway.translate --help` in the gateway's own venv (`/Users/chadkuisel/Workspace/mcp-context-forge/.venv/bin/python`) and reading `docs/docs/using/mcpgateway-translate.md`. It spawns the stdio server as a subprocess and re-exposes it locally over SSE (`--expose-sse`, endpoints `/sse` + `/message`) or StreamableHTTP (`--expose-streamable-http`, endpoint `/mcp`) on a port you choose. Example from the docs: `python3 -m mcpgateway.translate --stdio "uvx mcp-server-git" --expose-sse --port 9001`. That local URL is what then gets registered as a Gateway peer.
4. **Gateway registration accepts per-upstream auth**, not just a bare URL. `mcpgateway/schemas.py:2847` (`GatewayCreate`) has `auth_type` (`basic`, `bearer`, `authheaders`, `oauth`, `query_param`, or `none`), plus `auth_username`/`auth_password`, `auth_token`, `auth_header_key`/`auth_header_value`. Relevant for `courtlistener` (API token) and any of the hosted HTTP servers that turn out to need one.
5. **Admin UI panels exist and match this model.** `mcpgateway/templates/admin.html`: a **"Gateways"** panel (label: "MCP Servers & Federated Gateways (MCP Registry)" — line 4890, "Register external MCP Servers (SSE/HTTP) to retrieve their [tools]") is the peer/federation registration surface, and a separate **"Virtual Servers"** panel (line 2295, "Virtual Servers let you combine Tools, Resources, and Prompts into an MCP Server with its own API key") is the composition surface — confirms the two-tier model: register upstream peers under Gateways, then compose selected tools/resources/prompts from those peers (plus any custom Tools) into a Virtual Server that a client actually points at.
6. **Writes succeed unauthenticated on this instance — CORRECTED (live-tested).** `.env` has `AUTH_REQUIRED=false` and `ALLOW_UNAUTHENTICATED_ADMIN=true`. An unauthenticated `POST /admin/gateways` with an empty body was live-tested and returned **HTTP 422** (Pydantic body validation), **not** 401/403 — meaning the request already passed authN/authZ as the platform admin and failed only on missing body content. **No login, no session cookie, and no CSRF token are required** to register a gateway/server on this box. (The earlier draft mis-read `allow_admin_bypass=False` as a write-lock; it's only an RBAC flag, and the unauthenticated-admin identity genuinely *holds* `gateways.create`. Note `GET /admin/servers` carries the same `allow_admin_bypass=False` yet is also open — both are open, confirming there is no read/write asymmetry.) You can register via the browser Admin UI or via raw `curl` with just a JSON body — either works.
7. **launchd keeps the gateway running with `--reload`** watching `mcpgateway/` (`~/Library/LaunchAgents/com.chadkuisel.mcp-gateway.plist`). Registering gateways/servers is a DB write via the admin API, not a code change, so it won't trigger a reload — but this confirms nothing here needs a service restart.

---

## Staged Steps

### Stage 0 — Smoke test the Gateway-peer path on the simplest possible candidate

Pick `descrybe` first: it's a plain HTTPS URL, no auth, no local process to manage. This isolates "does registering a remote HTTP MCP server as a Gateway peer work at all" before adding the stdio-bridge variable.

1. **Action (Admin UI):** Log into `http://127.0.0.1:4444/admin` → **Gateways** panel → "Add New MCP Server or Gateway" → Name: `descrybe`, URL: `https://mcp.descrybe.com/mcp`, Transport: try `STREAMABLEHTTP` first (most hosted MCP servers today use it; **ASSUMPTION — verify**, ` descrybe`'s actual transport was not confirmed against its server — SSE is the fallback if StreamableHTTP fails).
   - Equivalent API call: `POST http://127.0.0.1:4444/admin/gateways` (no auth needed on this instance per Prereq 6), JSON body `{"name": "descrybe", "url": "https://mcp.descrybe.com/mcp", "transport": "STREAMABLEHTTP"}`.
2. **Expected result:** Gateway responds 200/201, the UI's "Test connection" button (admin.html line 5216) succeeds, and "Fetch Tools from MCP Server" (line 5233) populates a non-empty tool list.
3. **Verify:** `GET /admin/gateways` shows the new row with `enabled: true` and a reachable status; the tools it exposed appear in `GET /admin/servers/{id}` once added to a Virtual Server (Stage 2).

### Stage 1 — Bridge one stdio server through `mcpgateway.translate`

Pick `sequential-thinking`: no auth, well-known package, already proven to work as a direct stdio server in this session.

1. **Action:** Run the bridge as a background process (not yet a launchd service — validate manually first):
   ```
   cd /Users/chadkuisel/Workspace/mcp-context-forge
   .venv/bin/python -m mcpgateway.translate \
     --stdio "npx -y @modelcontextprotocol/server-sequential-thinking" \
     --expose-streamable-http \
     --port 9101 \
     --host 127.0.0.1
   ```
2. **Expected result:** Process logs a listening message on `127.0.0.1:9101`. **CORRECTED — do NOT verify with a bare `curl … tools/list`:** in the default `--expose-streamable-http` mode the endpoint is stateful (`translate.py:1390` `json_response=False`), so a plain POST without an `initialize` handshake, an `Accept: application/json, text/event-stream` header, and an `mcp-session-id` returns **406 / "not initialized"** — a working bridge would look broken. Two valid ways to verify instead: **(a) easiest** — skip the manual curl and just do step 3 (register in the Admin UI) then click **"Test connection" + "Fetch Tools"**; the gateway performs the MCP handshake for you and a non-empty tool list = success. **(b) manual** — relaunch the bridge with `--stateless --jsonResponse` added, then a single-shot `curl` works but must still send `initialize` first with the dual `Accept` header (see `translate.py:86` example).
3. **Register with the gateway:** Same as Stage 0, Admin UI → Gateways → Name: `sequential-thinking-bridge`, URL: `http://127.0.0.1:9101/mcp`, Transport: `STREAMABLEHTTP`.
4. **Verify:** "Fetch Tools" in the admin UI returns the sequential-thinking tool(s); calling the tool through the gateway's Virtual Server (Stage 2) returns the same result as calling it directly today.
5. **Note:** this bridge process must stay running for the gateway peer to stay reachable — it is a second long-lived process per stdio server wrapped, separate from the gateway itself. Not yet wired into launchd; that's a follow-on decision once Chad confirms he wants this server wrapped permanently.

### Stage 2 — Compose a Virtual Server from the wrapped tools

1. **Action (Admin UI):** Virtual Servers panel → "Create Virtual Server" → select the tools fetched from the `descrybe` and `sequential-thinking-bridge` gateways (and any others already registered) → save. This mints a new server id and an API key.
2. **Expected result:** New row in `servers` table; `GET /admin/servers/{new_id}` lists `associatedTools` matching what was selected.
3. **Verify:** Point one throwaway client config (or `mcpgateway.wrapper`, per `docs/docs/using/mcpgateway-wrapper.md`, env `MCP_SERVER_URL=http://127.0.0.1:4444/servers/{new_id}/mcp`) at it and confirm a tool call round-trips correctly.

### Stage 3 — Repeat for the remaining 12 servers, in two batches

- **Batch A (remote HTTP, likely low friction):** `com.supabase/mcp`, `midpage`, `midpage-legal-research`, `github-mcp-server`, `courtlistener` (needs `auth_type: bearer`/`authheaders` with the `COURTLISTENER_API_TOKEN` — resolve the config ambiguity noted in Current State first).
- **Batch B (stdio, needs a `translate` bridge each):** `chrome-devtools`, `context7` (resolve the `${input:CONTEXT7_API_KEY}` placeholder into a real static value first — `translate.py` has no interactive-input mechanism), `markitdown`, `playwright`, `frontend-visualqa`. Leave `kraang` and `GitKraken` out of this pass (see Open Questions).

Each follows the same action → expected result → verify shape as Stages 0–2. Not spelled out per-server here to avoid drafting 12 near-duplicate blocks before Stage 0–2 are proven out on this machine.

---

## Rollback

- **Stage 0/3-BatchA (Gateway peer registration):** Admin UI → Gateways → select row → Delete (`POST /admin/gateways/{gateway_id}/delete`, confirmed to exist in `admin.py:12990`). Removes the peer and its cached tools; does not touch the upstream server itself.
- **Stage 1/3-BatchB (translate bridge process):** Kill the `mcpgateway.translate` process (`Ctrl-C` or `kill <pid>`); then delete the corresponding Gateway row as above. No persistent state is created by the bridge itself (it's a stateless proxy process).
- **Stage 2 (Virtual Server composition):** Admin UI → Virtual Servers → select → Delete. The delete route is `/admin/servers/{server_id}/delete` (`admin.py:3348`). (Note: `/admin/servers/{id}/state` and `/admin/gateways/{id}/state` are enable/disable *toggles*, not deletes — use the `/delete` routes to fully remove.)
- **Full rollback:** All of the above are DB deletes via authenticated admin routes — no migrations run, no launchd changes, nothing to undo at the OS level. `mcp.db` itself is untouched by this plan (read-only queries only were used to reach the "Current State" above).

---

## Open Questions / Risks / Unverified Assumptions

1. **RESOLVED (was "auth-to-write untested").** A live unauthenticated `POST /admin/gateways` returned 422 (body validation), proving writes succeed with no login/session/CSRF on this instance — see corrected Prereq 6. The startup "CSRF protection middleware enabled" log line does not gate these admin write routes here (the unauthenticated-admin identity passes). No phantom login step needed.
2. **ASSUMPTION — hosted MCP servers' transport (SSE vs. StreamableHTTP) is unconfirmed per-server.** I did not probe `mcp.supabase.com`, `mcp.descrybe.com`, `app.midpage.ai`, `api.githubcopilot.com`, or `mcp.courtlistener.com` directly (would require an outbound network call, which felt out of scope for a read-only local-config analysis, and some need bearer tokens I shouldn't exercise from a planning task). Stage 0 explicitly plans to fail fast on this by trying StreamableHTTP first.
3. **`kraang`'s `KRAANG_DB_PATH` is still the literal placeholder value** (`/YOUR/CUSTOM/PATH/.kraang/kraang.db`) in every client config that has it. This server is likely non-functional today regardless of gateway wrapping — flagged as a candidate to drop or fix upstream before spending effort wrapping it.
4. **`courtlistener`'s client config is ambiguous** — it carries both a `url` (remote HTTPS) and a `command`/`args` (implying a local CLI binary `courtlistener-mcp`), plus a `REDIS_URL` pointing at a local Redis instance. I did not determine which of these is actually load-bearing for the MCP client, or whether `courtlistener-mcp` is a local stdio wrapper around the remote API (in which case it belongs in Batch B, not Batch A). Verify by checking which key each client actually reads (Cursor vs. Codex may resolve this differently) before registering.
5. **`context7`'s API key is an interactive placeholder** (`${input:CONTEXT7_API_KEY}`), which only Cursor/Claude Desktop-style clients resolve via a prompt. `mcpgateway.translate` has no such interactive-input feature — the real key value must be sourced (e.g., from wherever Chad currently stores it) and passed as a static env var to the bridge command.
6. **GitKraken's args are Cursor/GitLens-specific** (`--host=cursor --scheme=cursor`), so proxying it through a generic bridge may produce a server that behaves differently than the one currently wired into Cursor/Claude Code. Low priority; likely not worth wrapping versus leaving it client-native.
7. **Long-lived bridge processes are a new operational surface.** Every stdio server that gets wrapped needs its own always-on `mcpgateway.translate` process (5 in Batch B). None of this plan wires those into launchd yet — that's a deliberate deferral pending Chad confirming which servers he actually wants permanently wrapped, since each one is one more thing that can silently die and break the corresponding Gateway peer.
8. **Scope boundary respected:** this plan stops at "servers registered in the gateway." Repointing Chad's actual client configs (`~/.claude.json`, `~/.cursor/mcp.json`, `~/.codex/config.toml`) at the gateway's Virtual Server URL(s) instead of the original servers is intentionally NOT included — that's a follow-on decision once Stage 0–2 prove the mechanism works, and touches the `mcp-provision` skill's territory instead of this one.
