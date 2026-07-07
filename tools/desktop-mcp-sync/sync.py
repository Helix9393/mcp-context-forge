#!/usr/bin/env python3
"""desktop-mcp-sync

Watches Chad's standalone Claude Desktop MCP config
(~/Library/Application Support/Claude/claude_desktop_config.json). When a
REMOTE (http/SSE) MCP server shows up there, this script auto-registers it
on the local mcp-context-forge gateway (http://127.0.0.1:4444) and reconciles
the hub virtual server's tool set. stdio servers are skipped (log only).
Denylisted servers (see DENYLIST below) are skipped entirely -- never
registered, never federated into the hub.

OAuth-protected remote servers can't be fully automated (Chad has to log in
via browser), so for those we register + let the gateway DCR-prep, then send
a macOS notification with the authorize URL for manual login. We never
auto-open the browser.

Every run does THREE passes:
  1. config-sync   -- register any new remote server found in the Desktop config.
  2. reconcile-hub -- recompute the hub's COMPLETE associated_tools set from
     scratch (union of every non-denylisted federated gateway's tools) and
     PUT it. This runs every invocation regardless of what pass 1 did, so it
     self-heals state loss, attaches OAuth tools once Chad logs in, and prunes
     removed gateways. See reconcile_hub() for why this replaced the old
     one-shot "attach on registration" approach.
  3. token-health  -- for every federated OAuth server with a stored token,
     asks the gateway whether it's reachable and notifies Chad (with the
     login link) if not. See probe_token_health() for what this signal
     actually means and why -- it was empirically validated against the live
     gateway, and the mechanism described in the original design doc
     (POST .../tools/refresh) turned out to be a dead no-op for this tool's
     gateways. Read that docstring before changing this.

Runs stdlib-only (urllib, json, sqlite3, subprocess, fcntl). No third-party
deps.

Usage:
    python3 sync.py             # normal (live) run: all three passes
    python3 sync.py --dry-run   # classify + log intended actions; ZERO gateway writes
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants / paths
# ---------------------------------------------------------------------------

GATEWAY_BASE = "http://127.0.0.1:4444"
HUB_SERVER_ID = "2bdbd4fbb65f4bdf9c072ea2db37d9eb"
REDIRECT_URI = "http://127.0.0.1:4444/oauth/callback"

CLAUDE_DESKTOP_CONFIG = os.path.expanduser(
    "~/Library/Application Support/Claude/claude_desktop_config.json"
)
STATE_DIR = os.path.expanduser("~/Library/Application Support/desktop-mcp-sync")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
PENDING_OAUTH_FILE = os.path.join(STATE_DIR, "pending-oauth.txt")
LOCK_FILE = os.path.join(STATE_DIR, "sync.lock")

DB_PATH = os.path.expanduser("~/Workspace/mcp-context-forge/mcp.db")

DEBOUNCE_SECONDS = 12 * 60 * 60  # 12h: don't re-notify the same server more often than this
HTTP_TIMEOUT = 10

# Servers Chad has decided should never be federated into the (unauthenticated,
# loopback-only) hub -- he keeps using these directly in-process instead.
# Matched against BOTH the Desktop config key (server name) and the URL host,
# so a rename or a slightly different display name can't slip past it.
DENYLIST = {"supabase", "github"}

# Strings that, when found in a gateway's last_error, indicate an
# unambiguous auth failure rather than a transient network blip. Mirrors the
# same convention mcp-context-forge itself uses internally (see
# mcpgateway/services/gateway_service.py: checks for "401"/"403"/
# "unauthorized"/"forbidden" in the error string to detect auth rejections).
# Kept as a secondary signal in probe_token_health() -- see that docstring
# for why it is NOT the primary discriminator.
AUTH_FAILURE_MARKERS = ("401", "403", "unauthorized", "forbidden", "invalid_grant", "invalid_token")

_RESOURCE_METADATA_RE = re.compile(r'resource_metadata="([^"]+)"')


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", flush=True)


def _osascript_escape(s: str) -> str:
    """Escape a string for embedding in an AppleScript double-quoted literal.

    Order matters: backslashes must be doubled BEFORE quotes are escaped,
    otherwise an escaped quote's backslash would itself get re-escaped.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def notify(message: str, title: str = "MCP sync") -> None:
    """Fire a macOS notification. Never auto-opens a browser."""
    safe_message = _osascript_escape(message)
    safe_title = _osascript_escape(title)
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{safe_message}" with title "{safe_title}"'],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 - notification failures must never crash the sweep
        log(f"notify() failed (non-fatal): {exc}")


