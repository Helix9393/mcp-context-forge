I have everything I need — all target files read at current bytes, the alembic head confirmed (`5e72814c91e5`), the candidate rev id `e3d48e0f3f0f` verified unused, the 18 swap sites enumerated, and the critical `_renumber_next` tiebreak behavior verified. Here is the implementation package.

---

# Work Board Premium Polish — Exact Implementation Package

Repo: `/Users/chadkuisel/Workspace/mcp-context-forge` · Branch: live `--reload` uvicorn. Apply top-to-bottom (safest → riskiest). Every BEFORE matches current bytes on disk.

## Preflight verifications (results)

- **Alembic head** = `5e72814c91e5` (ran `.venv/bin/python -m alembic -c mcpgateway/alembic.ini heads`). Candidate rev `e3d48e0f3f0f` is unused. ✅
- **`hx-swap="outerHTML"`** appears **exactly 18 times** (lines 79, 88, 97, 157, 192, 202, 230, 244, 280, 294, 309, 333, 357, 400, 427, 446, 491, 503). No other `hx-swap` value exists except the doc-comment on line 9. ✅
- **`pr_state` casing**: `refresh_git` stores `gh pr list --json ... state` verbatim (uppercase `OPEN`/`MERGED`/`CLOSED`), service line 1425/1433. Macro must `|lower`. ✅
- **⚠️ `set-priority` cannot express an arbitrary drag reorder — VERIFIED BLOCKER.** `update_item(priority=)` sets the value then calls `_renumber_next` (service line 373-375), and `_renumber_next` (line 130-133) sorts by `(priority.is_(None), priority, id)` — **ties break by `id`, not by intended position**. Simulated A(1),B(2),C(3) → drag C to top → `PATCH C.priority=1` yields `A,C,B` (not `C,A,B`); sequential per-item PATCHes oscillate and never converge, because `_renumber_next` has no knowledge of DOM order. **Consequence:** the design's "SortableJS → existing `/admin/board/set-priority`" assumption does not survive verification. Step 12 therefore adds one small, additive, migration-free `reorder_next` service fn + `/admin/board/reorder` endpoint (full code in §12). This is the single necessary deviation from "reuse the existing endpoint," and it is exactly the `§9.1` contingency the design anticipated.

---

## STEP 1 — Fix the stale header comment (custodial accuracy)

**File:** `mcpgateway/templates/work_board_partial.html`

BEFORE (lines 2-11):
```html
<!--
  Work Board admin partial. Rendered server-side by
  mcpgateway.routers.work_board_router:admin_board_partial (and every
  admin/board/* form endpoint, which returns this same template re-rendered).

  Every control is a plain hx-get/hx-post/hx-patch targeting
  #work-board-content (the wrapper the panel div in admin.html owns) with
  hx-swap="innerHTML", mirroring the ToolOps wrapper pattern -- no inline
  onclick (CSP: @alpinejs/csp bundle).
-->
```

AFTER:
```html
<!--
  Work Board admin partial. Rendered server-side by
  mcpgateway.routers.work_board_router:admin_board_partial (and every
  admin/board/* form endpoint, which returns this same template re-rendered).

  This template's root is #work-board-content-inner. Every control targets
  hx-target="#work-board-content-inner" hx-swap="morph:outerHTML transition:true"
  -- an idiomorph morph swap (hx-ext="morph" is declared once on the stable
  #work-board-content wrapper in admin.html, and inherits down). Morphing (not a
  full outerHTML replace) preserves open <details> notes, the attention filter,
  scroll, and focus across every action. No inline onclick (CSP: @alpinejs/csp).
-->
```

**Verify:** `.venv/bin/python -c "from jinja2 import Environment, FileSystemLoader as F; Environment(loader=F('mcpgateway/templates')).get_template('work_board_partial.html')" && echo OK`
**Rollback:** restore the original comment block.

---

## STEP 2 — Verdict-driven left border on Branches & PRs

**File:** `mcpgateway/templates/work_board_partial.html`

### 2a. Branches row (inside `{% for item in board.branches %}`, line 350)

BEFORE:
```html
          <tr id="work-board-{{ item.id }}" class="work-board-item {{ 'work-board-item--attn' if item.attention != 'acknowledged' else '' }} border-l-4 border-sky-400">
```
AFTER:
```html
          <tr id="work-board-{{ item.id }}" class="work-board-item {{ 'work-board-item--attn' if item.attention != 'acknowledged' else '' }} border-l-4 {% if item.verdict == 'land' %}border-green-500{% elif item.verdict == 'rebase' %}border-amber-400{% elif item.verdict == 'abandon' %}border-gray-300{% else %}border-sky-400{% endif %}">
```

### 2b. PRs row (inside `{% for item in board.prs %}`, line 394 — byte-identical to 350, apply within the PRs loop)

BEFORE:
```html
          <tr id="work-board-{{ item.id }}" class="work-board-item {{ 'work-board-item--attn' if item.attention != 'acknowledged' else '' }} border-l-4 border-sky-400">
```
AFTER:
```html
          <tr id="work-board-{{ item.id }}" class="work-board-item {{ 'work-board-item--attn' if item.attention != 'acknowledged' else '' }} border-l-4 {% if item.verdict == 'land' %}border-green-500{% elif item.verdict == 'review' %}border-amber-400{% elif item.verdict == 'close' %}border-gray-300{% else %}border-sky-400{% endif %}">
```

`unknown` → `border-sky-400` verbatim (restraint guarantee: ~95% of rows unchanged). Only new literal is `border-green-500` (compiled in Step 6).
**Verify:** template parses (Step 1 command); after Step 6 rebuild, set a branch verdict to `land` in the UI → left rail turns green.
**Rollback:** put back `border-l-4 border-sky-400`.

