# Local Model Coordination — mcp-context-forge Gateway

Status: DRAFT — read-only analysis complete, no config changes made.
Scope: connect Chad's local models (Ollama, LM Studio) into the already-running gateway's LLM layer, usable via Admin UI → LLM Chat.

> **Doubt-review applied (adversarial pass, live-verified).** Corrections: (a) the primary success proof is now **LLM Settings → "Test"** (`POST /admin/llm/test`), which calls the provider directly — NOT "send a message in LLM Chat," because that chat harness ALSO requires a reachable MCP server and returns a 503 without one (unrelated to your model config). (b) `LLMCHAT_ENABLED` is confirmed **true by default** (no `.env` override) — the restart scare is dropped. (c) Ollama has zero models — Stage 0 now gives the explicit `ollama pull` command. (d) LM Studio's server toggle now lives under the **Developer** tab. Fixes inline.

---

## Goal (plain language)

The gateway already has a working "LLM Settings" + "LLM Chat" feature. Right now it has **zero providers configured** — it's a blank slate. The goal is to register Chad's local model servers (Ollama, LM Studio) as providers in that feature, so he can pick between local models (and later cloud models) from one dropdown in the Admin UI's LLM Chat panel, without hand-rolling a separate chat client.

This is **not** building new coordination/routing code — the gateway already has the provider-registry + single-request model-picker mechanism built in. This plan is pure configuration: add provider rows, verify they respond.

---

## Current State (verified)

### 1. Gateway LLM subsystem — real feature, not a stub

Evidence, all read from `/Users/chadkuisel/Workspace/mcp-context-forge`:

- `mcpgateway/llm_provider_configs.py` defines `LLMProviderTypeEnum` with 12 supported provider types: `openai`, `azure_openai`, `anthropic`, `bedrock`, `google_vertex`, `watsonx`, `ollama`, `openai_compatible`, `cohere`, `mistral`, `groq`, `together`.
- Ollama's registered config (lines 431-440): `requires_api_key=False`, `requires_api_base=True`, `api_base_default="http://localhost:11434"`, described as "Ollama server URL (native API)" — i.e. the gateway talks to Ollama's **native** `/api/*` endpoints, not its OpenAI-compatible `/v1` shim.
- `openai_compatible` config (lines 441-450): `requires_api_key=False`, `requires_api_base=True`, `api_base_default="http://localhost:8080/v1"` — a generic slot for any OpenAI-compatible server. **This is the type to use for LM Studio** (LM Studio's server default is `http://127.0.0.1:1234/v1`).
- Storage is a real SQLite schema, not env vars, confirmed by direct read of `mcp.db`:
  ```
  CREATE TABLE llm_providers (
      id, name, slug, description, provider_type, api_key, api_base, api_version,
      config JSON, default_model, default_temperature, default_max_tokens,
      enabled, health_status, last_health_check, plugin_ids JSON, created_at, updated_at, ...
  )
  CREATE TABLE llm_models (
      id, provider_id (FK→llm_providers), model_id, model_name, model_alias,
      supports_chat/streaming/function_calling/vision, context_window, max_output_tokens,
      enabled, deprecated, ...
  )
  ```
  Both tables currently have **0 rows** (`select count(*) from llm_providers;` → `0`; same for `llm_models`). No providers of any kind — local or cloud — are configured yet.
- `.env.example` (line 3448-3450) confirms the intended workflow: *"All LLM providers are now configured via Admin UI → Settings → LLM Settings. Add providers (OpenAI, Azure OpenAI, Anthropic, AWS Bedrock, Ollama, watsonx) and their models through the Admin UI. API keys and credentials are securely [encrypted]."*
- `mcpgateway/routers/llm_admin_router.py` mounts under `/admin/llm/...` (confirmed via comment in `mcpgateway/admin.py` lines 296-298: `/admin/llm/providers/html`, `/admin/llm/models/html`, `/admin/llm/api-info/html`). It exposes CRUD for providers/models plus `GET /admin/llm/provider-defaults`, `GET /admin/llm/provider-configs`, `POST /admin/llm/providers/{id}/fetch-models`, and `POST /admin/llm/test` (test a model without needing a client-side API key).
- API keys are encrypted before storage: `protect_provider_config_for_storage()` / `_encrypt_provider_config_secret()` in the admin router path run on every sensitive config field before the DB write.
- Admin nav (`mcpgateway/templates/admin.html` lines 528-546): sidebar has an "LLM Chat" tab (gated by `llmchat_enabled`, i.e. `LLMCHAT_ENABLED` — confirmed `true` is the intended default per `.env.example` line 3441, commented out so it falls back to the code default) and an "LLM Settings" tab (gated by `'settings' not in hidden_sections`).
- `mcpgateway/routers/llmchat_router.py` exposes the chat UI's backend: `POST /connect`, `POST /chat`, `POST /disconnect`, `GET /status/{user_id}`, `GET /config/{user_id}`, `GET /gateway/models` (the last one populates the model-picker dropdown from `llm_provider_service.get_gateway_models()` — i.e. from whatever is in `llm_models` with `enabled=true`).
- Separately, `mcpgateway/routers/llm_proxy_router.py` is mounted at `settings.llm_api_prefix` (default `/v1` per `.env.example` line 3478, `LLM_API_PREFIX=/v1`). This exposes the *same* provider/model registry as a generic OpenAI-compatible endpoint (`http://127.0.0.1:4444/v1/chat/completions`) — useful if Chad ever wants another tool (not just the Admin Chat UI) to hit the gateway as "one unified LLM endpoint."

**"Coordination/routing" reality check** (read `mcpgateway/services/llm_proxy_service.py` directly):
```python
async def chat_completion(self, db, request):
    ...
    provider, model = self._resolve_model(db, request.model)   # single lookup, by id / model_id / model_alias
    if provider.provider_type == LLMProviderType.AZURE_OPENAI: ...
    elif provider.provider_type == LLMProviderType.ANTHROPIC: ...
    elif provider.provider_type == LLMProviderType.OLLAMA: ...
    else: ...  # OpenAI-compatible default — covers LM Studio
```
`_resolve_model()` does one DB lookup and returns exactly one `(provider, model)` pair per request. There is **no fan-out, ensemble, voting, or cross-provider orchestration** anywhere in this file or its callers. What the gateway actually gives you is: *one registry of many providers/models, with a single-model-per-request picker* (dropdown in the UI, or the `model` field in the OpenAI-compatible API). That's a real and useful feature — it's just not "coordination" in the multi-model-consensus sense. Anything beyond "pick one model per message" would have to be built by Chad (e.g., calling `/v1/chat/completions` twice with two different `model` values and comparing outputs client-side) — the gateway doesn't do that for him.

### 2. Local models on this machine — mixed picture, evidence below

| Server | Installed? | Running now? | Models available |
|---|---|---|---|
| **Ollama** | Yes, binary at `/opt/homebrew/bin/ollama`, registered as a brew service | **No.** `ollama list` → `Error: could not connect to ollama server, run 'ollama serve' to start it`. `curl http://127.0.0.1:11434/api/tags` → no response (connection refused). `brew services list` shows `ollama none chadkuisel` (status "none" = not started). `pgrep -fl ollama` → no process. | **None pulled.** `~/.ollama/models` is 8.0 KB total; `~/.ollama/models/manifests` and `.../blobs` are empty. This is a fresh/unused Ollama install — no models have ever been pulled on this machine. |
| **LM Studio** | Yes, app at `/Applications/LM Studio.app` | **No.** No `lms`/LM Studio process found via `ps aux`. `curl http://127.0.0.1:1234/v1/models` → no response. | **One real model on disk**: `~/.lmstudio/models/introvoyz041/Cydonia-v4.1-MS3.2-Magnum-Diamond-24B-mlx-4Bit-mlx-4Bit` — 12 GB, MLX format, 4-bit quantized. The `lmstudio-community/` folder exists but contains only a stray `.DS_Store` — no actual model there (likely a deleted/never-completed download). |

**Bottom line**: neither local server is running right now, and Ollama has zero models to serve. LM Studio has exactly one usable local model (a 24B MLX model), but its server must be started first.

### 3. Currently configured providers in this gateway

**None.** `select count(*) from llm_providers` → `0`, `select count(*) from llm_models` → `0`, verified via direct read-only `sqlite3 -readonly mcp.db` query. There is nothing to migrate or conflict with — this is a from-scratch setup.

Gateway itself is confirmed up: `curl -o /dev/null -w '%{http_code}' http://127.0.0.1:4444/` → `303` (redirect to login, expected for an unauthenticated root request).

---

## Prerequisites

| # | Prerequisite | Proving command | Status |
|---|---|---|---|
| 1 | Gateway reachable | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:4444/` → `303` | ✅ verified |
| 2 | `llm_providers`/`llm_models` tables exist (migration applied) | `sqlite3 -readonly mcp.db '.schema llm_providers'` | ✅ verified |
| 3 | `LLMCHAT_ENABLED` true (LLM Chat tab visible) | code default `llmchat_enabled: bool = Field(default=True)` at `config.py:1249`; live `.env` has NO `LLMCHAT_ENABLED` override | ✅ CONFIRMED true by default (no override → tab renders). No restart scare — the earlier hedge was resolvable and resolves TRUE. |
| 4 | Ollama server can be started | `ollama serve` (foreground) or `brew services start ollama` (background) | Not started yet — first move (see Staged Steps) |
| 5 | LM Studio server can be started | Open LM Studio app → enable "Local Server" (or `~/.lmstudio/bin/lms server start` if the bundled `lms` CLI is on PATH) | Not started yet |
| 6 | Ollama has at least one model to serve | `ollama list` after starting the server | ❌ Currently empty — Chad must `ollama pull <model>` before Ollama is useful (explicitly out of scope for this read-only analysis — Chad's call on which model) |
| 7 | LM Studio has a loadable model | Cydonia-v4.1-MS3.2-Magnum-Diamond-24B-mlx-4Bit present on disk (12 GB) | ✅ verified — but load it in LM Studio's UI before starting the server, or point the server at that model explicitly |

---

## Staged Steps

### Stage 0 — Start the local servers (outside the gateway)

1. **Ollama**: run `ollama serve` in a terminal (or `brew services start ollama` to run it in the background permanently). Verify: `curl -s http://127.0.0.1:11434/api/tags` returns `{"models":[...]}`.
   - **Ollama has ZERO models pulled** (hard blocker — until you pull one, the dropdown and `fetch-models` are empty). Run a concrete first pull: `ollama pull llama3.2` (a small ~2GB default that's fine for testing). Verify: `ollama list` shows it. (Swap for any other model later — this is just to get a working one on the board.)
2. **LM Studio**: open the app → in current versions the local-server toggle is under the **Developer** tab (older builds called it "Local Server") → load the Cydonia-v4.1-MS3.2-Magnum-Diamond-24B model → **Start Server** (default port 1234). Verify: `curl -s http://127.0.0.1:1234/v1/models` returns a JSON `data` array containing the model. (CLI alternative: `~/.lmstudio/bin/lms server start` — but the `lms` CLI may need `~/.lmstudio/bin/lms bootstrap` first to be on PATH.)

Both of these are outside the gateway and outside this plan's write-boundary — they're prerequisites, not gateway config.

### Stage 1 — Register Ollama as a provider (Admin UI path)

1. Navigate to `http://127.0.0.1:4444/admin` → log in → left sidebar → **LLM Settings**.
2. Click "Add Provider" (or equivalent — exact button label wasn't read from the JS/HTML in this pass; the panel is driven by `mcpgateway/admin_ui/llmModels.js` / the `llm_providers_partial.html` template).
3. Fill fields per `llm_provider_configs.py`'s Ollama definition:
   - Provider type: `Ollama`
   - Name: e.g. `Local Ollama`
   - API Base: `http://localhost:11434` (native API — do not append `/v1`)
   - API Key: leave blank (`requires_api_key=False`)
4. Save. Then use the "fetch models" action (`POST /admin/llm/providers/{provider_id}/fetch-models`) to pull the model list from the live Ollama server, or manually add a model row with `model_id` matching Ollama's tag (e.g. `llama3.2:latest` — colons are explicitly allowed per `LLMModelBase.model_id`'s validator comment).
5. **Verification (PRIMARY — corrected):** use the LLM Settings page's **"Test"** action (`POST /admin/llm/test` with `test_type: "chat"`, `model_id: <the model>`, a short `message`) — this instantiates the proxy and calls your local model **directly, with no MCP dependency**, and returns the completion. This is the real proof the provider works. *Secondary/optional:* the "LLM Chat" tab's dropdown should also list the model, but do NOT rely on sending a message there as your test — that chat harness's `connect` also opens an MCP-server session and returns 503 "Failed to connect to MCP server" if none is running at its configured URL, which has nothing to do with your model. Get "Test" green first; treat LLM Chat as a bonus.

### Stage 2 — Register LM Studio as an OpenAI-Compatible provider

1. Same Admin UI path → **LLM Settings** → Add Provider.
2. Provider type: `OpenAI Compatible`
3. API Base: `http://127.0.0.1:1234/v1` (LM Studio's OpenAI-compatible endpoint — do not use Ollama's port here)
4. API Key: leave blank, or a dummy value if LM Studio's server enforces a non-empty `Authorization` header (LM Studio typically does not require a real key locally, but if the "Test" call in Stage 1 pattern fails with a 401, add a placeholder string).
5. Add a model row with `model_id` = the exact model identifier LM Studio reports in `GET /v1/models` (fetch this via `curl http://127.0.0.1:1234/v1/models` — it will be something like the model's folder/file name, not necessarily `Cydonia-v4.1-...` verbatim — confirm from the live response, don't guess).
6. **Verification**: same as Stage 1 — use LLM Settings **"Test"** (`POST /admin/llm/test`, `test_type: "chat"`) as the primary proof (no MCP dependency); the LLM Chat dropdown listing is a secondary check only.

### Stage 3 — (Optional) Confirm the unified `/v1` proxy path

1. With both providers configured and `enabled=true`, call the gateway directly (bypassing the Admin Chat UI) to prove the "one endpoint, pick-a-model" pattern:
   ```
   curl -s http://127.0.0.1:4444/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"model": "<ollama-model-id-or-alias>", "messages": [{"role":"user","content":"hi"}]}'
   ```
   (This likely needs a gateway auth token/API key per normal gateway auth rules — not investigated here since it's optional/Stage 3.)
2. Repeat with the LM Studio model's id to confirm both routes work through the same gateway endpoint.

This proves the "one place to route between local models" goal is met via manual model selection (per-request `model` field), not automatic coordination.

---

## Rollback

- Deleting a bad provider row: Admin UI → LLM Settings → Providers list → delete action, or `DELETE` via `llm_admin_router`'s provider endpoint. `llm_models` rows cascade-delete on provider delete (`ON DELETE CASCADE` confirmed in the `llm_models` schema), so no orphaned model rows to clean up manually.
- If a bad config makes the LLM Chat panel error out entirely: disable the provider (`enabled=false` toggle) rather than deleting, to preserve the config while debugging.
- No gateway restart, migration, or code change is needed at any point in this plan — everything is data added through the Admin UI, so rollback is just "delete/disable the row(s) you added."
- Stopping the local servers (`ollama serve` process / LM Studio "Stop Server") has no effect on the gateway's stored config — the provider rows persist; they'll just fail health checks / return connection errors until the local server is started again.

---

## Open Questions / Risks / Unverified Assumptions

1. **RESOLVED (was `LLMCHAT_ENABLED` assumption).** Confirmed true: code default is `True` (`config.py:1249`) and the live `.env` has no override, so the LLM Chat tab renders. No restart needed; the earlier launchd-restart hedge is dropped.
2. **ASSUMPTION — verify before executing**: exact Admin UI button labels/flow for "add provider" (I read the router/service/schema layer and the nav tabs, not the full `llm_providers_partial.html` form markup or `admin_ui/llmModels.js` click handlers). The field names and endpoint paths are verified from the Python/SQL layer, but the precise click-path in the UI should be confirmed live rather than assumed pixel-for-pixel.
3. **No true multi-model "coordination"/routing exists.** Confirmed by reading `llm_proxy_service.py`'s `chat_completion()`/`_resolve_model()` — it's a single-provider-per-request resolver, not an ensemble or router. If Chad wants genuine multi-model coordination (e.g., "ask both Ollama and LM Studio, compare answers"), that has to be built as new code (a small script or gateway plugin calling `/v1/chat/completions` twice with different `model` values) — this plan does not build that, and flags it as a real gap between "what LLM Chat looks like" and "coordination" as a marketing word.
4. **Ollama has no models.** The actual finding is Chad has never pulled a single Ollama model on this machine. Stage 0 now gives a concrete first pull (`ollama pull llama3.2`, ~2GB) so there's a working model to test with — swap it for any other later. (The read-only *analysis* did not pull anything; the pull is an execution step for Chad, not something done during planning.)
5. **LM Studio's exact reported `model_id` string is unverified** — since the server wasn't running during this analysis, the exact string LM Studio's `/v1/models` will return for the Cydonia model wasn't captured. Stage 2 step 5 explicitly calls this out — fetch it live, don't guess it from the folder name.
6. **Gateway's own `/v1` auth requirements for Stage 3** weren't investigated (whether it needs a virtual/team API key like the rest of the gateway's tool-calling surface, or is open when called from localhost). Treat Stage 3 as optional/exploratory, not required for the core goal (Admin UI → LLM Chat working).
7. Neither local server was left running by this analysis (both were down at read time and were not started, per the read-only constraint) — Stage 0 is a real, not cosmetic, first step.