def ensure_state_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def is_denylisted(name: str, url: str) -> bool:
    lname = (name or "").lower()
    host = urllib.parse.urlparse(url or "").netloc.lower()
    for term in DENYLIST:
        if term in lname or term in host:
            return True
    return False


# ---------------------------------------------------------------------------
# Concurrency lock (F7)
# ---------------------------------------------------------------------------

_lock_fh = None  # module-level handle so it stays open (and thus held) for the process lifetime


def acquire_lock() -> bool:
    """Acquire an exclusive, non-blocking lock on LOCK_FILE.

    Returns True if the lock was acquired, False if another run already
    holds it (caller should exit cleanly, not treat this as an error).
    """
    global _lock_fh
    ensure_state_dir()
    _lock_fh = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _lock_fh.close()
        _lock_fh = None
        return False
    return True


def release_lock() -> None:
    global _lock_fh
    if _lock_fh is None:
        return
    try:
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    _lock_fh.close()
    _lock_fh = None


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"federated": {}, "last_notified": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("federated", {})
        data.setdefault("last_notified", {})
        return data
    except Exception as exc:  # noqa: BLE001
        log(f"state file unreadable/corrupt ({exc}); starting from empty state")
        return {"federated": {}, "last_notified": {}}


def save_state(state: dict) -> None:
    ensure_state_dir()
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# Claude Desktop config (READ-ONLY -- never written by this script)
# ---------------------------------------------------------------------------


