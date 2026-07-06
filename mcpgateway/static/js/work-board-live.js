/* Work Board liveness (Tier 2 state preservation + filter persistence).
 * Self-hosted static file (CSP 'self' covers it; no inline JS, no eval).
 * Loads after idiomorph.min.js (core), idiomorph-htmx.min.js (htmx ext), and the admin
 * bundle (window.htmx). Tier 3 optimistic reflect is appended to this file later.
 *
 * NOTE on approach: idiomorph's htmx swap path does NOT consult
 * Idiomorph.defaults.callbacks (verified empirically: 0 invocations), so a
 * beforeAttributeUpdated guard is a dead end here. Instead we snapshot which
 * <details> are open right before the swap and re-open the matching ones right
 * after -- independent of idiomorph internals, so it survives library upgrades.
 */
(function () {
  "use strict";

  // Stable identity for a <details> across a morph swap: the enclosing work-board
  // item's id (morph preserves ids) + the details' fixed position within that item
  // (each item renders the same details in the same order every render).
  function detailsKey(d) {
    var item = d.closest(".work-board-item");
    if (!item || !item.id) return null;
    var siblings = item.querySelectorAll("details");
    var idx = Array.prototype.indexOf.call(siblings, d);
    return item.id + "::" + idx;
  }

  // True when an htmx swap targets something inside the Work Board wrapper.
  function targetsBoard(node) {
    if (!node) return false;
    if (node.id === "work-board-content-inner" || node.id === "work-board-content") return true;
    return !!(node.closest && node.closest("#work-board-content"));
  }

  // ----- Tier 2: preserve open <details> across every morph swap -----
  // The server always renders <details> collapsed, so without this every swap would
  // collapse open notes -- the exact problem this work exists to fix.
  // Both handlers guard on targetsBoard(evt.detail.target): the admin shell fires
  // background htmx swaps (metrics/events partials), and with View Transitions the
  // board morph is async -- so a non-board afterSwap can interleave. Pairing capture
  // and restore to the SAME board swap keeps an unrelated swap from consuming the snapshot.
  var openSnapshot = null;

  document.body.addEventListener("htmx:beforeSwap", function (evt) {
    var tgt = evt.detail && evt.detail.target;
    if (!targetsBoard(tgt)) return;
    openSnapshot = new Set();
    document.querySelectorAll("#work-board-content details[open]").forEach(function (d) {
      var k = detailsKey(d);
      if (k) openSnapshot.add(k);
    });
  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var tgt = evt.detail && evt.detail.target;
    if (!targetsBoard(tgt) || openSnapshot === null) return;
    var snap = openSnapshot;
    openSnapshot = null;
    document.querySelectorAll("#work-board-content details").forEach(function (d) {
      var k = detailsKey(d);
      if (k && snap.has(k)) d.open = true;
    });
  });

  // ----- Tier 2: attention-filter persistence across morph -----
  // The filter class lives on the stable #work-board-content wrapper (never swapped).
  // The checkbox re-renders unchecked inside the partial, so re-sync it from the wrapper.
  function resyncAttentionFilter() {
    var cb = document.getElementById("work-board-attention-filter");
    var wrap = document.getElementById("work-board-content");
    if (cb && wrap) cb.checked = wrap.classList.contains("work-board-filter-attention");
  }
  document.body.addEventListener("htmx:afterSettle", resyncAttentionFilter);
  document.body.addEventListener("htmx:load", resyncAttentionFilter);

  // ----- Tier 3: optimistic left-border reflect on verdict/severity change -----
  // Recolor the row's left edge the instant the operator picks a value, before the
  // hx-patch round-trip returns. The morph response then reconciles to the server value.
  var SEVERITY_BORDER = { critical: "border-red-500", warning: "border-amber-400", advisory: "border-gray-300" };
  var ALL_SEVERITY_BORDERS = ["border-red-500", "border-amber-400", "border-gray-300"];
  function onSelectChange(ev) {
    var sel = ev.target;
    if (!sel || sel.tagName !== "SELECT") return;
    var row = sel.closest(".work-board-item");
    if (!row) return;
    if (sel.name === "verdict") {
      // Branches/PRs rows live in a divide-y tbody; the left border is driven by the
      // data-verdict attribute + inline CSS (a bare Tailwind border class is overridden).
      row.setAttribute("data-verdict", sel.value);
    } else if (sel.name === "severity") {
      // Findings are plain divs (no divide-y override) -> swap the Tailwind border class.
      var cls = SEVERITY_BORDER[sel.value];
      if (cls) {
        ALL_SEVERITY_BORDERS.forEach(function (c) { row.classList.remove(c); });
        row.classList.add(cls);
      }
    }
  }
  // Delegated on the stable, never-morphed wrapper -> no re-binding after swaps.
  var stableWrap = document.getElementById("work-board-content");
  if (stableWrap) stableWrap.addEventListener("change", onSelectChange);

  // ----- "Jump to it": scroll to the item WITHOUT changing the URL hash -----
  // The admin shell is hash-routed; a bare href="#work-board-<id>" changes
  // location.hash, which the tab router reads as "switch tab", fails to match,
  // and bounces the user to Overview. Intercept, scroll in place, briefly flash.
  function onJumpClick(ev) {
    var link = ev.target.closest("[data-wb-jump]");
    if (!link) return;
    ev.preventDefault();
    var target = document.getElementById(link.getAttribute("data-wb-jump"));
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.style.transition = "box-shadow .3s ease";
    target.style.boxShadow = "0 0 0 3px rgba(99, 102, 241, 0.6)";
    setTimeout(function () { target.style.boxShadow = ""; }, 1200);
  }
  if (stableWrap) stableWrap.addEventListener("click", onJumpClick);
})();
