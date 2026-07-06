# Gateway configuration plans — for Chad's machine

Three staged, verified config plans to actually *use* the mcp-context-forge gateway
(not just the Work Board page). Each was written by a subagent that inspected the real
code + this machine, then hardened by an adversarial doubt-review pass (Opus, live-tested
against the running gateway). Every "ASSUMPTION" that could be resolved was resolved.

## Recommended order

1. **[config-local-models.md](config-local-models.md)** — connect Ollama + LM Studio to the LLM layer.
   *Start here:* it's the most self-contained (clean slate, zero providers), gives a fast
   visible win (a local model answering in the Test panel), and has no dependency on the others.
2. **[config-mcp-wrapper.md](config-mcp-wrapper.md)** — register your 14 real MCP servers behind the
   one gateway. Bigger payoff, more moving parts (6 register directly, 8 stdio need a `translate`
   bridge each). Do the Stage 0 smoke-test (`descrybe`) first.
3. **[config-plugins.md](config-plugins.md)** — guardrails. Do this LAST and cautiously: a
   mis-staged guardrail can silently block/mutate the traffic the other two set up. Every
   guardrail is staged **observe-only first**.

## Cross-cutting facts (true for all three, verified live)

- **Local writes need no auth.** `AUTH_REQUIRED=false` + `ALLOW_UNAUTHENTICATED_ADMIN=true` on this
  box — admin POST/PATCH/PUT succeed with no login/CSRF (live-tested: unauth POST → 422, not 401).
- **Clean slate.** 0 federated gateways, 0 LLM providers, 43 plugins all `disabled`. Nothing to
  conflict with; everything here is additive and reversible.
- **No gateway restart for data changes.** Registering servers/providers is a DB write — no reload.
  Only plugin YAML edits need a `launchctl kickstart -k gui/501/com.chadkuisel.mcp-gateway`.
- **The gateway does NOT do multi-model "coordination"** — it's a one-model-per-request picker
  (verified in `llm_proxy_service.py`). Real ensemble/routing would be new code.
- **Everything is reversible** — each plan has a rollback section; `mcp.db` is untouched by the
  planning itself (read-only analysis only).

## Provenance

Written 2026-07-06 via 3 parallel analysis subagents + 3 adversarial doubt-reviewers
(doubt-driven-development). Cross-model second opinion offered and declined (Opus reviewers had
already live-verified every load-bearing claim). Findings folded in are marked "CORRECTED" /
"RESOLVED" inline in each plan.