def load_desktop_servers() -> dict:
    if not os.path.exists(CLAUDE_DESKTOP_CONFIG):
        log(f"Claude Desktop config not found at {CLAUDE_DESKTOP_CONFIG}; skipping config-sync pass")
        return {}
    try:
        with open(CLAUDE_DESKTOP_CONFIG, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        log(f"failed to read/parse Claude Desktop config ({exc}); skipping config-sync pass")
        return {}
    return cfg.get("mcpServers", {}) or {}


def classify(entry: dict) -> str:
    """Classify a Claude Desktop MCP server entry as remote / stdio / unknown."""
    if isinstance(entry, dict):
        if "url" in entry or entry.get("type") in ("http", "sse", "remote"):
            return "remote"
        if "command" in entry:
            return "stdio"
    return "unknown"


# ---------------------------------------------------------------------------
# Gateway HTTP helpers
# ---------------------------------------------------------------------------


def gateway_request(method: str, path: str, body: dict | None = None, timeout: int = HTTP_TIMEOUT):
    """
    Call the local gateway. Per verified gateway facts, calls must be made
    with MCPGATEWAY_BEARER_TOKEN unset in the environment (loopback admin;
    a *bad* token would 401, having none authenticates as admin). We simply
    never set that env var here.
    """
    url = f"{GATEWAY_BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            parsed = json.loads(raw) if raw else None
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            parsed = None
        return exc.code, parsed
    except urllib.error.URLError as exc:
        return None, {"error": str(exc)}


def http_get_json(url: str, headers: dict | None = None, timeout: int = HTTP_TIMEOUT):
    req = urllib.request.Request(url, method="GET", headers=headers or {"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except urllib.error.URLError as exc:
        log(f"GET {url} failed (network): {exc}")
        return None, None


def get_existing_gateways() -> list:
    """List every gateway currently registered on the gateway (full records)."""
    status, data = gateway_request("GET", "/gateways")
    if status != 200 or not isinstance(data, list):
        log(f"warning: could not list existing gateways (status={status}); assuming none for dedupe purposes")
        return []
    return [gw for gw in data if isinstance(gw, dict)]


def get_gateway_auth_type_from_db(gateway_id: str) -> str:
    """auth_type is redacted (always None) on GatewayRead per verified gateway
    facts, so when we need the REAL value (e.g. backfilling state for an
    adopted gateway) we read the DB directly, same pattern as tool ids."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT auth_type FROM gateways WHERE id = ?", (gateway_id,))
            row = cur.fetchone()
            return row[0] if row and row[0] else "no-auth"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log(f"failed to query auth_type for gateway_id={gateway_id}: {exc}")
        return "unknown"


def gateway_has_oauth_token(gateway_id: str) -> bool:
    """True if an oauth_tokens row exists for this gateway (i.e. Chad has
    completed the login at least once) -- no row means "pending first login",
    which is not a health failure and should not be probed/notified about."""
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM oauth_tokens WHERE gateway_id = ? LIMIT 1", (gateway_id,))
            return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log(f"failed to check oauth_tokens for gateway_id={gateway_id}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Auth discovery / probing
# ---------------------------------------------------------------------------


def _well_known_url(base_url: str, well_known_name: str) -> str:
    """Build an RFC 8414 / RFC 9728 compliant well-known URL.

    Per both RFCs, the well-known path segment is inserted BETWEEN the
    host and the original path -- it is NOT simply appended after it. The
    gateway's own dcr_service does exactly this (dcr_service.py:102-104);
    the old version of this function appended after the path instead, which
    only happened to work for bare-root URLs.
    """
    parsed = urllib.parse.urlparse(base_url.rstrip("/"))
    return f"{parsed.scheme}://{parsed.netloc}/.well-known/{well_known_name}{parsed.path}"


def _extract_resource_metadata_url(www_authenticate: str) -> str | None:
    """Pull resource_metadata="..." out of a WWW-Authenticate header value, if present."""
    match = _RESOURCE_METADATA_RE.search(www_authenticate or "")
    return match.group(1) if match else None


def discover_oauth(url: str, resource_metadata_url: str | None = None) -> dict | None:
    """
    Per verified gateway facts:
      GET <url>/.well-known/oauth-protected-resource -> authorization_servers[0] = issuer, scopes_supported
      GET <issuer>/.well-known/oauth-authorization-server -> authorization_endpoint, token_endpoint,
          registration_endpoint, scopes_supported

    If the 401 response's WWW-Authenticate header already carried a
    resource_metadata="..." pointer, the caller passes it as
    resource_metadata_url and we fetch THAT instead of constructing one
    (the server told us exactly where to look; constructing our own could
    disagree with it).
    """
    prm_url = resource_metadata_url or _well_known_url(url, "oauth-protected-resource")
    prm_status, prm_data = http_get_json(prm_url)
    if prm_status != 200 or not prm_data:
        log(f"oauth-protected-resource discovery failed for {url} (status={prm_status}, url={prm_url})")
        return None
    authorization_servers = prm_data.get("authorization_servers") or []
    if not authorization_servers:
        log(f"no authorization_servers in protected-resource metadata for {url}")
        return None
    issuer = authorization_servers[0]
    prm_scopes = prm_data.get("scopes_supported", [])

    as_url = _well_known_url(issuer, "oauth-authorization-server")
    as_status, as_data = http_get_json(as_url)
    if as_status != 200 or not as_data:
        log(f"oauth-authorization-server discovery failed for issuer {issuer} (status={as_status}, url={as_url})")
        return None

    return {
        "issuer": issuer,
        "authorization_url": as_data.get("authorization_endpoint"),
        "token_url": as_data.get("token_endpoint"),
        "registration_endpoint": as_data.get("registration_endpoint"),
        "scopes": as_data.get("scopes_supported") or prm_scopes,
    }


def probe_remote_auth(url: str):
    """
    Unauth probe: POST an MCP `initialize` with
    Accept: application/json, text/event-stream.
      200            -> no-auth
      401 + WWW-Authenticate: Bearer -> oauth (then run discovery)
      anything else  -> ambiguous; caller should skip this run and retry later
    Returns (kind, discovery_or_None) where kind is "no-auth", "oauth", or None.
    """
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "desktop-mcp-sync", "version": "1.0"},
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(init_body).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            if resp.status == 200:
                return "no-auth", None
            log(f"probe for {url} returned unexpected status {resp.status}; ambiguous, skipping this run")
            return None, None
    except urllib.error.HTTPError as exc:
        www_auth = exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
        if exc.code == 401 and "bearer" in www_auth.lower():
            resource_metadata_url = _extract_resource_metadata_url(www_auth)
            discovery = discover_oauth(url, resource_metadata_url)
            if discovery:
                return "oauth", discovery
            log(f"401+Bearer for {url} but OAuth discovery failed; ambiguous, skipping this run")
            return None, None
        log(f"probe for {url} returned HTTP {exc.code} (not a clean 200 or 401+Bearer); ambiguous, skipping this run")
        return None, None
    except urllib.error.URLError as exc:
        log(f"probe for {url} failed (network): {exc}; ambiguous, skipping this run")
        return None, None


# ---------------------------------------------------------------------------
# Gateway registration
# ---------------------------------------------------------------------------


def authorize_url_for(gateway_id: str) -> str:
    return f"{GATEWAY_BASE}/oauth/authorize/{gateway_id}"


def register_gateway(name: str, url: str, oauth_config: dict | None, dry_run: bool):
    body = {"name": name, "url": url, "transport": "STREAMABLEHTTP"}
    if oauth_config:
        body["auth_type"] = "oauth"
        body["oauth_config"] = oauth_config
    if dry_run:
        log(f"[dry-run] would POST /gateways: {json.dumps(body)}")
        return True, {"id": None}
    status, data = gateway_request("POST", "/gateways", body=body)
    if status in (200, 201) and isinstance(data, dict):
        log(f"registered gateway '{name}' (id={data.get('id')})")
        return True, data
    log(f"failed to register gateway '{name}': status={status} data={data}")
    return False, data


# ---------------------------------------------------------------------------
# Hub reconciliation (F1 + F2 + F3)
# ---------------------------------------------------------------------------


def reconcile_hub(dry_run: bool) -> None:
    """
    Recompute the hub's COMPLETE associated_tools set from scratch, every
    invocation, independent of whatever config_sync_pass did this run.

    Why this exists (replaces the old one-shot `attach_tools_to_hub`, which
    was only ever called from the no-auth registration branch): OAuth
    gateways register + notify but their tools were never attached even
    after Chad logged in, and a re-run's `if name in state["federated"]:
    continue` meant they never would be. Desired set = union of
    `tools.gateway_id` for every gateway in GET /gateways whose name/url-host
    is NOT denylisted -- this is the source of truth for "what's federated"
    regardless of how/when it got there, so it auto-attaches OAuth tools
    post-login, self-heals any state.json loss, and prunes tools from
    removed/denylisted gateways.

    PUT body is FLAT (`{"associated_tools": [...], "visibility": "public"}`),
    NOT nested under a "server" key -- the update route takes a single
    ServerUpdate body param with extra="ignore", so a nested {"server": {...}}
    is silently dropped (200, no-op). Confirmed against source
    (mcpgateway/main.py update_server route; mcpgateway/utils/base_models.py).

    NEVER sends a partial list: `_update_server_associations` on the gateway
    side is full-replace (clears then repopulates), so if we can't compute
    the COMPLETE desired set for any reason -- GET /gateways fails, or the DB
    query throws -- we abort the PUT entirely rather than risk wiping every
    other server's tools from the hub.
    """
    status, gateways = gateway_request("GET", "/gateways")
    if status != 200 or not isinstance(gateways, list):
        log(f"reconcile_hub: could not list gateways (status={status}); ABORTING reconcile (refusing a partial PUT)")
        return

    eligible_gateway_ids = []
    for gw in gateways:
        if not isinstance(gw, dict):
            continue
        gw_id = gw.get("id")
        gw_name = gw.get("name") or ""
        gw_url = gw.get("url") or ""
        if not gw_id:
            continue
        if is_denylisted(gw_name, gw_url):
            log(f"reconcile_hub: excluding denylisted gateway '{gw_name}' from hub")
            continue
        eligible_gateway_ids.append(gw_id)

    desired_tool_ids: set = set()
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            if eligible_gateway_ids:
                placeholders = ",".join("?" for _ in eligible_gateway_ids)
                cur.execute(f"SELECT id FROM tools WHERE gateway_id IN ({placeholders})", eligible_gateway_ids)
                desired_tool_ids = {row[0] for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log(f"reconcile_hub: failed to compute desired tool set from DB ({exc}); ABORTING reconcile (refusing a partial PUT)")
        return

    sorted_ids = sorted(desired_tool_ids)

    if dry_run:
        log(f"[dry-run] would PUT /servers/{HUB_SERVER_ID} (flat body) with {len(sorted_ids)} tool(s) from {len(eligible_gateway_ids)} eligible gateway(s)")
        return

    body = {"associated_tools": sorted_ids, "visibility": "public"}
    status, data = gateway_request("PUT", f"/servers/{HUB_SERVER_ID}", body=body)
    if status == 200:
        log(f"reconcile_hub: hub now has {len(sorted_ids)} associated tool(s) (flat PUT) from {len(eligible_gateway_ids)} eligible gateway(s)")
    else:
        log(f"reconcile_hub: failed to PUT hub associated_tools: status={status} data={data}")


# ---------------------------------------------------------------------------
# Pass 1: config sync
# ---------------------------------------------------------------------------


def config_sync_pass(state: dict, dry_run: bool) -> None:
    servers = load_desktop_servers()
    if not servers:
        log("config-sync: no mcpServers entries found")
        return

    existing_gateways = None  # lazily fetched, only if we find a remote candidate

    # Normalized URLs already tracked in local state -- used for URL-based
    # dedupe (F6) instead of the old name-based dedupe, since the gateway
    # itself identifies servers by normalized URL + creds, not display name.
    known_urls = {normalize_url(v.get("url", "")) for v in state["federated"].values() if v.get("url")}

    for name, entry in servers.items():
        kind = classify(entry)

        if kind == "stdio":
            log(f"skipped stdio: {name}")
            continue
        if kind == "unknown":
            log(f"skipped unknown entry (neither url nor command): {name}")
            continue

        # kind == "remote"
        url = entry.get("url")
        if not url:
            log(f"skipped remote entry with no url: {name}")
            continue

        if is_denylisted(name, url):
            log(f"skipped denylisted server: {name} ({url})")
            continue

        norm_url = normalize_url(url)

        if name in state["federated"]:
            log(f"already federated (state, by name): {name}")
            continue
        if norm_url in known_urls:
            log(f"already federated (state, by URL under a different name): {name} -> {url}")
            continue

        if dry_run:
            log(f"[dry-run] remote candidate found: {name} -- would check gateway dedupe, probe auth, and register if new")
            continue

        if existing_gateways is None:
            existing_gateways = get_existing_gateways()

        matched_gw = next((gw for gw in existing_gateways if normalize_url(gw.get("url", "")) == norm_url), None)
        if matched_gw:
            gw_id = matched_gw.get("id")
            real_auth_type = get_gateway_auth_type_from_db(gw_id) if gw_id else "unknown"
            log(f"already federated (gateway, by URL, not yet in local state): {name}; backfilling gateway_id={gw_id} auth_type={real_auth_type}")
            state["federated"][name] = {
                "url": url,
                "auth_type": real_auth_type,
                "gateway_id": gw_id,
                "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            known_urls.add(norm_url)
            continue

        log(f"discovering auth for remote server: {name} ({url})")
        auth_kind, discovery = probe_remote_auth(url)

        if auth_kind is None:
            log(f"could not classify auth for {name}; skipping this run, will retry next run")
            continue

        if auth_kind == "no-auth":
            ok, data = register_gateway(name, url, oauth_config=None, dry_run=dry_run)
            if not ok:
                continue
            gw_id = data.get("id") if isinstance(data, dict) else None
            state["federated"][name] = {
                "url": url,
                "auth_type": "no-auth",
                "gateway_id": gw_id,
                "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            known_urls.add(norm_url)
            # Tool attachment to the hub is handled by reconcile_hub(), which
            # runs once per invocation after this pass -- see F1.
            continue

        # auth_kind == "oauth"
        oauth_config = {
            "grant_type": "authorization_code",
            "issuer": discovery["issuer"],
            "authorization_url": discovery["authorization_url"],
            "token_url": discovery["token_url"],
            "scopes": discovery.get("scopes") or [],
            "redirect_uri": REDIRECT_URI,
        }
        ok, data = register_gateway(name, url, oauth_config=oauth_config, dry_run=dry_run)
        if not ok:
            continue
        gw_id = data.get("id") if isinstance(data, dict) else None
        state["federated"][name] = {
            "url": url,
            "auth_type": "oauth",
            "gateway_id": gw_id,
            "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        known_urls.add(norm_url)
        if gw_id:
            auth_url = authorize_url_for(gw_id)
            notify(f"{name} needs authorization -- open {auth_url}")
            ensure_state_dir()
            with open(PENDING_OAUTH_FILE, "a", encoding="utf-8") as fh:
                fh.write(f"{name} -> {auth_url}\n")
            log(f"OAuth server '{name}' registered; pending manual authorization at {auth_url}")
        else:
            log(f"OAuth server '{name}' registered but no gateway_id returned; cannot build authorize URL")


# ---------------------------------------------------------------------------
# Pass 2: token health sweep
# ---------------------------------------------------------------------------


def probe_token_health(gateway_id: str, name: str) -> str:
    """
    Returns one of: "ok", "auth-failed", "ambiguous".

    ****************************************************************
    EMPIRICALLY VALIDATED against the live gateway on 2026-07-07 --
    read this before changing the mechanism below.
    ****************************************************************

    The original design called for POST /gateways/{id}/tools/refresh as the
    health signal (it returns a structured {success, error,
    validation_errors} and, per mcpgateway/services/gateway_service.py,
    forces the gateway to actually use/refresh the stored token). That is
    true for `client_credentials`-grant OAuth gateways, but this tool always
    registers gateways with grant_type="authorization_code" (see
    register_gateway() above) -- and for THAT grant type,
    _initialize_gateway() unconditionally early-returns
    `{}, [], [], [], []` with success=True whenever
    oauth_auto_fetch_tool_flag is not explicitly True
    (gateway_service.py:4690-4701), which the manual /tools/refresh route
    never passes (gateway_service.py:5838-5851 calls _initialize_gateway
    without it). Confirmed live against courtlistener
    (gateway_id 6fd2933c91eb418881e8c02f8c852700): identical
    `{"success":true,"error":null,"toolsAdded":0}` response, duration
    <1.1ms (i.e. no network call happened), BOTH before and after
    deliberately corrupting the stored refresh_token in the DB and
    restoring it afterward. So /tools/refresh cannot detect an auth
    failure for this tool's gateways and is deliberately NOT used here.

    Instead we read `GET /gateways/{id}` `reachable`. Per verified gateway
    facts, `reachable`/`last_error` are NOT part of GatewayRead's redacted
    field set (only auth_type/oauth_config/associated_tools are redacted),
    so this is ground truth without needing sqlite3. The gateway's OWN
    internal periodic health-check loop
    (check_health_of_gateways/_check_single_gateway_health,
    health_check_interval=60s and unhealthy_threshold=3 consecutive
    failures by default) DOES exercise the real
    get_user_token()/_refresh_access_token() path for authorization_code
    gateways, and on failure eventually flips reachable=False via
    set_gateway_state(..., only_update_reachable=True) inside
    _handle_gateway_failure.

    That same failure path NEVER writes last_error for an
    already-active gateway (the only last_error write in gateway_service.py
    that could apply is in the PENDING-gateway registration-retry lifecycle,
    gateway_service.py:3909 -- not the ongoing health-check path), so we do
    NOT gate on a last_error marker match the way the original code did
    (that gate could structurally never pass, which was the original C2 bug).
    AUTH_FAILURE_MARKERS is kept only as a secondary, best-effort refinement:
    if last_error happens to be populated by some other code path AND it is
    clearly NOT auth-shaped (e.g. a DNS/network blip), we downgrade to
    "ambiguous" rather than nag; otherwise reachable=False is treated as
    auth-failed on its own.

    Inherent lag caveat: this signal depends on the gateway's own internal
    health-check loop having actually run and observed a failure since the
    token broke -- so it can lag by up to
    (health_check_interval * unhealthy_threshold). That lag is unavoidable;
    callers should treat "ok" as "no known problem", not as a live, instant
    health check.

    The broken-token -> reachable=false path was traced end-to-end in the
    gateway code on 2026-07-07 and holds: a past expires_at forces
    get_user_token() down its refresh branch
    (token_storage_service.py:221-229); a failing refresh raises OAuthError;
    _handle_gateway_failure -> set_gateway_state(only_update_reachable=True)
    flips reachable=false after unhealthy_threshold consecutive failures. The
    loop was confirmed running live (~60s cadence, lastSeen advancing), and
    the token is re-read fresh from the DB every cycle -- _get_gateways()
    re-queries (gateway_service.py:4778-4783) and get_user_token() opens a
    fresh session (gateway_service.py:4332) -- so there is no in-memory token
    cache that a real break could slip past.

    MAINTAINER WARNING -- do NOT stage a live token-break test by writing a
    plain garbage value into refresh_token. A non-"v2:" value is passed
    through as plaintext (encryption_service.py:330-332), the refresh then
    POSTs to the real provider, and on the "invalid" error the gateway's OWN
    code deletes the token row (token_storage_service.py:420-427) -- wiping
    the live credential before you can restore it. If you must break a token
    to test, tamper only the inner ciphertext of the existing v2:{...} JSON
    so decrypt returns None (local "No refresh token available", no network,
    no delete), and restore via UPDATE ... WHERE id (not INSERT -- the row
    still exists and would hit unique_gateway_user).
    """
    status, data = gateway_request("GET", f"/gateways/{gateway_id}")
    if status != 200 or not isinstance(data, dict):
        log(f"token-health: could not fetch gateway {gateway_id} ({name}) (status={status}); ambiguous, not notifying")
        return "ambiguous"

    reachable = data.get("reachable", True)
    if reachable:
        return "ok"

    last_error = (data.get("lastError") or data.get("last_error") or "").lower()
    if last_error and not any(marker in last_error for marker in AUTH_FAILURE_MARKERS):
        log(f"token-health: {name} unreachable but last_error not auth-shaped ('{last_error}'); ambiguous, not notifying")
        return "ambiguous"

    log(f"token-health: {name} ({gateway_id}) reported unreachable by the gateway's internal health-check loop")
    return "auth-failed"


def token_health_pass(state: dict, dry_run: bool) -> None:
    oauth_servers = {n: v for n, v in state["federated"].items() if v.get("auth_type") == "oauth"}
    if not oauth_servers:
        log("token-health: no federated OAuth servers to check")
        return

    now = time.time()
    for name, info in oauth_servers.items():
        gw_id = info.get("gateway_id")
        if not gw_id or gw_id in ("unknown", None):
            log(f"token-health: {name} has no gateway_id on record; skipping")
            continue

        if dry_run:
            log(f"[dry-run] would probe token health for {name} (gateway_id={gw_id})")
            continue

        if not gateway_has_oauth_token(gw_id):
            log(f"token-health: {name} has no stored OAuth token yet (pending first login); skipping")
            continue

        result = probe_token_health(gw_id, name)
        if result == "ok":
            log(f"token-health: {name} OK")
            continue
        if result == "ambiguous":
            continue

        # result == "auth-failed"
        last_notified = state["last_notified"].get(name, 0)
        if now - last_notified < DEBOUNCE_SECONDS:
            remaining = int(DEBOUNCE_SECONDS - (now - last_notified))
            log(f"token-health: {name} needs re-auth but debounced ({remaining}s left in debounce window)")
            continue

        auth_url = authorize_url_for(gw_id)
        notify(f"re-auth needed: {name} -> {auth_url}")
        ensure_state_dir()
        with open(PENDING_OAUTH_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{name} -> {auth_url}\n")
        state["last_notified"][name] = now
        log(f"token-health: notified re-auth needed for {name} -> {auth_url}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Claude Desktop remote MCP servers to the local gateway.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and log intended actions only. Makes ZERO gateway writes and does not persist state.",
    )
    args = parser.parse_args()

    log(f"desktop-mcp-sync starting ({'DRY-RUN' if args.dry_run else 'LIVE'})")

    if not acquire_lock():
        log("another run is already in progress (lock held); exiting")
        return 0

    try:
        state = load_state()
        try:
            config_sync_pass(state, dry_run=args.dry_run)
            reconcile_hub(dry_run=args.dry_run)
            token_health_pass(state, dry_run=args.dry_run)
        finally:
            if not args.dry_run:
                save_state(state)
            else:
                log("[dry-run] not persisting state.json (no side effects)")
    finally:
        release_lock()

    log("desktop-mcp-sync finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
