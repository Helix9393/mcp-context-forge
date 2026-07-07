# desktop-mcp-sync

Watches Chad's standalone Claude Desktop MCP config and keeps the local
`mcp-context-forge` gateway (`http://127.0.0.1:4444`) in sync with it.

## Precondition: this tool relies on the unauthenticated-loopback posture

Every gateway call is made with `MCPGATEWAY_BEARER_TOKEN` **unset**. On this
gateway's current config (`AUTH_REQUIRED=false`,
`ALLOW_UNAUTHENTICATED_ADMIN=true`, `HOST=127.0.0.1`), that authenticates as
`admin@example.com` for free — no token needed, and a *bad* token would
actually 401. This is fine as long as the gateway only ever binds to
loopback. **If the gateway is ever made network-bound (a non-loopback
`HOST`, a reverse proxy, container port-mapping to `0.0.0.0`, etc.), this
tool must be re-secured first** (real bearer token, `MCPGATEWAY_BEARER_TOKEN`
set in the launchd plist's environment) — otherwise every API call this
script makes, including destructive ones (`DELETE /gateways/{id}`, the hub's
full-replace `PUT /servers/{id}`), becomes reachable to anything that can
hit that host.

## What it does

Every run does three passes:

1. **Config-sync pass** — reads (never writes)
   `~/Library/Application Support/Claude/claude_desktop_config.json`.
   - `command`-based (stdio) servers are logged and skipped — stdio can't be
     federated.
   - Servers matching `DENYLIST` (currently `supabase`, `github` — matched
     against both the Desktop config key and the URL host) are skipped
     entirely: never registered, never federated into the hub. Chad keeps
     using those directly, in-process, outside the gateway.
   - `url`-based (remote: http/SSE/streamable-http) servers not already
     federated get registered. Dedupe is by **normalized URL** (not name) —
     checked against local state first, then against `GET /gateways` on the
     gateway. If a Desktop entry's URL matches a gateway that already exists
     (e.g. registered by hand, or under a different display name), it is
     **adopted**: the real `gateway_id` and `auth_type` are looked up and
     backfilled into local state (the real `auth_type`, since `GatewayRead`
     redacts that field on read, is read from the DB instead) rather than
     stored as `"unknown"`. New servers get:
     - probed to determine no-auth vs OAuth (RFC 8414/9728-compliant
       well-known URL discovery — the well-known path segment is inserted
       between host and path per spec, and a `resource_metadata=` pointer in
       a `WWW-Authenticate` header is honored if the server provides one),
     - registered on the gateway (`POST /gateways`),
     - if OAuth: the gateway DCR-preps the client, and Chad gets a macOS
       notification with the authorize URL
       (`http://127.0.0.1:4444/oauth/authorize/{gateway_id}`) to open and log
       in manually. **This script never opens the browser for you.** The
       authorize URL is also appended to `pending-oauth.txt` so it isn't
       lost.
   - Tool attachment to the hub is **not** done here — see pass 2.

2. **Reconcile-hub pass** — runs every invocation, independent of what pass 1
   did (and independent of `state.json` entirely). Recomputes the hub's
   **complete** `associated_tools` set from scratch: the union of
   `tools.gateway_id` for every gateway in `GET /gateways` whose name/URL
   host is not denylisted, read from the local DB
   (`~/Workspace/mcp-context-forge/mcp.db`, read-only). This is what
   auto-attaches an OAuth server's tools once Chad completes login (no extra
   trigger needed), self-heals any `state.json` loss, and prunes tools from
   removed/denylisted gateways. The hub is updated via a single **flat** PUT
   body (`{"associated_tools": [...], "visibility": "public"}`) — a nested
   `{"server": {...}}` body is silently accepted-and-ignored by the update
   route (`extra="ignore"`), which is a 200-status no-op that looks like
   success but changes nothing. If the complete desired set can't be computed
   for any reason (gateway list fetch fails, DB read fails), the pass aborts
   and logs rather than sending a partial list — the hub's associations are
   full-replace on the gateway side, so a partial PUT would delete every
   other server's tools.

