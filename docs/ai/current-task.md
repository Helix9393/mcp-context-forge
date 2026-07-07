# Current Task

## CURRENT STATE (2026-07-06, session 6) — CourtListener federated + desktop-mcp-sync built/fixed; active-probe in flight

**Done:** Federated CourtListener onto the gateway via OAuth+DCR (one browser login → 16 tools), composed a virtual server (`2bdbd4fbb65f4bdf9c072ea2db37d9eb`, renamed "gateway-hub", now 29 tools with descrybe's 13), and pointed 4 clients' `courtlistener-gw` entries at it. Built `desktop-mcp-sync` (launchd auto-propagation tool at `tools/desktop-mcp-sync/`, UNTRACKED). Ran doubt-driven review that found the tool would silently fail; validated every finding vs source; wrote + user-approved a fix plan (`~/.claude/plans/skip-create-a-plan-stateless-rossum.md`); implemented F1-F13 (Sonnet). Security decision: excluded supabase (deleted its gateway) + github from the unauth hub.

**In flight:** Opus agent `a585486247cf457d3` rebuilding the F4 re-auth health signal as an active probe (the plan's `POST /gateways/{id}/tools/refresh` mechanism was empirically disproven — dead no-op for authorization_code gateways). Result NOT yet in context.

**Deferred / next (user):** restart Claude Code to see gateway-hub; open 2 pending OAuth logins (midpage `.../oauth/authorize/69e38eb77d924ca682b598c4050d1a29`, midpage-legal-research `.../7762399fb4364b199ea42e9d4bf997c5`) → reconcile auto-attaches their tools; commit `tools/desktop-mcp-sync/` after active-probe verified; github needs a manual OAuth app (no DCR) if ever wanted.

## PRIOR STATE (2026-07-06, session 5) — pivot to whole-gateway config; 3 verified plans committed

Scope widened from Work Board to the whole gateway (user: "you've neglected the other pages… mcp wrapper, local models, plugins"). **Done:** Work Board overhaul fast-forward-merged into `main` (`bba67c9c`, NOT pushed). Then produced 3 machine-specific config plans via parallel analysis subagents + adversarial doubt-review (Opus, live-tested), committed `b603f3aa` in `docs/ai/plans/` — `config-local-models.md`, `config-mcp-wrapper.md`, `config-plugins.md`, `README.md` (order: local-models → mcp-wrapper → plugins). Doubt findings folded in (marked CORRECTED/RESOLVED): killed a false login/CSRF prereq, fixed false-fail verify steps, re-staged PII/Secrets guardrails observe-only-first. Cross-model offered → user skipped.

**In flight / NEXT:** execute **Plan 1 (local models)** when user confirms — nothing configured yet (clean slate, all reversible). Fast win: local model answering in the LLM Settings Test panel.

**PARKED (uncommitted, deferred by user's pivot):** (1) Work Board visual optimization pass — indigo priority chips, attention-row POP, tactile press, in-flight spinners, section icons, card separation, dead-`onSelectChange` removed — live in browser, NOT committed. (2) Terminology/effortless-use brainstorm — normalize "needs you"="Needs attention" etc., keep git terms/fix inconsistencies, add discoverability.

## PRIOR STATE (2026-07-06, session 4) — 4-part interactivity overhaul COMPLETE + spec fully implemented + tested

**DONE.** Goal "implement the workflow spec completely, test all features, optimize UI per Chad's preferences" is met. All committed on `feat/work-board-premium-polish`:
- `0bb6e2fe` — **Batch A**: `chip_group` Jinja macro replaces every `<select>` with tap-chips; plain-language renames (Decision, Merge it/Redo on latest/Drop it, New commits/Behind main, Sort this, Start work, Side ideas); color legend; humanized pending badge.
- `14b84183` — **Batch B/C/D + polish**: "Saved ✓" toast + green row flash on real actions (red toast on error); self-updating `#wb-poller` (20s morph, pauses while editing, `live` pulse); inline title edit (click Now/Next/Side-idea title → PATCH `/admin/board/set-title`, Enter/Escape); section landmark icons (🎯⏭️🌿🔀💡🔎); `prefers-reduced-motion` guard.

**Spec status:** original workboard-polish-spec.md Steps 1–11 all live & re-verified (verdict borders, pr_state_badge, ahead/behind numerals, motion/pulse, idiomorph morph, 5s running poll, `work_board_meta` + freshness chip). **Step 12 (drag-to-reorder) intentionally omitted** — user dropped it ("dropdowns + refresh suffice"); spec itself flagged it needs a new `/reorder` endpoint. Step 11 simplified to a plain "git refreshed Xm ago" timestamp (no staleness threshold) per user.

**Tested live via chrome-devtools:** chip patch → toast+flash+data-verdict reconcile; inline rename round-trip; poller morph preserves open notes; poller pause keeps an open editor intact; add-side-idea form; refresh-from-git updates freshness chip "1h ago"→"just now". No console errors. Test artifacts cleaned (deleted test side-idea t-003; reverted PR p-4 verdict + finding f-001 status + their test notes in mcp.db).

**Known no-op (harmless, optional cleanup):** `onSelectChange` in work-board-live.js (Tier-3 optimistic border reflect) targets `<select>` elements that no longer exist (chips replaced them); it never fires now. Border still updates correctly via the fast morph response. Leave or delete — not a bug.

NEXT ACTION: none required. Optional follow-ups: run gateway test suite; delete the dead `onSelectChange`; consider inline-edit for Findings titles too.

## PRIOR STATE (2026-07-06, session 2) — Work Board premium polish: ALL TIERS SHIPPED & VERIFIED

Complete and chrome-devtools-verified: border fix (data-verdict + inline !important, two-class attn specificity); Tier 2 idiomorph morph (self-hosted core+shim; open notes/attention-filter/scroll survive every swap via beforeSwap/afterSwap snapshot in work-board-live.js — NOT defaults.callbacks, which the htmx path ignores); Tier 3 (5s scoped poll on running rows + optimistic verdict reflect via data-verdict); Freshness (WorkBoardMeta model + migration e3d48e0f3f0f applied, refresh_git stamps last_git_refresh, get_board formats neutral "git refreshed Xm ago" label, gray chip in Branches header). Gateway healthy on --reload; no console errors. **Uncommitted** — working-tree only. Optional leftover: spec Step-1 header-comment rewrite (cosmetic). Env gotchas in conversation-log (Jinja no-autoreload; SSE blocks reload; chrome-devtools same-URL navigate is a no-op; idiomorph htmx ignores defaults.callbacks).

## PRIOR STATE (2026-07-06, session 1) — Tier 1 applied, border bug to fix first

Executing the approved 3-tier premium polish for the Work Board admin UI (`http://127.0.0.1:4444/admin#work-board`), from the exact-edit plan at `docs/ai/workboard-polish-spec.md`. **Done this session:** launchd `--reload` added (verified); Tier 1 template edits applied to `mcpgateway/templates/work_board_partial.html` (verdict-color borders, `pr_state_badge` chip macro, divergence numerals, running-row `animate-pulse-soft`, motion-cue `<style>` transitions); `tailwind.config.js` safelist added (drag classes omitted); CSS rebuilt + classes confirmed compiled; template parses; gateway healthy.

**In flight / DO FIRST on resume:** confirmed border bug — verdict left-borders only render on the first row of each table because the tbody `divide-y dark:divide-gray-700` overrides `border-l-4 border-<verdict>` on non-first rows. Fix = `data-verdict` attribute + inline `<style>` `border-left-color !important` rules ordered before `.work-board-item--attn` (colors: land #22c55e, rebase/review #fbbf24, abandon/close #d1d5db, unknown #38bdf8). Then Tier-3 optimistic JS must toggle `data-verdict`, not classes.

**Deferred/dropped (by user):** drag-to-reorder dropped entirely (spec Step 12 + SortableJS skipped); freshness chip = last-refresh time, not staleness cutoff (spec Step 11 modified).

**Remaining:** border fix → Tier 2 (idiomorph morph swap only, Steps 7–8) → Tier 3 (5s poll on running rows + optimistic reflect, Step 9) → Freshness (`work_board_meta` CREATE-TABLE migration `e3d48e0f3f0f` from head `5e72814c91e5`, Steps 10–11 modified). Verify each via chrome-devtools computed `borderLeftColor` per verdict.

## HISTORICAL LOG
