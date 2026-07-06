# Handoff

## Last Updated: 2026-07-06 (session 4)

## Goal
ContextForge **Work Board** admin UI (`http://127.0.0.1:4444/admin/#work-board`) — premium polish, custodial.

## STATUS: ✅ COMPLETE (session 4)
Original spec (Steps 1–11) + the full 4-part interactivity overhaul are shipped, chrome-devtools-verified, and committed on `feat/work-board-premium-polish` (`0bb6e2fe` Batch A, `14b84183` Batch B/C/D + polish). Step 12 drag intentionally omitted (user-dropped). See `current-task.md` for the verified feature list, test-artifact cleanup, and the one known harmless no-op (`onSelectChange`). Branch is ready to merge (no PR opened — awaiting user). The sections below are the original session-2 plan, kept for reference.

---

## (session 2 plan — reference only, now implemented)
ContextForge Work Board — premium polish, custodial. **The active job now is a big 4-part interactivity overhaul + plain-language pass** the user just approved.

## DONE & COMMITTED this session (branch `feat/work-board-premium-polish`)
- Border fix, Tier 1 signals, Tier 2 idiomorph morph, Tier 3 poll+optimistic reflect, Freshness chip — all shipped & chrome-devtools-verified (commit `29d7c771`, which also force-added the gitignored `tailwind.min.css`).
- **Jump-to-it fix** (commit `cdbe6732`): the anchor changed `location.hash` to `#work-board-<id>`, which the hash-routed admin shell read as a tab switch → bounced to Overview and hid the board. Fixed via `data-wb-jump` attr on the anchor + a delegated click handler in `work-board-live.js` that `preventDefault`s, `scrollIntoView`s in place, and flashes the target indigo. Verified: hash stays `#work-board`, board stays, target flashes.
- Ran a `reasoning-analysis` (Opus) UX critique (agentId `a02124f91daf24c4c`) — findings folded into "Plan" below.

## ACTIVE TASK — 4-part interactivity overhaul (user picked ALL 4 + bundled renames)
User: "i also need a fuck ton more interactivity." Approved all four via AskUserQuestion, plus the plain-language renames come bundled:
1. **Tap-not-type** — replace EVERY `<select>` dropdown with clickable colored chip buttons (tap "Merge it"/"Drop it" directly). One tap = done. This also converts the jargon into plain buttons.
2. **Live feedback + motion** — "Saved ✓" flash after changes, toast pop-ups, button spinners (hx-disabled-elt/hx-indicator), row glow/slide on change, hover highlights, maybe count-up numerals.
3. **Inline editing** — click title/priority/note to edit in place; quick "＋ note" / "✓ mark done" per row. (Title edit needs a NEW endpoint — see below.)
4. **Self-updating board** — auto-poll refresh (whole board, ~15–30s, morphs so open notes/scroll survive), a "live" pulse indicator, new items slide in.

### Endpoint inventory (all EXIST, all take `Form(...)` params — chips drive them via `hx-vals`, NO backend change needed except inline-title)
`mcpgateway/routers/work_board_router.py` `work_board_admin_router` (prefix `/admin/board`):
- POST `/note`, `/tangent`, `/promote`, `/demote`, `/acknowledge`, `/classify`, `/launch`, `/launch-status`, `/refresh`
- PATCH `/set-verdict` (item_id, verdict), `/set-status` (item_id, status alias), `/set-severity` (item_id, severity), `/set-priority` (item_id, priority:int)
- All call `service.update_item(db, item_id, {...}, author="operator")` via `_board_partial_after`. A `<button hx-patch=".../set-verdict" hx-vals='{"item_id":"X","verdict":"land"}' hx-target="#work-board-content-inner" hx-swap="morph:outerHTML transition:true">` works directly.
- **Inline title edit is the ONLY new backend needed:** add `/set-title` (item_id, title) → `service.update_item(db, item_id, {"title": title})`. Confirm `update_item` accepts a `title` key first.

### Batch A design (IN PROGRESS — start here)
Build a reusable Jinja **`chip_group` macro**: params (item_id, endpoint, field_name, options=[{label,value,color}], current_value) → renders a segmented row of chip `<button>`s, active one filled/others outline, each `hx-patch|post` the endpoint with `hx-vals` + morph swap. Then replace each control:
- **Branches verdict** (~line 405, select name=verdict, options land/rebase/abandon/unknown) → chips **Merge it / Redo on latest / Drop it / Not sure yet**.
- **PRs verdict** (~448, options land/review/close/unknown) → **Merge it / Review it / Close it / Not sure yet**.
- **Findings severity** (~538, critical/warning/advisory) → chips; **status** select → chips (`wontfix`→"Won't fix").
- **Tangents status** (~494, parked/promoted/dropped) → chips, add label.
- **NEXT edit-priority** (~317, 1–5 select) → chips or +/- stepper (add "1 = top" hint); **NOW demote priority** (~266) likewise.
- **NEXT promote "Displace NOW to"** (~331, next/tangent/drop) → buttons.