---

## STEP 3 — `pr_state_badge` chip macro (reuses existing green/purple/gray literals)

**File:** `mcpgateway/templates/work_board_partial.html`

### 3a. Define the macro (insert right after the `attention_badge` macro's `{% endmacro %}`, line 161)

BEFORE (lines 158-163):
```html
        Acknowledge
      </button>
    {% endif %}
  {% endmacro %}

  {% macro latest_agent_note(item) %}
```
AFTER:
```html
        Acknowledge
      </button>
    {% endif %}
  {% endmacro %}

  {% macro pr_state_badge(item) %}
    {% set st = (item.pr_state or '')|lower %}
    {% if st == 'open' %}
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200"><span aria-hidden="true">◦</span>OPEN</span>
    {% elif st == 'merged' %}
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-200"><span aria-hidden="true">⇉</span>MERGED</span>
    {% elif st == 'closed' %}
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-700/40 dark:text-gray-300"><span aria-hidden="true">✕</span>CLOSED</span>
    {% else %}
      <span class="text-gray-400 dark:text-gray-500">—</span>
    {% endif %}
  {% endmacro %}

  {% macro latest_agent_note(item) %}
```
(green = the `applied` badge string; purple = the `followup_requested` badge string at line 149; gray = the `advisory` badge string — all already compiled. No new classes.)

### 3b. Use it in the PRs State cell (line 396)

BEFORE:
```html
            <td class="px-3 py-2 dark:text-gray-300">{{ item.pr_state or '—' }}</td>
```
AFTER:
```html
            <td class="px-3 py-2">{{ pr_state_badge(item) }}</td>
```
**Verify:** template parses; a MERGED PR row shows a purple `⇉ MERGED` chip.
**Rollback:** delete the macro block and restore the bare-text `<td>`.

---

## STEP 4 — Ahead/Behind colored numerals (amber-text rung 4; red ≥ 200; green ahead)

**File:** `mcpgateway/templates/work_board_partial.html`

BEFORE (line 352):
```html
            <td class="px-3 py-2 dark:text-gray-300">{{ item.git_ahead if item.git_ahead is not none else '—' }} / {{ item.git_behind if item.git_behind is not none else '—' }}</td>
```
AFTER:
```html
            <td class="px-3 py-2">
              {% set _a = item.git_ahead %}{% set _b = item.git_behind %}
              <span class="transition-colors duration-200 {% if _a is not none and _a > 0 %}text-green-600 dark:text-green-400{% else %}text-gray-400 dark:text-gray-500{% endif %}">{{ _a if _a is not none else '—' }}</span>
              <span class="text-gray-400 dark:text-gray-500"> / </span>
              <span class="transition-colors duration-200 {% if _b is not none and _b >= 200 %}text-red-600 dark:text-red-400{% elif _b is not none and _b > 0 %}text-amber-600 dark:text-amber-400{% else %}text-gray-400 dark:text-gray-500{% endif %}">{{ _b if _b is not none else '—' }}</span>
            </td>
```
Divergence amber is **text-only** (ladder rung 4) — never a rail/pill, so it cannot compete with the attention rail or the running pill. New literals compiled in Step 6.
**Verify:** a branch with `behind` between 1-199 shows an amber number; ≥200 shows red; `ahead>0` shows green.
**Rollback:** restore the single-line `<td>`.

---

## STEP 5 — Motion cue + running-row pulse (no page-wide animation)

**File:** `mcpgateway/templates/work_board_partial.html`

### 5a. Transition rule in the existing inline `<style>` (zero Tailwind/purge risk)

BEFORE (lines 138-143):
```html
  <style>
    /* Pure-CSS filter toggle: hide anything not carrying the needs-attention marker. */
    .work-board-filter-attention .work-board-item:not(.work-board-item--attn) { display: none; }
    /* Attention rail: needs-attention items get a strong amber left edge regardless of lane. */
    .work-board-item--attn { border-left-color: #f59e0b !important; }
  </style>
```
AFTER:
```html
  <style>
    /* Pure-CSS filter toggle: hide anything not carrying the needs-attention marker. */
    .work-board-filter-attention .work-board-item:not(.work-board-item--attn) { display: none; }
    /* Attention rail: needs-attention items get a strong amber left edge regardless of lane. */
    .work-board-item--attn { border-left-color: #f59e0b !important; }
    /* Motion as cue: animate left-border/badge recolors (verdict flips, morph patches). */
    .work-board-item { transition: border-color .2s ease, background-color .2s ease; }
    .work-board-item .inline-flex { transition: color .2s ease, background-color .2s ease; }
  </style>
```

### 5b. `animate-pulse-soft` on running pending rows only

BEFORE (line 68):
```html
          <tr id="work-board-pending-{{ item.id }}" class="work-board-item {{ 'work-board-item--attn' if item.attention != 'acknowledged' else '' }}">
```
AFTER:
```html
          <tr id="work-board-pending-{{ item.id }}" class="work-board-item {{ 'work-board-item--attn' if item.attention != 'acknowledged' else '' }}{% if item.run_state == 'running' %} animate-pulse-soft{% endif %}">
```
(`animate-pulse-soft` is defined in `tailwind.config.js` `theme.extend.animation`; JIT compiles it once the literal is in the template + Step 6 rebuild.)
**Verify:** a `running` pending row breathes softly; no other row animates.
**Rollback:** remove the `<style>` additions and the `{% if item.run_state == 'running' %}` fragment.

---

## STEP 6 — Rebuild CSS + safelist (gate for Steps 2-5, 12)

### 6a. Safelist the dynamically-branched color/utility literals

