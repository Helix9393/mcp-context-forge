# mcp-context-forge

ContextForge federates MCP/A2A/REST/gRPC tools behind a gateway with RBAC, plugins, and observability.

**Full guidance lives in `AGENTS.md`** (canonical, used by all AI tools — do not duplicate its detail here). Subdirectory docs: `tests/AGENTS.md`, `plugins/AGENTS.md`, `charts/AGENTS.md`, `docs/AGENTS.md`, `mcp-servers/AGENTS.md`, `crates/mcp_runtime/DEVELOPING.md`. Read the relevant one before working in that area. `llms/` is end-user runtime guidance, not for code agents.

## Must-follow invariants

**Security (two-layer model)** — Layer 1 (token scoping) controls visibility; Layer 2 (RBAC) controls actions. Never re-implement team interpretation logic — use `normalize_token_teams()` (API/legacy tokens) or `resolve_session_teams()` (session tokens), both in `mcpgateway/auth.py`. Never accept auth tokens via URL query params. Never trust client-provided ownership fields (`owner_email`, `team_id`). Security-sensitive changes need deny-path regression tests. Full matrix: AGENTS.md "Authentication & RBAC Overview".

**Identity extraction** — email takes precedence over `sub`; use `get_user_email()` in `mcpgateway/auth_context.py`, never re-derive.

**Audit/observability sessions** — `AuditTrailService.log_action()` and observability writes open their own `SessionLocal()`. Never pass the caller's request-scoped `db` session into `log_action()` — it causes "transaction is inactive" errors (issue #2871/#3883).

**Alembic migrations** — always run `alembic heads` first and point `down_revision` at the real current head. Write idempotent upgrades (check `inspector` before adding columns/tables). If `downgrade()` reads `settings`, snapshot those values into `migration_metadata` during `upgrade()` (hermetic downgrade pattern) — see AGENTS.md for the full code pattern.

**Sync SQLAlchemy in async handlers is intentional** — don't flag it as a bug.

## Commands

```bash
make dev                 # dev server :8000
make pre-commit          # after writing code
make ruff bandit interrogate pylint verify   # before committing
```

## Standards

- Python ≥3.11, strict mypy, Ruff (line length 200), `snake_case`/`PascalCase`/`UPPER_CASE`
- Sign commits (`git commit -s`), Conventional Commits, link issues (`Closes #123`)
- Don't push until asked; external-contributor branches — see `todo/force-push.md` first
- detect-secrets false positives: inline `# pragma: allowlist secret` (Python) or `make detect-secrets-scan` (other files)

## Constraints

- Never mention AI assistants in PRs/diffs; no test plans or effort estimates in PRs
- Never create files unless necessary; never proactively create docs
- Never commit secrets
- When auditing repo state, ignore `todo/`, `tmp/`, `artifacts/`, `logs/`, `coverage/` unless asked
- Avoid brittle numeric claims (service/router/middleware/plugin counts) unless actively verifying them