### Bundled plain-language renames (from the critique) — before → after
- `pending_status_badge` macro (~30–59): map raw enum triple → plain, keep glyph. e.g. `impl`→"Code change", `running`→"Running now", `needs_attention`→"Needs you". Currently renders `impl · running · needs_attention`.
- Column **"Verdict"** → **"Decision"** (branches ~384, PRs ~433).
- **"Ahead / Behind"** header (~382) → **"New commits / Behind main"** + hint "red = far behind". (numerals colored at ~393–396.)
- **"Classify"** btn (~85) → **"Sort this"** + `title="Figure out what kind of change this is"`; **"Launch"** (~94) → **"Start work"** + `title="Start an agent working on this"`.
- **"Tangents"** (~467) → **"Side ideas"**, "Add tangent…" → "Add a side idea…".
- **"Promote"** → "Make this the current task" (or keep + tooltip); "Displace NOW to" → "Move current task to"; NOW "Demote → Next"/"Park → Tangent" → "Move down to Next"/"Set aside".
- NOW empty (~288): "Promote a NEXT item." → **"Nothing in progress. Pick something from the Next list below to start."**
- Small enums: `wontfix`→"Won't fix", `advisory`→"FYI"/"low"; label the unlabeled NOW priority select (~266); "Next (≤ 5)" header (~294) less mathy.
- **Add a color legend chip-row** under the "Work Board" heading (~137): 🟩 ready to merge · 🟨 needs work · 🟦 undecided · 🟧 needs you.
- **Consistency bug:** amber attention rail shows whenever `attention != 'acknowledged'` (~73/250/297) but the "Needs attention" chip only renders for `needs_attention` (~166) — color & chip disagree. Make them consistent.

### Batch B/C/D notes
- B: flash the changed row on `htmx:afterSwap` in work-board-live.js (reuse the box-shadow flash helper already added for Jump); add a toast container in admin.html + trigger via `HX-Trigger` response header or client-side; `hx-disabled-elt="this"` on chips for spinners; hover highlight via CSS.
- C: `hx-trigger="every 20s"` on `#work-board-content` wrapper (or a hidden poller) → GET `/admin/board/partial`, morph. Existing beforeSwap/afterSwap guards keep open notes/scroll. A small "● live" pulse chip. Slide-in via a CSS keyframe on newly-added rows.
- D: `/set-title` endpoint + click-to-edit (details/contenteditable or swap-to-input pattern).

## Files (all under `/Users/chadkuisel/Workspace/mcp-context-forge`)
- Template: `mcpgateway/templates/work_board_partial.html` (the whole board; inline `<style>` at ~138).
- Shell: `mcpgateway/templates/admin.html` (script loads ~251, `hx-ext="morph"` wrapper ~4335).
- Liveness JS: `mcpgateway/static/js/work-board-live.js` (morph guards + filter resync + optimistic reflect + Jump handler + a box-shadow flash helper).
- Router: `mcpgateway/routers/work_board_router.py`. Service: `mcpgateway/services/work_board_service.py` (`update_item`, `get_board` ~1292, `refresh_git` ~1444, freshness helpers ~87). Model: `mcpgateway/db_work_board.py` (+ `WorkBoardMeta`).
- Self-hosted: `mcpgateway/static/js/idiomorph.min.js` (core) + `idiomorph-htmx.min.js` (shim). CSS build: `mcpgateway/static/css/tailwind.min.css` (GITIGNORED — force-add or rely on `npm run build:css`); safelist in `tailwind.config.js`.

## HARD environment gotchas (verified — read before touching the live UI)
- **Jinja does NOT auto-reload** (`templates_auto_reload=False`). Template `.html` edits are invisible until the process restarts. `--reload` only watches `.py`. To apply a template edit: `touch /Users/chadkuisel/Workspace/mcp-context-forge/mcpgateway/routers/work_board_router.py` (ABSOLUTE path — persisted cwd is often Atlas-Copilot, so a relative touch silently no-ops).
- **The `/admin/events` SSE from any open admin browser tab blocks the graceful `--reload`** (hangs "Waiting for connections to close" → health 000). BEFORE touching a `.py`: navigate the browser to `about:blank` AND close any extra admin tabs (`close_page`), then touch, then poll health. With SSE dropped it reloads in ~1s.
- **chrome-devtools `navigate_page` to the SAME url (esp. only a `#fragment` differs) is a NO-OP — no reload**, so edited JS/CSS/templates never load into the running page. Always go `about:blank` → target url to force a real reload.
- **idiomorph's htmx path ignores `Idiomorph.defaults.callbacks`** — preserve client state via htmx `beforeSwap`/`afterSwap` snapshot in work-board-live.js, guarded on `targetsBoard(evt.detail.target)`.
- Static files serve `Cache-Control: no-store` (JS/CSS edits need only a real browser reload, no restart). `getComputedStyle` after a bare `setAttribute` can read stale — a `display` toggle forces restyle (only matters for tests; real repaint is automatic).
- Any NEW Tailwind class must be rebuilt: `npm run build:css`, then it's in `tailwind.min.css`. Safelist JS-injected dynamic classes in `tailwind.config.js`. Verify chip colors/borders survive the build.
- Verify each batch via chrome-devtools; confirm `/health` 200 after every `.py`/template restart.

## Blockers
None. Continue Batch A: build the `chip_group` macro, then convert controls + apply renames + legend, rebuild CSS, restart, verify. Then B → C → D. Commit per batch on `feat/work-board-premium-polish`.

## References
- Spec (original polish): `docs/ai/workboard-polish-spec.md`. Full UX critique = this handoff's "renames" + "Batch A" sections (agent `a02124f91daf24c4c`).