**File:** `tailwind.config.js`

BEFORE (lines 6-8):
```js
    ],
    darkMode: "class",
    theme: {
```
AFTER:
```js
    ],
    darkMode: "class",
    safelist: [
        // Verdict / severity left-border colors (Jinja if/elif + JS optimistic reflect)
        "border-green-500", "border-amber-400", "border-sky-400", "border-gray-300", "border-red-500",
        // Ahead/behind divergence numerals
        "text-green-600", "dark:text-green-400",
        "text-amber-600", "dark:text-amber-400",
        "text-red-600", "dark:text-red-400",
        "text-gray-400", "dark:text-gray-500",
        // Running-row pulse + drag handle affordance
        "animate-pulse-soft",
        "cursor-grab", "opacity-0", "group-hover:opacity-100", "transition-opacity",
    ],
    theme: {
```

### 6b. Rebuild

```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && npm run build:css 2>&1 | tail -5
```

**Verify (token check):**
```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && for c in 'border-green-500' 'text-amber-600' 'text-red-600' 'text-green-600' 'animate-pulse-soft' 'cursor-grab'; do printf '%s: ' "$c"; grep -c "$c" mcpgateway/static/css/tailwind.min.css; done
```
Every count must be ≥ 1.
**Rollback:** remove the `safelist` block, re-run `npm run build:css`.

---

## STEP 7 — Morph foundation (Tier-2: state survives every click)

### 7a. Self-host the libraries

```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge/mcpgateway/static/js
curl -fSL -o idiomorph-htmx.min.js https://cdn.jsdelivr.net/npm/idiomorph@0.7.3/dist/idiomorph-htmx.min.js
curl -fSL -o sortable.min.js       https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js
# sanity: idiomorph must self-register the htmx morph extension; sortable must export Sortable
grep -c "defineExtension" idiomorph-htmx.min.js   # >= 1
grep -c "Sortable" sortable.min.js                # >= 1
```
(`static/js/*.js` is already covered by CSP `script-src-elem 'self'` — no nonce, no CSP edit, per the gantt-chart.js/flame-graph.js precedent. `static/js/*.js` is also a Tailwind content glob — see Step 8/12 note.)

### 7b. Load them + `work-board-live.js` after the bundle (admin.html)

**File:** `mcpgateway/templates/admin.html`

BEFORE (lines 251-252):
```html
    <script defer src="{{ root_path }}/static/{{ bundle_js }}"></script>
    {% if is_admin %}
```
AFTER:
```html
    <script defer src="{{ root_path }}/static/{{ bundle_js }}"></script>
    {% if work_board_enabled %}
    <!-- Work Board liveness: idiomorph (htmx morph swap) + SortableJS (drag) + delegated optimistic reflect.
         Self-hosted under /static/js (CSP 'self' covers these; no nonce). `defer` + document order AFTER
         the bundle guarantees window.htmx exists when idiomorph runs htmx.defineExtension('morph', ...). -->
    <script defer src="{{ root_path }}/static/js/idiomorph-htmx.min.js"></script>
    <script defer src="{{ root_path }}/static/js/sortable.min.js"></script>
    <script defer src="{{ root_path }}/static/js/work-board-live.js"></script>
    {% endif %}
    {% if is_admin %}
```

### 7c. Declare `hx-ext="morph"` once on the stable wrapper (admin.html)

BEFORE (line 4330):
```html
        <div id="work-board-content" hx-get="{{ root_path }}/admin/board/partial" hx-trigger="load" hx-swap="innerHTML">
```
AFTER:
```html
        <div id="work-board-content" hx-ext="morph" hx-get="{{ root_path }}/admin/board/partial" hx-trigger="load" hx-swap="innerHTML">
```
(This wrapper is never swapped, so `hx-ext` survives every morph and inherits to all 18 controls. Its own `hx-swap="innerHTML"` for the one-time lazy load is left as-is.)

### 7d. Upgrade all 18 swap sites (do this LAST among template edits — a single global replace)

