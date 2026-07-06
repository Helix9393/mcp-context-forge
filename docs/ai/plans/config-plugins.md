# Plugins (guardrails / policy / safety) — configuration plan

> **Doubt-review applied (adversarial pass against live code).** Corrections folded in: (a) `enforce`/`permissive` are **legacy aliases** the framework maps to the canonical `sequential`/`transform` modes — they work, but see the corrected mode section; (b) both PII filter and Secrets detection are now staged **observe-only first**, because in blocking/masking mode they act on *all* traffic (incl. the Work Board) and can silently break tool calls a non-technical user can't debug; (c) verification steps were fixed to target hooks the plugins actually run on; (d) UI mode toggles auto-expire after 24h — always mirror to YAML. Original analysis below, with fixes inline.

Scope: read-only analysis of the plugin framework in this fork, the catalogue of available plugins, what's actually running, and a staged plan for the handful worth enabling for a solo, single-user, pre-launch local gateway. No config/code was changed to produce this plan.

## Goal

Chad runs one always-on local gateway (launchd `com.chadkuisel.mcp-gateway`, uvicorn on `127.0.0.1:4444`) that proxies MCP tool calls for his own personal use. The gateway ships a large plugin framework (guardrails, PII redaction, policy engines, content moderation, reliability helpers). Almost none of it is turned on. This plan answers three questions in plain language:

1. How does the plugin system actually work on this machine (config file, hooks, enable/disable mechanics)?
2. Of the ~43 plugins bundled in this repo, which ones are genuinely worth Chad's time to turn on, and which are enterprise machinery he can ignore?
3. For the ones worth enabling, what's the exact, minimal-risk sequence to do it and confirm it worked?

## What the plugin system is