3. **Token-health pass** — for every federated OAuth server that has a stored
   `oauth_tokens` row (no row = pending first login, not a failure — skipped),
   asks the gateway (`GET /gateways/{id}`) whether it reports `reachable`.
   See "How the health signal actually works" below for what this means and
   why it isn't what the original design doc assumed. On an unreachable
   gateway, Chad gets a `re-auth needed: <name> -> <authorize URL>`
   notification (the URL is included, and also appended to
   `pending-oauth.txt`), debounced to at most once per ~12h per server.

Every action is logged with an ISO-8601 UTC timestamp.

## How the health signal actually works (read before touching `probe_token_health`)

The original design for this pass was: call `POST /gateways/{id}/tools/refresh`
and inspect its structured `{success, error, validation_errors}` response,
since that route forces the gateway to actually use/refresh the stored OAuth
token. **That was tested live and found to be a dead signal for this tool's
gateways.** Every gateway this script registers uses
`oauth_config.grant_type = "authorization_code"`, and for that grant type the
gateway's own `_initialize_gateway()` unconditionally early-returns
`success=True` with zero tools/resources/prompts unless a flag
(`oauth_auto_fetch_tool_flag=True`) is passed — which the manual
`/tools/refresh` route never does. Confirmed against the live gateway: the
refresh endpoint returned an **identical**
`{"success":true,"error":null,"toolsAdded":0}` response, in under 1.1ms (i.e.
no network call happened at all), both before and after deliberately
corrupting the stored `refresh_token` in the DB. So this tool does not use
`/tools/refresh` as the health discriminator.

Instead, `probe_token_health()` reads `GET /gateways/{id}` → `reachable`
(this field, along with `last_error`, is **not** redacted on read — only
`auth_type`/`oauth_config`/`associated_tools` are). The gateway's own internal
periodic health-check loop (`health_check_interval` = 60s,
`unhealthy_threshold` = 3 consecutive failures by default) *does* exercise
the real token-refresh path for `authorization_code` gateways, and flips
`reachable` to `false` on repeated failure. That same failure path never
populates `last_error` for an already-registered, already-active gateway
(the only `last_error` writes in the gateway's health/refresh code apply to
the *initial pending-registration* retry lifecycle, not ongoing health
checks) — so, unlike the original design, this tool does **not** require a
`last_error` auth marker match; `reachable=false` alone (for a gateway that
already has a stored token) is treated as needing attention.
`AUTH_FAILURE_MARKERS` text matching is kept only as a secondary refinement,
in case `last_error` ever is populated by some other path: an unreachable
gateway whose `last_error` is clearly *not* auth-shaped (e.g. a plain network
timeout string) is downgraded to "ambiguous" rather than nagging.

**Known limitation:** this signal depends on the gateway's own internal
health-check loop having actually run and observed a failure since the token
broke, so it can lag by up to `health_check_interval * unhealthy_threshold`.
Treat a resulting "ok" as "no known problem" rather than "just confirmed
this instant." The `broken-token → reachable=false` path itself was traced
end-to-end in the gateway code (2026-07-07): a past `expires_at` forces the
refresh branch, a failed refresh raises `OAuthError`, and
`_handle_gateway_failure` flips `reachable=false` after
`unhealthy_threshold` consecutive failures. The loop was confirmed running
live (~60s cadence) and re-reads the token fresh from the DB each cycle (no
in-memory cache), so the path is code-verified; only the live firing latency
is left to the inherent lag above.

**Do not test this by naively corrupting the DB token.** Writing a non-`v2:`
value into `refresh_token` is treated as plaintext, triggers a real refresh
POST to the provider, and on the `invalid` error the gateway's *own* code
**deletes the token row** (`token_storage_service.py:420-427`) — destroying
the live credential. To break a token for testing, tamper only the inner
ciphertext of the `v2:{...}` JSON (decrypt → `None`, no network, no delete)
and restore with `UPDATE ... WHERE id`, not `INSERT`.

## Requirements

- Python 3 stdlib only (`urllib`, `json`, `sqlite3`, `subprocess`, `argparse`,
  `fcntl`, `re`) — no pip installs.