**File:** `mcpgateway/templates/work_board_partial.html`
Replace **every** occurrence (18×) of the exact string:
```
hx-swap="outerHTML"
```
with:
```
hx-swap="morph:outerHTML transition:true"
```
Command (idempotent, count-checked):
```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && \
perl -pi -e 's/hx-swap="outerHTML"/hx-swap="morph:outerHTML transition:true"/g' mcpgateway/templates/work_board_partial.html && \
echo "remaining plain outerHTML swaps:" && grep -c 'hx-swap="outerHTML"' mcpgateway/templates/work_board_partial.html && \
echo "morph swaps:" && grep -c 'hx-swap="morph:outerHTML transition:true"' mcpgateway/templates/work_board_partial.html
```
Expect: remaining `0`, morph `18`. (`transition:true` uses htmx's native `document.startViewTransition`; unsupported browsers silently no-op — safe.)

**Verify (whole step):** reload `http://127.0.0.1:4444/admin#work-board`. Open a Notes `<details>`, tick "Needs attention only", scroll down, then change any select → notes stay open, filter stays on, scroll holds, only the changed region cross-fades.
**Rollback:** reverse 7d (`s/morph:outerHTML transition:true/outerHTML/`), remove `hx-ext="morph"` (7c), remove the three script tags (7b), delete the two downloaded JS files. (The board falls back to full-swap behavior, still functional.)

---

## STEP 8 — `work-board-live.js` (Tier-2 morph guards + Tier-3 optimistic reflect, filter persistence, drag)

**New file:** `mcpgateway/static/js/work-board-live.js` — full contents:

```js
/* Work Board liveness (Tier 2 + Tier 3).
 * Self-hosted static file (CSP 'self' covers it; no inline JS, no eval).
 * Loads after idiomorph-htmx.min.js, sortable.min.js and the admin bundle (window.htmx).
 * Every Tailwind class string below is a full literal (also safelisted) so JIT purge keeps it.
 */
(function () {
  "use strict";

  // ----- Tier 2: teach morph to preserve state the server can't know about -----
  // idiomorph morphs attributes to match the incoming server HTML. The server always
  // renders <details> collapsed, so without this guard every morph would collapse open
  // notes -- the exact #1 problem this work exists to fix. Keep the live open/closed state.
  function installMorphGuards() {
    var I = window.Idiomorph;
    if (!I || !I.defaults) return false;
    I.defaults.callbacks = I.defaults.callbacks || {};
    var prev = I.defaults.callbacks.beforeAttributeUpdated;
    I.defaults.callbacks.beforeAttributeUpdated = function (attrName, node, mutationType) {
      if (node.tagName === "DETAILS" && attrName === "open") return false;
      if (typeof prev === "function") return prev(attrName, node, mutationType);
      return undefined;
    };
    return true;
  }
  if (!installMorphGuards()) {
    document.addEventListener("DOMContentLoaded", installMorphGuards);
  }

  // ----- Tier 3a: optimistic left-border reflection on verdict/severity change -----
  var VERDICT_BORDER = {
    land: "border-green-500", rebase: "border-amber-400", review: "border-amber-400",
    abandon: "border-gray-300", close: "border-gray-300", unknown: "border-sky-400",
  };
  var SEVERITY_BORDER = {
    critical: "border-red-500", warning: "border-amber-400", advisory: "border-gray-300",
  };
  var ALL_BORDER_COLORS = [
    "border-green-500", "border-amber-400", "border-gray-300", "border-sky-400", "border-red-500",
  ];
  function reflectBorder(rowEl, newClass) {
    if (!rowEl || !newClass) return;
    ALL_BORDER_COLORS.forEach(function (c) { rowEl.classList.remove(c); });
    rowEl.classList.add(newClass);
  }
  function onSelectChange(ev) {
    var sel = ev.target;
    if (!sel || sel.tagName !== "SELECT") return;
    var row = sel.closest(".work-board-item");
    if (!row) return;
    if (sel.name === "verdict") reflectBorder(row, VERDICT_BORDER[sel.value]);
    else if (sel.name === "severity") reflectBorder(row, SEVERITY_BORDER[sel.value]);
    // 'status' has no left-border mapping (tangent border is fixed purple; finding border
    // tracks severity, not status) -> the morph response reconciles it; nothing to pre-flip.
  }

  // ----- Tier 3b: attention-filter persistence across morph -----
  // The filter class now lives on the stable #work-board-content wrapper (never morphed).
  // The checkbox itself lives inside the morphed partial and re-renders unchecked, so
  // re-sync its checked state from the wrapper after every settle.
  function resyncAttentionFilter() {
    var cb = document.getElementById("work-board-attention-filter");
    var wrap = document.getElementById("work-board-content");
    if (cb && wrap) cb.checked = wrap.classList.contains("work-board-filter-attention");
  }

  // ----- Tier 3c: drag-to-reorder NEXT (SortableJS -> /admin/board/reorder) -----
  function boardRoot() {
    var w = document.getElementById("work-board-content");
    var g = (w && w.getAttribute("hx-get")) || "";
    return g.replace(/\/admin\/board\/partial.*$/, "");
  }
  function initSortable() {
    var list = document.getElementById("work-board-next-list");
    if (!list || !window.Sortable) return;
    if (list._wbSortable) { list._wbSortable.destroy(); list._wbSortable = null; }
    list._wbSortable = window.Sortable.create(list, {
      handle: ".wb-drag-handle",
      animation: 150,
      onEnd: function () {
        var ids = Array.prototype.map.call(
          list.querySelectorAll("[data-wb-id]"),
          function (el) { return el.getAttribute("data-wb-id"); }
        ).filter(Boolean);
        if (!ids.length || !window.htmx) return;
        // htmx.ajax rides the same CSRF path as every other control; the morph swap keeps
        // open notes/scroll. SortableJS's own DOM move is the optimistic reflection.
        window.htmx.ajax("POST", boardRoot() + "/admin/board/reorder", {
          source: list,
          target: "#work-board-content-inner",
          swap: "morph:outerHTML transition:true",
          values: { ordered_ids: ids.join(",") },
        });
      },
    });
  }

  // Delegated change listener on the stable, never-morphed ancestor (no re-binding needed).
  var stable = document.getElementById("work-board-content");
  if (stable) stable.addEventListener("change", onSelectChange);

  // Re-run per-render wiring after the partial first loads and after every morph swap.
  document.body.addEventListener("htmx:afterSettle", function () { initSortable(); resyncAttentionFilter(); });
  document.body.addEventListener("htmx:load", function () { initSortable(); resyncAttentionFilter(); });
})();
```

### 8b. Retarget the attention-filter checkbox to the stable wrapper (morph-safe filter)

**File:** `mcpgateway/templates/work_board_partial.html`

BEFORE (lines 132-133):
```html
      <input type="checkbox" id="work-board-attention-filter" class="h-4 w-4 rounded border-gray-300 text-indigo-600"
             onchange="document.getElementById('work-board-content-inner').classList.toggle('work-board-filter-attention', this.checked)">
```
AFTER:
```html
      <input type="checkbox" id="work-board-attention-filter" class="h-4 w-4 rounded border-gray-300 text-indigo-600"
             onchange="document.getElementById('work-board-content').classList.toggle('work-board-filter-attention', this.checked)">
```
Why: the class now lives on `#work-board-content` (the stable ancestor morph never replaces), so the filter can't be wiped by a swap. The existing CSS selector `.work-board-filter-attention .work-board-item:not(...)` still matches (descendant combinator; the class just moved up one node). The inline `onchange` remains CSP-legal via `script-src-attr 'unsafe-inline'`.

**Verify:** with a select-change causing a morph, an open note stays open; tick the filter then trigger any mutation → filter stays applied and the checkbox stays ticked; change a verdict select → the rail recolors instantly (before the network round-trip).
**Rollback:** delete `work-board-live.js`, restore the checkbox `onchange` to target `#work-board-content-inner`.

---

## STEP 9 — Scoped poll on running pending rows only

**File:** `mcpgateway/templates/work_board_partial.html`
The "Refresh status" button (lines 93-99) renders **only** inside `{% if item.run_state == 'running' %}` (line 92), so this poll is inherently scoped to running rows — never board-wide.

BEFORE (lines 95-96):
```html
                        hx-post="{{ root_path }}/admin/board/launch-status"
                        hx-vals='{"item_id": "{{ item.id }}"}'
```
AFTER:
```html
                        hx-post="{{ root_path }}/admin/board/launch-status"
                        hx-trigger="click, every 5s"
                        hx-vals='{"item_id": "{{ item.id }}"}'
```
(`click` retained so the button still works manually; `every 5s` is fixed, non-compounding, per row. Do this edit against original bytes — it does not touch the `hx-swap="outerHTML"` on line 97, which the Step 7d global pass upgrades.)
**Verify:** launch an impl item → its row auto-refreshes every 5s with no board-wide reflow; idle rows never poll.
**Rollback:** remove the `hx-trigger` line.

---

## STEP 10 — `work_board_meta` model + alembic migration (highest crash blast — save exactly)

### 10a. Model addition

**File:** `mcpgateway/db_work_board.py` (all needed imports — `String`, `Text`, `DateTime`, `text`, `utc_now`, `Mapped`, `mapped_column`, `Optional`, `datetime` — are already imported at top).

BEFORE (lines 167-173, end of file):
```python
    def __repr__(self) -> str:
        """String representation.

        Returns:
            str: String representation of the WorkBoardNote instance.
        """
        return f"<WorkBoardNote(id={self.id}, item_id='{self.item_id}', author='{self.author}')>"
```
AFTER:
```python
    def __repr__(self) -> str:
        """String representation.

        Returns:
            str: String representation of the WorkBoardNote instance.
        """
        return f"<WorkBoardNote(id={self.id}, item_id='{self.item_id}', author='{self.author}')>"


class WorkBoardMeta(Base):
    """Freeform key/value store for board-level metadata that belongs to no single item.

    Used for cross-item facts such as the last git-refresh timestamp (key
    ``last_git_refresh``), surfaced as the Branches-header freshness chip. Values are
    opaque strings written/read only by the service layer; there is no enum to enforce,
    so no CHECK constraint is needed (and none could be ALTER-added on SQLite anyway).
    """

    __tablename__ = "work_board_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    def __repr__(self) -> str:
        """String representation.

        Returns:
            str: String representation of the WorkBoardMeta instance.
        """
        return f"<WorkBoardMeta(key='{self.key}')>"
```

### 10b. Migration — **new file** `mcpgateway/alembic/versions/e3d48e0f3f0f_add_work_board_meta_table.py`

> Re-run `.venv/bin/python -m alembic -c mcpgateway/alembic.ini heads` **immediately before saving** to confirm head is still `5e72814c91e5` and `e3d48e0f3f0f` is still unused.

Full contents:
```python
# -*- coding: utf-8 -*-
"""Location: ./mcpgateway/alembic/versions/e3d48e0f3f0f_add_work_board_meta_table.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

e3d48e0f3f0f_add_work_board_meta_table

Revision ID: e3d48e0f3f0f
Revises: 5e72814c91e5
Create Date: 2026-07-05 00:00:00.000000

Creates ``work_board_meta`` -- a generic key/value table for board-level metadata
that does not belong on any single ``work_board_items`` row (first consumer: the
``last_git_refresh`` timestamp behind the Branches-header freshness chip).

This is a fresh CREATE TABLE, not an ALTER, so the SQLite ALTER-add CHECK-constraint
limitation does not apply -- and none is needed anyway (freeform value column). Any
value-shape enforcement stays in the service layer, matching the work-board convention.
"""

# Standard
from typing import Sequence, Union

# Third-Party
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e3d48e0f3f0f"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "5e72814c91e5"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the work_board_meta key/value table (idempotent guard for re-runs)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "work_board_meta" not in existing_tables:
        op.create_table(
            "work_board_meta",
            sa.Column("key", sa.String(64), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        )


def downgrade() -> None:
    """Drop the work_board_meta table (non-fatal warning on failure, house style)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "work_board_meta" in existing_tables:
        try:
            op.drop_table("work_board_meta")
        except Exception as e:  # pylint: disable=broad-except
            print(f"Warning: Could not drop table work_board_meta: {e}")
```

**Verify:**
```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && \
.venv/bin/python -c "import ast; ast.parse(open('mcpgateway/alembic/versions/e3d48e0f3f0f_add_work_board_meta_table.py').read()); print('syntax OK')" && \
.venv/bin/python -m alembic -c mcpgateway/alembic.ini heads   # -> e3d48e0f3f0f (head)
```
The always-on service runs `alembic upgrade head` on its next `--reload`; confirm the table lands:
```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && .venv/bin/python -c "import sqlite3; print([r[0] for r in sqlite3.connect('mcp.db').execute(\"select name from sqlite_master where type='table' and name='work_board_meta'\")])"
```
**Rollback:** `.venv/bin/python -m alembic -c mcpgateway/alembic.ini downgrade 5e72814c91e5`, delete the migration file, revert the model addition. (Do the alembic downgrade **before** deleting the file.)

---

## STEP 11 — Freshness timestamp wiring (service + template chip)

**File:** `mcpgateway/services/work_board_service.py`

### 11a. Import the new model (line 35)

BEFORE:
```python
from mcpgateway.db_work_board import WorkBoardItem, WorkBoardNote
```
AFTER:
```python
from mcpgateway.db_work_board import WorkBoardItem, WorkBoardMeta, WorkBoardNote
```

### 11b. Staleness constant (after line 53 `_NEXT_LANE_CAP = 5`)

BEFORE:
```python
_NEXT_LANE_CAP = 5
```
AFTER:
```python
_NEXT_LANE_CAP = 5

# Freshness threshold for the Branches "git refreshed Xm ago" chip: amber past this many minutes.
# FLAG (owner-tunable): confirm 30 with Chad before treating as final (judge-flagged as unverified).
_GIT_STALE_MINUTES = 30
```

### 11c. Meta helpers (insert after `_today()`, i.e. after line 86)

BEFORE (lines 80-87):
```python
def _today() -> str:
    """Return today's date as an ISO ``YYYY-MM-DD`` string.

    Returns:
        str: Today's date in ISO 8601 date format (UTC).
    """
    return datetime.now(timezone.utc).date().isoformat()

```
AFTER:
```python
def _today() -> str:
    """Return today's date as an ISO ``YYYY-MM-DD`` string.

    Returns:
        str: Today's date in ISO 8601 date format (UTC).
    """
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (for work_board_meta values).

    Returns:
        str: Current UTC time, ISO-8601 with offset.
    """
    return datetime.now(timezone.utc).isoformat()


def _get_meta(db: Session, key: str) -> Optional[str]:
    """Return the stored value for a work_board_meta key, or None if unset.

    Args:
        db: SQLAlchemy session.
        key: Meta key.

    Returns:
        Optional[str]: The stored value, or None.
    """
    row = db.query(WorkBoardMeta).filter(WorkBoardMeta.key == key).one_or_none()
    return row.value if row is not None else None


def _set_meta(db: Session, key: str, value: str) -> None:
    """Upsert a freeform key/value into work_board_meta (caller commits).

    Args:
        db: SQLAlchemy session.
        key: Meta key.
        value: Opaque string value.
    """
    row = db.query(WorkBoardMeta).filter(WorkBoardMeta.key == key).one_or_none()
    if row is None:
        db.add(WorkBoardMeta(key=key, value=value))
    else:
        row.value = value

```

### 11d. `refresh_git` writes the timestamp on the success path (lines 1443-1444)

BEFORE:
```python
    db.commit()
    return {"refreshed": True, "branches_updated": branches_updated, "prs_updated": prs_updated}
```
AFTER:
```python
    _set_meta(db, "last_git_refresh", _now_iso())
    db.commit()
    return {"refreshed": True, "branches_updated": branches_updated, "prs_updated": prs_updated}
```
(Only the real-refresh success path is stamped — the early soft-fail `return`s at line 1344/1349 correctly leave the timestamp untouched.)

### 11e. `get_board` reads it back (lines 1292-1303)

BEFORE:
```python
    updated = max((i.updated_at for i in all_items), default=None)

    return {
        "now": _item_to_dict(now_item) if now_item else None,
        "next": [_item_to_dict(i) for i in next_items],
        "branches": [_item_to_dict(i) for i in branches],
        "prs": [_item_to_dict(i) for i in prs],
        "tangents": [_item_to_dict(i) for i in tangents],
        "findings": [_item_to_dict(i) for i in findings],
        "next_move": next_move(db),
        "updated": updated,
    }
```
AFTER:
```python
    updated = max((i.updated_at for i in all_items), default=None)

    git_refreshed_iso = _get_meta(db, "last_git_refresh")
    git_refreshed_at = None
    git_refreshed_age_min = None
    if git_refreshed_iso:
        try:
            _ts = datetime.fromisoformat(git_refreshed_iso)
            git_refreshed_at = _ts
            git_refreshed_age_min = max(0, int((datetime.now(timezone.utc) - _ts).total_seconds() // 60))
        except ValueError:
            pass

    return {
        "now": _item_to_dict(now_item) if now_item else None,
        "next": [_item_to_dict(i) for i in next_items],
        "branches": [_item_to_dict(i) for i in branches],
        "prs": [_item_to_dict(i) for i in prs],
        "tangents": [_item_to_dict(i) for i in tangents],
        "findings": [_item_to_dict(i) for i in findings],
        "next_move": next_move(db),
        "updated": updated,
        "git_refreshed_at": git_refreshed_at,
        "git_refreshed_age_min": git_refreshed_age_min,
        "git_stale": (git_refreshed_age_min is not None and git_refreshed_age_min >= _GIT_STALE_MINUTES),
    }
```

### 11f. Freshness chip in the Branches header

**File:** `mcpgateway/templates/work_board_partial.html`

BEFORE (lines 328-336):
```html
    <div class="flex items-center justify-between mb-2">
      <h3 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">Branches</h3>
      <button type="button"
              class="text-xs font-medium px-2 py-1 rounded-md bg-gray-100 text-gray-800 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-200"
              hx-post="{{ root_path }}/admin/board/refresh"
              hx-target="#work-board-content-inner" hx-swap="outerHTML">
        Refresh from git
      </button>
    </div>
```
AFTER:
```html
    <div class="flex items-center justify-between mb-2">
      <h3 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">Branches</h3>
      <div class="flex items-center gap-2">
        {% set _age = board.git_refreshed_age_min %}
        {% if _age is none %}
        <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-700/40 dark:text-gray-300">never refreshed</span>
        {% elif board.git_stale %}
        <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">git refreshed {{ _age }}m ago</span>
        {% else %}
        <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-700/40 dark:text-gray-300">git refreshed {{ _age }}m ago</span>
        {% endif %}
        <button type="button"
                class="text-xs font-medium px-2 py-1 rounded-md bg-gray-100 text-gray-800 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-200"
                hx-post="{{ root_path }}/admin/board/refresh"
                hx-target="#work-board-content-inner" hx-swap="outerHTML"
                hx-disabled-elt="this">
          Refresh from git
        </button>
      </div>
    </div>
```
(Chip reuses the already-compiled gray `advisory` and amber `needs-attention` badge strings — no new CSS. The button's `hx-swap="outerHTML"` here is only present if you apply 11f **before** the Step 7d global pass; if applied after, write it as `hx-swap="morph:outerHTML transition:true"`. Recommended order: apply granular edits first, run 7d last.)

**Verify:** click "Refresh from git" → chip reads `git refreshed 0m ago` in gray; the button disables during the request; after 30+ simulated minutes the chip renders amber. Confirm the JSON API is unbroken:
```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && .venv/bin/python -c "import ast; ast.parse(open('mcpgateway/services/work_board_service.py').read()); print('service syntax OK')"
```
**Rollback:** revert 11a-11f in reverse; the chip simply disappears and the timestamp is unused.

---

## STEP 12 — Drag-to-reorder NEXT (gated on the verified `set-priority` blocker)

Because `_renumber_next` tiebreaks by `id` (preflight above), drag needs an explicit-order write. This adds one additive service fn + one endpoint — **no migration, no schema change, `--reload`-safe.**

### 12a. Service `reorder_next` (insert after `_renumber_next`, i.e. after line 134)

**File:** `mcpgateway/services/work_board_service.py`

BEFORE (lines 130-134):
```python
    items = db.query(WorkBoardItem).filter(WorkBoardItem.lane == "next").order_by(WorkBoardItem.priority.is_(None), WorkBoardItem.priority, WorkBoardItem.id).all()
    for idx, item in enumerate(items, start=1):
        if item.priority != idx:
            item.priority = idx

```
AFTER:
```python
    items = db.query(WorkBoardItem).filter(WorkBoardItem.lane == "next").order_by(WorkBoardItem.priority.is_(None), WorkBoardItem.priority, WorkBoardItem.id).all()
    for idx, item in enumerate(items, start=1):
        if item.priority != idx:
            item.priority = idx


def reorder_next(db: Session, ordered_ids: List[str]) -> None:
    """Assign dense 1..n priority to NEXT-lane items in the exact given id order.

    Unlike ``update_item(priority=...)`` + ``_renumber_next`` (which breaks ties by ``id``,
    not by intended position, and so cannot express an arbitrary drag reorder), this writes
    the caller's explicit order directly. Ids not in the NEXT lane are ignored; NEXT items
    the caller omitted are appended after, stable by (priority, id).

    Args:
        db: SQLAlchemy session.
        ordered_ids: NEXT-lane item ids in their new top-to-bottom order.
    """
    next_items = db.query(WorkBoardItem).filter(WorkBoardItem.lane == "next").all()
    by_id = {i.id: i for i in next_items}
    seq = 1
    seen = set()
    for item_id in ordered_ids:
        it = by_id.get(item_id)
        if it is not None and it.id not in seen:
            it.priority = seq
            seq += 1
            seen.add(it.id)
    for it in sorted((i for i in next_items if i.id not in seen), key=lambda i: (i.priority if i.priority is not None else 999, i.id)):
        it.priority = seq
        seq += 1
    db.commit()

```

### 12b. Router `/admin/board/reorder` (insert after `admin_refresh_git`, before the `# NOTE: __all__` block, ~line 984)

**File:** `mcpgateway/routers/work_board_router.py`

BEFORE (lines 982-987):
```python
    service.refresh_git(db, settings.work_board_git_repo)
    return _render_board_partial(request, db)


# NOTE: __all__ documents the frozen enum vocabulary re-exported for the MCP
# tool-registration script (§4) and the admin partial template context.
```
AFTER:
```python
    service.refresh_git(db, settings.work_board_git_repo)
    return _render_board_partial(request, db)


@work_board_admin_router.post("/reorder", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_reorder_next(
    request: Request,
    ordered_ids: str = Form(...),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Reorder the NEXT lane to an explicit id order (SortableJS drag-to-reorder drop).

    The single ``set-priority`` endpoint cannot express an arbitrary reorder because
    ``_renumber_next`` breaks priority ties by ``id`` rather than by dropped position; this
    endpoint writes the client's explicit top-to-bottom order via ``service.reorder_next``.

    Args:
        request: Incoming request.
        ordered_ids: Comma-separated NEXT-lane item ids in their new order.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial.
    """
    ids = [s for s in ordered_ids.split(",") if s]
    return _board_partial_after(request, db, lambda: service.reorder_next(db, ids))


# NOTE: __all__ documents the frozen enum vocabulary re-exported for the MCP
# tool-registration script (§4) and the admin partial template context.
```

### 12c. NEXT list container id + row `data-wb-id` + drag handle (template)

**File:** `mcpgateway/templates/work_board_partial.html`

(i) Container (lines 261-263):
BEFORE:
```html
    <h3 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">Next (&le; 5)</h3>
    <div class="space-y-1.5">
      {% for item in board.next %}
```
AFTER:
```html
    <h3 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">Next (&le; 5)</h3>
    <div class="space-y-1.5" id="work-board-next-list">
      {% for item in board.next %}
```

(ii) Row `data-wb-id` (line 264):
BEFORE:
```html
      <div id="work-board-{{ item.id }}" class="work-board-item group {{ 'work-board-item--attn' if item.attention != 'acknowledged' else '' }} border-l-4 border-gray-300 rounded-md px-3 py-2 dark:bg-gray-800">
```
AFTER:
```html
      <div id="work-board-{{ item.id }}" data-wb-id="{{ item.id }}" class="work-board-item group {{ 'work-board-item--attn' if item.attention != 'acknowledged' else '' }} border-l-4 border-gray-300 rounded-md px-3 py-2 dark:bg-gray-800">
```

(iii) Drag handle beside the existing priority picker (lines 273-274) — additive, keyboard/`<select>` path untouched ("click over type"):
BEFORE:
```html
        <div class="mt-3 flex flex-wrap items-center gap-2">
          <details class="inline-block">
```
AFTER:
```html
        <div class="mt-3 flex flex-wrap items-center gap-2">
          <span class="wb-drag-handle cursor-grab select-none px-1 text-gray-300 dark:text-gray-600 opacity-0 group-hover:opacity-100 transition-opacity" title="Drag to reorder" aria-hidden="true">⠿</span>
          <details class="inline-block">
```

**Verify:** hover a NEXT row → grip appears; drag it above another and drop → order persists after the morph (open notes stay open); reload → server order matches. Then trigger an unrelated mutation and confirm drag still works after the morph re-diff (afterSettle re-inits Sortable). Syntax gates:
```bash
cd /Users/chadkuisel/Workspace/mcp-context-forge && .venv/bin/python -c "import ast; ast.parse(open('mcpgateway/routers/work_board_router.py').read()); ast.parse(open('mcpgateway/services/work_board_service.py').read()); print('py OK')"
```
**Rollback:** remove the `/reorder` endpoint (12b), `reorder_next` (12a), the container id/`data-wb-id`/handle (12c). The `<select>` priority editor keeps working.

---

## Final end-to-end verification (chrome-devtools, per tier)

Open `http://127.0.0.1:4444/admin`, click the Work Board tab, DevTools console open (watch for CSP violations / 404s on the three `/static/js/*.js` files).

**Tier 1 — signals & polish**
1. `take_snapshot` the Branches/PRs tables. Set a branch verdict `unknown→land` → left rail green (cross-fades, not hard-cut). Set `land→rebase` → amber. Confirm `unknown` rows are still sky (`border-sky-400`).
2. A PR that is MERGED shows a purple `⇉ MERGED` chip; OPEN → green `◦ OPEN`; CLOSED → gray `✕ CLOSED`.
3. A branch with `behind` 1-199 → amber number; ≥200 → red; `ahead>0` → green; zeros muted gray.
4. Click "Refresh from git" → button disables mid-request; chip reads `git refreshed 0m ago` (gray).
5. Confirm no `Refused to load`/`Content-Security-Policy` errors in console; `list_network_requests` shows the three JS files `200`.

**Tier 2 — state preservation (the #1 fix)**
6. Expand two Notes `<details>`, tick "Needs attention only", scroll to Findings. Change a Findings severity select. **Expect:** both notes stay open, filter stays applied (checkbox stays ticked), scroll holds, only the changed region view-transitions. Repeat with the note reply text box mid-type (focus + typed text must survive).

**Tier 3 — per-item liveness**
7. Change verdict/severity selects → rail recolors **instantly** (before the network request settles — throttle network to "Slow 3G" to see the optimism), then reconciles silently.
8. Launch an impl item → its pending row pulses (`animate-pulse-soft`) and auto-refreshes every 5s; confirm via `list_network_requests` that only that row's `launch-status` polls and no board-wide refetch occurs.
9. Drag a NEXT row by its grip to a new position, drop → order sticks; `evaluate_script` reading `Array.from(document.querySelectorAll('#work-board-next-list [data-wb-id]')).map(e=>e.dataset.wbId)` matches the visual order; reload → same order server-side. Then mutate an unrelated item and re-drag to confirm Sortable survived the morph.

---

## Consolidated rollback (fastest → cleanest)

- **JS/CSS/template only** (Steps 1-9, 11f, 12c): `git checkout -- mcpgateway/templates/work_board_partial.html mcpgateway/templates/admin.html tailwind.config.js`, delete `mcpgateway/static/js/{idiomorph-htmx.min.js,sortable.min.js,work-board-live.js}`, `npm run build:css`. Board reverts to full-swap behavior, fully functional.
- **Python service/router** (Steps 11a-11e, 12a-12b): `git checkout -- mcpgateway/services/work_board_service.py mcpgateway/routers/work_board_router.py mcpgateway/db_work_board.py`. (`--reload` re-imports cleanly on save.)
- **Migration** (Step 10): `.venv/bin/python -m alembic -c mcpgateway/alembic.ini downgrade 5e72814c91e5` **then** delete `mcpgateway/alembic/versions/e3d48e0f3f0f_add_work_board_meta_table.py`. (Order matters — downgrade before deleting.)

## Two flags for Chad before finalizing
1. **Staleness threshold** `_GIT_STALE_MINUTES = 30` is a guess — confirm the amber cutoff (service line, Step 11b). One-constant change.
2. **Drag needed a new `/admin/board/reorder` endpoint**, not the existing `set-priority` — the existing renumber tiebreaks by `id`, not drag position (verified by simulation). It's additive and migration-free, but it is the one place the "reuse the existing endpoint" instruction did not survive verification. If a new endpoint is unacceptable, drag-to-reorder must be dropped and the `<select>` priority editor kept as the only reorder path.