**Framework**: the plugin engine itself is a separate pip package, `cpex` (ContextForge Plugin Extensions), installed in `.venv/lib/python3.11/site-packages/cpex` (version `0.1.1`), documented as living at `github.com/contextforge-org/contextforge-plugins-framework` (per `plugins/AGENTS.md` — this is the plugin catalogue's own contributor guide, not gateway core docs; treat the upstream URL as **ASSUMPTION — verify before trusting**, it was not independently fetched). Individual first-party plugins (PII filter, secrets detection, rate limiter, encoded-exfil detector, URL reputation, retry-with-backoff) are *also* separately pip-installed as their own packages (`cpex_pii_filter-0.3.5`, `cpex_secrets_detection-0.3.6`, `cpex_rate_limiter-0.1.6`, `cpex_encoded_exfil_detection-0.3.5`, `cpex_url_reputation-0.3.4`, `cpex_retry_with_backoff-0.3.1` — all confirmed present under `.venv/lib/python3.11/site-packages/`). The rest of the catalogue (37+ plugins) lives as source in this repo under `plugins/<name>/` with a `plugin-manifest.yaml` + `README.md` each.

**Enable/disable switch (top level)**: `.env` line 26 on this machine: `PLUGINS_ENABLED=true` — confirmed by direct read of the actual `.env` (not `.env.example`). So the plugin framework itself is **loaded and running** in the live gateway process right now.

**Config file**: `PLUGINS_CONFIG_FILE` env var, default `plugins/config.yaml` (`.env.example` comment: "Path to the plugin configuration file… Contains plugin definitions, hooks, and settings"). Chad's `.env` does **not** override this var, so the active file is the repo-root `plugins/config.yaml`. There is a second, clearly-alternate file, `plugins/config-pii-guardian-policy.yaml`, sitting next to it — this is **not** the active file (nothing in `.env` points to it) and is best read as a "here's a stricter example" reference config the repo maintainers left behind, not something currently loaded.

**Config shape** (`plugins/AGENTS.md` → Configuration Schema, and confirmed against the live file):
```yaml
plugin_settings:
  plugin_timeout: 120          # active file's value (AGENTS.md's own example shows 30 — just an example, not this file's number)
  fail_on_plugin_error: false
  enable_plugin_api: true      # turns on the Admin-API plugin management endpoints, see below
  parallel_execution_within_band: true
  plugin_health_check_interval: 120
plugins:
  - name: "PluginName"
    kind: "fully.qualified.python.Class"   # or kind: "external" for an MCP-server-based plugin
    hooks: [...]
    mode: "enforce"    # or "permissive" / "disabled" / "enforce_ignore_error"
    priority: 50       # lower runs first
    conditions: []     # optional scoping by prompt/server_id/tenant_id
    config: {...}      # plugin-specific settings
```

**Hooks**: `plugins/AGENTS.md` states "six production hooks" — `prompt_pre_fetch`/`prompt_post_fetch`, `tool_pre_invoke`/`tool_post_invoke`, `resource_pre_fetch`/`resource_post_fetch`. This is **stale relative to the actual config file**: the live `plugins/config.yaml` also uses a 7th hook, `http_auth_resolve_user` (used by `JwtClaimsExtractionPlugin`), which AGENTS.md doesn't mention. Flagging this as a real documentation/code drift, not a guess.

**Mode values — CORRECTED after doubt review (read `cpex/models.py` directly).** The canonical `PluginMode` enum (`cpex` `models.py:90,116-121`) is `fire_and_forget` / `concurrent` / `sequential` / `transform` / `audit` / `disabled` — there is **no** `enforce` or `permissive` in the enum; `PluginMode("enforce")` would raise `ValueError`. AGENTS.md's `sequential`/`transform` naming is the CANONICAL one; the YAML files' `enforce`/`permissive` inline comments are **legacy aliases**, silently mapped by a `@model_validator(mode="before") _migrate_legacy_modes` (`models.py:1243-1262`): `enforce`→`sequential`, `permissive`→`transform`, `enforce_ignore_error`→`sequential` + `on_error=ignore`. So writing `mode: "enforce"` in the YAML works and behaves as intended, but the accurate mental model is: **`enforce` = `sequential` (runs in order, CAN block/modify)**, **`permissive` = `transform` (non-blocking, observe/transform only)**, and there is also an **`audit`** mode (observe-only, log without acting). The staged steps below use `permissive`/`audit` deliberately for observe-only phases.

**External plugins**: a plugin entry can set `kind: "external"` and point at a separate MCP server (`STDIO` or `STREAMABLEHTTP` transport) that must expose `get_plugin_config` plus the hook-name tools. Two examples exist in the active config, both **fully commented out**: `ClamAVRemote` (external ClamAV scanner — the repo's own comment says it's "DISABLED due to script path issue," i.e. known-broken upstream) and `OPAPluginFilter` (would need a running Open Policy Agent server at `http://127.0.0.1:8181` — nothing currently listens there on this machine, unverified but no OPA process was found).

**How toggling actually works on this machine** — this matters more than it looks:
- Uvicorn runs via launchd with `--reload --reload-dir mcpgateway` (confirmed from `launchctl print gui/501/com.chadkuisel.mcp-gateway`). The reload watch is scoped to the `mcpgateway/` package only. `plugins/config.yaml` lives at the repo root, **outside** that watched directory — editing it will **not** auto-reload the running process.
- There is, however, a live Admin API for exactly this: `GET /admin/plugins` (list + status), `GET /admin/plugins/{name}` (detail), and `PUT /admin/plugins/{name}` (mode override). Reading `mcpgateway/admin.py` (~line 17780 `update_plugin_mode`): a `PUT` writes the new mode to an in-process override map **and** to Redis (`publish_plugin_mode_change`), then invalidates cached plugin managers — this takes effect on the *next request*, with **no gateway restart needed**. Confirmed a local Redis is actually reachable right now (`redis-cli ping` → `PONG`, no Docker container involved — a native Redis process is already running on this machine).
- Important nuance: that `PUT` override is **not written back into `plugins/config.yaml`**. It lives in Redis + the in-process map. If Redis is ever flushed/reinstalled, or the override map is lost, the plugin's mode reverts to whatever `plugins/config.yaml` says on disk (currently `disabled` for everything). **Recommendation baked into the staged steps below: after confirming a toggle works via the Admin UI, also hand-edit the YAML file to match, so the change survives a Redis loss — then restart the gateway once to pick up the YAML as the new baseline.**
- Gateway logs land at `/Users/chadkuisel/Library/Logs/mcp-gateway.log` (from the launchd plist's `StandardOutPath`/`StandardErrorPath`) — this is the file to tail for verification.

## Catalogue

43 plugin entries exist in the active `plugins/config.yaml`; **all 43 are currently `mode: "disabled"`** (verified: `grep -oP 'mode:\s*"?\K[a-zA-Z_]+' plugins/config.yaml | sort | uniq -c` → `43 disabled`, zero of any other value). Two more (`ClamAVRemote`, `OPAPluginFilter`) exist only as commented-out external-plugin examples and aren't counted among the 43.

| Plugin | What it does | Effort to enable | External deps | Verdict | Why |
|---|---|---|---|---|---|
| **PIIFilterPlugin** | Detects/masks SSN, credit card, email, AWS/API keys in prompts + tool calls | Flip 1 YAML line | None (pip pkg already installed) | **enable-now** | Direct match for "PII redaction," zero setup cost, masks by default without blocking |
| **SecretsDetection** | Regex detector for AWS keys, private-key blocks, JWT-like tokens, high-entropy secrets | Flip 1 YAML line | None (pip pkg already installed) | **enable-now** | Cheapest real guard against Chad accidentally routing a live credential through a tool call |
| **CodeSafetyLinterPlugin** | Blocks `eval`/`exec`/`os.system`/`subprocess.*`/`rm -rf` patterns in tool *outputs* | Flip 1 YAML line, test in `permissive` first | None | **enable-now (staged)** | Chad routes coding-agent tool calls through this gateway; a tripwire on dangerous exec patterns is cheap and relevant — but no visible "block vs. warn" toggle in its config, so stage via `permissive` before `enforce` to avoid surprise blocks on legitimate code snippets |
| EncodedExfilDetector | Flags base64/hex/percent-encoded payloads in prompts/outputs (possible exfil) | Flip 1 YAML line | None | maybe-later | Same trusted first-party family as the two above, but real false-positive risk (legit tools pass base64 images/blobs routinely) against a threat model (insider exfil) that doesn't fit a solo non-adversarial user |
| OutputLengthGuardPlugin | Enforce min/max tool output length; block or truncate | Needs per-tool thresholds tuned | None | maybe-later | Attractive for Chad's stated context-economy priorities, but needs real tuning work or it'll truncate legitimate large results |
| SchemaGuardPlugin | Validate tool args/results against a JSON (sub)schema | Needs Chad to author schemas per tool | None | maybe-later | Real safety value, but zero schemas exist yet — this is a project, not a toggle |
| CachedToolResultPlugin | Caches idempotent tool results in-memory | Flip 1 line | None | maybe-later | Free latency/cost win, no guardrail role — worth a look once the enable-now three are validated |
| ToonEncoder | Converts JSON tool results to TOON format, ~30-70% token reduction | Flip 1 line | None | maybe-later | Genuinely on-brand for Chad's token-conservation priorities, but it's a format transform, not a guardrail — separate decision, flagged as a tangent |
| RateLimiterPlugin | Per-user/tenant/tool rate limiting via Redis fixed-window counters | Flip 1 line | Redis — **already running locally** (confirmed reachable) | skip-for-solo | Rate limiting protects a service from *other* callers; Chad is the only caller of his own gateway. Enterprise control with no threat model here |
| UnifiedPDPPlugin | Unified RBAC/ABAC policy engine (native rules / MAC / OPA / Cedar) | Non-trivial: author rule sets | Native sub-engine: none. OPA/Cedar sub-engines: separate servers (not running) | skip-for-solo | RBAC solves a multi-user access-control problem. Chad is sole admin — nothing to gate access from |
| JwtClaimsExtractionPlugin | Extracts JWT claims for downstream OPA/Cedar authz plugins | Flip 1 line | None directly, but pointless without UnifiedPDP/OPA/Cedar | skip-for-solo | Feeds a policy engine Chad isn't running |
| ContentModeration | AI-powered harm/toxicity moderation via IBM Watson/Granite Guardian (Ollama)/OpenAI/Azure/AWS | Real setup: `ollama serve` + `ollama pull granite3-guardian:2b` for the no-external-key path | Ollama installed on this machine but **not currently running**, model **not pulled** (`ollama list` → "could not connect to ollama server") | maybe-later | Interesting because the free path uses local Ollama (machine already has it), but it's real setup work, not a toggle, and moderating Chad's own single-user traffic for hate/violence/self-harm categories has limited value |
| HarmfulContentDetector | Regex-lexicon detector for self-harm/violence/hate phrases | Flip 1 line | None | skip-for-solo | Blunt keyword matching aimed at moderating *other people's* input; doesn't fit a single operator's own traffic |
| VirusTotalURLCheckerPlugin | Checks URLs/domains/IPs/files against VirusTotal | Needs a VT API key (external signup) | External API + key | maybe-later | Only useful if tools fetch arbitrary untrusted URLs; worth it only if that becomes a real workflow |
| SafeHTMLSanitizer | Strips XSS vectors from fetched HTML resources | Flip 1 line | None | maybe-later | Relevant only if resource-fetch tools pull untrusted HTML |
| ResourceFilterExample | Protocol/domain allowlist + content redaction for resource fetches | Meant as a copy-and-customize template (name says "Example") | None | maybe-later | Not meant to be enabled as-is; would need Chad to define his own domain/protocol policy first |
| RobotsLicenseGuard | Honors robots/noai + license meta tags on fetched HTML | Flip 1 line | None | skip-for-solo | Only matters when scraping others' web content at scale |
| CircuitBreaker | Trips a per-tool breaker on high error rate / consecutive failures | Flip 1 line | None | maybe-later | Reliability nicety; for a solo user, failures are usually noticed directly and immediately |
| Watchdog | Warns/blocks on tool calls exceeding a max runtime | Flip 1 line | None | maybe-later | Same reasoning as CircuitBreaker |
| RetryWithBackoffPlugin | Auto-retries failed tool calls with backoff | Flip 1 line | None | maybe-later | Reliability convenience, not a guardrail |
| JSONRepairPlugin | Attempts to repair near-valid JSON tool outputs | Flip 1 line | None | maybe-later | Handy but narrow |
| ALTKJsonProcessor | Extracts data from long JSON tool responses via ALTK | Flip 1 line | None | maybe-later | Token-economy feature, same tangent-lane as ToonEncoder |
| ResponseCacheByPrompt | Advisory cosine-similarity cache hint (metadata only, doesn't short-circuit execution) | Flip 1 line | None (own README: "no external dependencies") | skip | Its own README admits it can't actually block execution at pre-hook — advisory-only, limited practical payoff |
| SQLSanitizer | Detects/strips/blocks risky SQL patterns in tool args/outputs | Flip 1 line | None | maybe-later | Only relevant **if** Chad routes SQL-executing MCP tools through this gateway — unverified whether he currently does |
| ArgumentNormalizer | Normalizes Unicode/whitespace/casing/dates/numbers in tool args | Flip 1 line | None | maybe-later | Input hygiene, not a guardrail |
| FileTypeAllowlistPlugin | Restricts allowed file types for resource fetch | Flip 1 line | None | maybe-later | Only matters if fetch tools handle arbitrary file types |
| URLReputationPlugin | Checks URL reputation (cpex_url_reputation package) | Unverified — no `config:` block shown, dependency on an external reputation feed **not confirmed** | Unverified — **ASSUMPTION: likely needs an external reputation service/API**, not independently confirmed | maybe-later (unverified) | Flagging as unverified rather than guessing at its backend |
| HeaderInjector | Injects configured HTTP headers on resource fetch | Flip 1 line | None | skip | Niche; no current use case |
| PrivacyNoticeInjector | Appends a static privacy-notice string to rendered prompts | Flip 1 line | None | skip | Cosmetic; no PHI/compliance requirement identified for this gateway's traffic |
| WebhookNotification | Sends HTTP webhooks on plugin events/violations | Needs a receiving webhook URL | External receiver | skip-for-solo | Nothing to notify — Chad reads the log file directly |
| VaultPlugin | Generates bearer tokens from vault-saved tokens | Needs a configured token vault | Depends on vault setup | skip-for-solo | Enterprise secret-management pattern |
| CodeFormatter | Formats code found in tool outputs | Flip 1 line | None | skip | Cosmetic |
| LicenseHeaderInjector | Injects license headers into code outputs | Flip 1 line | None | skip | Not relevant to Chad's use case |
| CitationValidator | Validates citations in outputs | Flip 1 line | None | skip | Only relevant for research/citation-heavy tool flows |
| MarkdownCleanerPlugin | Tidies markdown formatting in prompts/resources | Flip 1 line | None | skip | Cosmetic |
| HTMLToMarkdownPlugin | Converts fetched HTML resources to Markdown | Flip 1 line | None | skip | Quality-of-life, not a guardrail |
| DenyListPlugin | Blocks a configured word list in prompts | Flip 1 line | None | skip | Demo-grade, arbitrary word list has no clear use case here |
| ReplaceBadWordsPlugin | Find/replace words in prompts (ships with "crap"→"crud" demo config) | Flip 1 line | None | skip | Demo plugin |
| SpanAttributeCustomizer | Renames OpenTelemetry span attributes for compliance/org standards | Flip 1 line | Needs an OTel collector to matter | skip-for-solo | Enterprise observability semantics with no consumer |
| ToolsTelemetryExporter | Exports tool-invocation telemetry to OpenTelemetry | Flip 1 line | Needs an OTel collector | skip-for-solo | Same — nothing currently consumes OTel data on this machine |
| TimezoneTranslator | Converts ISO timestamps between server/user timezones | Flip 1 line | None | skip | Not a guardrail, no identified need |
| SPARCStaticValidator | Static-validates tool-call args (types/required/enums) via ALTK | Flip 1 line | None | maybe-later | Real value if Chad wants stricter tool-arg contracts; not urgent |
| AIArtifactsNormalizer | Normalizes smart quotes/ligatures/dashes in AI-generated text | N/A | None | skip — **broken** | Repo's own comment: "DISABLED due to syntax error in plugin." Entire entry is commented out; don't chase it |
| ClamAVRemote | External MCP-based ClamAV file/text scanner | N/A | External MCP server (script) | skip — **broken** | Repo's own comment: disabled due to a script-path issue upstream |
| OPAPluginFilter | External OPA-backed policy filter example | Needs a running OPA server | External OPA server (none running) | skip-for-solo | Real infra to stand up for a single local user with no external callers |

## Current state

- `PLUGINS_ENABLED=true` in `.env` — framework is loaded in the live process.
- Active config file: `plugins/config.yaml` (default path, not overridden).
- **All 43 registered plugin entries are `mode: "disabled"`.** The framework is running but doing nothing — every hook point is a no-op pass-through right now.
- A second file, `plugins/config-pii-guardian-policy.yaml`, exists with 3 plugins set to `enforce` (`PIIFilterPlugin`, `ContentModeration`, `UnifiedPDPPlugin`) but is **not loaded** — nothing in `.env` points at it. Treat it as a reference/example, not a preview of production state.
- Admin UI "Plugins" nav page is backed by live `GET/PUT /admin/plugins*` routes in `mcpgateway/admin.py`; it can list current plugin status and toggle mode without a restart (see mechanics above). It was not exercised in this analysis (would require an authenticated admin session — the `/admin` root correctly 307-redirects to login, confirming it's not open).

## Staged steps

Only for the three **enable-now** plugins. Each step is independent — do them one at a time and verify before moving to the next.

### 1. PIIFilterPlugin  ⚠️ CORRECTED — this MUTATES live traffic, so start observe-only

**Why the change:** the original plan called `enforce` + `block_on_detection: false` "safe because it only masks, not blocks." That's misleading. `enforce` = `sequential` = a **modifying** mode: with `detect_email: true, detect_ssn: true, detect_credit_card: true` and `conditions: []`, EVERY email/SSN/credit-card-shaped value flowing through the prompt/tool hooks — on ALL servers, incl. Work Board — gets **rewritten to a mask before the tool receives it**. A legitimate lookup/notify tool that needs a real email address will silently get `[PII_REDACTED]` instead and fail with no error surfaced. "Mask" here means "mutate the real payload," which the original step never said.

- **Phase A (observe-only):** in `plugins/config.yaml`, `PIIFilterPlugin` entry (`kind: "cpex_pii_filter.PIIFilterPlugin"`), set `mode: "audit"` (observe + log, does NOT modify the payload). Restart once to bake it in: `launchctl kickstart -k gui/501/com.chadkuisel.mcp-gateway`. Run a day of normal usage and `tail -f /Users/chadkuisel/Library/Logs/mcp-gateway.log` — watch which real values it WOULD have masked. If it flags emails/IDs that legit tools actually need, either scope it (`conditions:` limited to specific `server_id`s) or leave it audit-only.
- **Phase B (only if audit showed no collateral):** change `mode: "audit"` → `mode: "enforce"` (now masking is live), keep `block_on_detection: false`. Restart.
- **Verification (fixed):** don't just look for a log line — confirm the actual substitution. Send a prompt/tool call containing a *non-whitelisted* fake value (note `test@example.com` and `555-555-5555` ARE whitelisted at config.yaml:120-122 — use a different fake, e.g. `123-45-6789`) and confirm the value the tool RECEIVES is the masked form, not just that a log line appeared. Cross-check `GET /admin/plugins/PIIFilterPlugin` shows the active mode.

### 2. SecretsDetection  ⚠️ CORRECTED — do NOT go straight to blocking

**Why the change:** the original plan set `enforce` with `block_on_detection: true, min_findings_to_block: 1` immediately — i.e. **one hit hard-blocks the result, on all traffic**. But its enabled set includes `jwt_like: true` and `hex_secret_32: true` (config.yaml:779-780), and JWT-shaped strings + 32-char hex (MD5/ETag/hash IDs, cache keys) are ROUTINE in legitimate tool output. So this would false-block normal Work Board / tool responses with a bare "blocked" the user can't debug. This is the higher false-positive plugin, yet it was the one NOT staged — inverted risk.

- **Phase A (observe-only):** `SecretsDetection` entry (`kind: "cpex_secrets_detection.SecretsDetectionPlugin"`), set `mode: "audit"` (or `permissive`), and set `block_on_detection: false` for now. Restart. Run normal usage and watch the log for how often `jwt_like`/`hex_secret_32` fire on legitimate output. Strongly consider setting `jwt_like: false` and `hex_secret_32: false` (keep the high-signal `aws_access_key`, private-key PEM, and Slack-token patterns) to cut the false-positive surface.
- **Phase B (only if audit is quiet):** set `mode: "enforce"` and `block_on_detection: true`. Restart.
- **Expected result**: a prompt or tool **output** containing a real secret pattern gets blocked, with a violation logged. (Note: this fires post-invoke — it blocks the RESULT from being returned; the tool already ran. It is not pre-execution prevention.)
- **Verification (fixed):** SecretsDetection's hooks are `prompt_pre_fetch, tool_post_invoke, resource_post_fetch` (config.yaml:767) — **NOT** `tool_pre_invoke`. A fake key in tool *input arguments* is never inspected. Put the dummy secret where a hooked point sees it: in a **prompt** (`prompt_pre_fetch`) or in a tool's **output** (`tool_post_invoke`). Use a fake AWS-key-shaped string (`AKIA` + 16 alphanumerics, unused) and confirm that prompt/output is blocked and logged.

### 3. CodeSafetyLinterPlugin (staged: permissive → enforce)

- **Action, phase A**: `CodeSafetyLinterPlugin` entry, change `mode: "disabled"` → `mode: "permissive"` first (log-only; nothing in its config file exposes an explicit block/warn toggle, so `permissive` is the safe way to see what it *would* have flagged before committing to `enforce`). Restart.
- **Run it for a normal working session** (a day or so of Chad's actual coding-agent usage through the gateway) and check `/Users/chadkuisel/Library/Logs/mcp-gateway.log` for any `CodeSafetyLinterPlugin` matches on legitimate code (e.g. a tool output that legitimately contains the string `subprocess.run(` as part of ordinary generated code, which would be a false-positive-in-waiting).
- **Action, phase B** (only if phase A showed no problematic false positives): change `mode: "permissive"` → `mode: "enforce"`, restart.
- **Verification**: after phase B, deliberately produce a tool output containing `os.system(` or `eval(` and confirm the call is blocked with a violation logged.

## Rollback

- Any single plugin: set its `mode:` back to `"disabled"` in `plugins/config.yaml` and restart (`launchctl kickstart -k gui/501/com.chadkuisel.mcp-gateway`) — this always wins since it's the on-disk baseline.
- Faster, no-restart rollback: use the Admin UI's Plugins page (or `PUT /admin/plugins/{name}` with `{"mode": "disabled"}`) — takes effect immediately via the Redis/in-process override, but **this doesn't touch the YAML file**. CORRECTED detail: that override carries a **24-hour TTL** (`_PLUGIN_MODE_TTL_SECONDS = 86400`, `mcpgateway/plugins/__init__.py:49,281,287`) — it is NOT permanent. A UI toggle silently reverts to the YAML's on-disk mode after 24h even if Redis is never touched. So treat UI toggles as *temporary experiments only*; for anything you want to keep, edit `plugins/config.yaml` and restart so the on-disk baseline is authoritative. Always mirror a UI change to the YAML in the same session.
- Full reset: `PLUGINS_ENABLED=false` in `.env` + restart turns off the entire framework (nuclear option, not needed for a single-plugin rollback).

## Open questions / risks / unverified assumptions

- **ASSUMPTION** — the `cpex` framework's GitHub URL cited in `plugins/AGENTS.md` was not independently fetched/verified; treat it as informational only.
- **ASSUMPTION** — mode semantics (`enforce`/`permissive`/`disabled`/`enforce_ignore_error`) are inferred from the inline YAML comments repeated across ~43 entries, not from reading `cpex`'s own source. High confidence given the consistency, but not a first-party confirmation.
- **Documentation drift, confirmed not assumed**: `plugins/AGENTS.md`'s "Hook Lifecycle" section omits the `http_auth_resolve_user` hook actually used in the live config, and its "Plugin Modes" section names entirely different mode values (`sequential`/`transform`) than what every YAML entry actually uses. Don't trust AGENTS.md's prose over the YAML file's own inline comments if they ever conflict again.
- **Unverified**: whether Chad currently routes any SQL-executing or arbitrary-URL-fetching MCP tools through this gateway — that fact would upgrade `SQLSanitizer` / `SafeHTMLSanitizer` / `VirusTotalURLCheckerPlugin` from "maybe-later" toward "enable-now." Worth a follow-up look at what tools/servers are actually registered on this gateway (out of scope for this plugin-only pass).
- **Unverified**: `URLReputationPlugin`'s actual backend/dependency — no `config:` block was visible for it in the sampled YAML excerpt, so its external-service requirement (if any) is a guess, not a confirmed fact.
- **Risk**: the Redis-backed live-override mechanism (`PUT /admin/plugins/{name}`) means "what mode is this plugin actually running in" can silently diverge from what `plugins/config.yaml` says on disk. For a solo operator without a team to coordinate with, this is low-stakes, but it's worth remembering if gateway behavior ever looks inconsistent with the YAML file's contents — check the live Admin UI/API state, not just the file.
- **Risk, self-inflicted footgun**: `CodeSafetyLinterPlugin` in `enforce` mode has no confirmed way (from its visible config surface) to distinguish "code that legitimately contains `eval(`" from "an attack" — it's a blunt regex tripwire on `tool_post_invoke`. If Chad's coding-agent workflows regularly produce code samples containing these patterns as normal output (not just executable payloads), enabling this in `enforce` mode could start blocking legitimate results. That's exactly why the staged plan above runs it `permissive` first.
- Not verified in this pass: whether restarting via `launchctl kickstart -k` actually re-reads `plugins/config.yaml` cleanly (vs. picking up a stale Redis override first) — first restart after any YAML edit should be watched via the log file to confirm the expected mode took effect, not assumed.