- The gateway must be reachable at `http://127.0.0.1:4444` with
  `MCPGATEWAY_BEARER_TOKEN` **unset** in the environment (loopback admin —
  a bad token 401s, no token authenticates as admin). See the precondition
  note above.
- Reads `~/Workspace/mcp-context-forge/mcp.db` read-only (via
  `file:...?mode=ro`) to get ground truth for tool IDs, the real `auth_type`
  of an adopted gateway, and whether an `oauth_tokens` row exists — all of
  which the API either redacts or can't answer directly.
- A single-process concurrency lock (`sync.lock`, `fcntl.flock`,
  non-blocking) ensures overlapping invocations (WatchPaths trigger +
  StartInterval timer + RunAtLoad, all potentially firing close together)
  never run at the same time — the second invocation logs and exits `0`
  immediately rather than racing the first (important since the hub's
  reconcile PUT is full-replace).

## Usage

```bash
# Dry run: classify + log intended actions only. Makes ZERO gateway writes
# and does not touch state.json.
python3 sync.py --dry-run

# Live run: does the real config-sync + reconcile-hub + token-health passes.
python3 sync.py
```

Live registration is idempotent — a server already in `state.json` or
already present on the gateway (matched by **normalized URL**, not name) is
never re-registered, and denylisted servers are never registered at all, so
running this repeatedly (or having launchd trigger it on every config
change) can never double-create a gateway entry. A concurrency lock also
means overlapping runs serialize rather than race.

## Where state lives

| What | Path |
|---|---|
| Federated-server tracking + notification debounce timestamps | `~/Library/Application Support/desktop-mcp-sync/state.json` |
| Pending OAuth authorize URLs (append-only) | `~/Library/Application Support/desktop-mcp-sync/pending-oauth.txt` |
| stdout/stderr log (launchd redirects here) | `~/Library/Logs/desktop-mcp-sync.log` |

`state.json` shape:

```json
{
  "federated": {
    "<server-name>": {
      "url": "...",
      "auth_type": "no-auth | oauth | unknown",
      "gateway_id": "...",
      "registered_at": "2026-07-07T02:00:00+00:00"
    }
  },
  "last_notified": {
    "<server-name>": 1751850000.0
  }
}
```

To read the log tail:

```bash
tail -50 ~/Library/Logs/desktop-mcp-sync.log
```

To see what's still waiting on manual OAuth login:

```bash
cat "~/Library/Application Support/desktop-mcp-sync/pending-oauth.txt"
```

To reset all tracking (e.g. after manually cleaning up gateway entries) and
force a full re-scan next run, delete `state.json` — the script recreates it
on the next live run. This does **not** un-register anything already on the
gateway; re-registration attempts for URLs that already exist there are
still skipped via the `GET /gateways` dedupe check (and adopted into state
instead, per F6 above).

## Install

The launchd agent watches the Claude Desktop config file for changes and
also runs on a 30-minute timer (for the token-health sweep, and as a
belt-and-suspenders re-check of the config).

```bash
plutil -lint ~/Library/LaunchAgents/com.chadkuisel.desktop-mcp-sync.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chadkuisel.desktop-mcp-sync.plist
launchctl print gui/$(id -u)/com.chadkuisel.desktop-mcp-sync | head
```

`RunAtLoad` means it fires immediately on bootstrap — safe, since the
worst case is a config-sync pass over whatever's currently in the Desktop
config (idempotent), a reconcile-hub pass that recomputes and re-PUTs the
same tool set if nothing changed (also idempotent — it's a full recompute
every time, not an incremental attach), and a token-health pass over
whatever's in `state.json` (empty until something has actually been
federated through this tool's config-sync pass).

## Uninstall

```bash
launchctl bootout gui/$(id -u)/com.chadkuisel.desktop-mcp-sync
rm ~/Library/LaunchAgents/com.chadkuisel.desktop-mcp-sync.plist
```

This does not remove `state.json`, `pending-oauth.txt`, the log file, or
anything already registered on the gateway — remove those manually if
desired.
