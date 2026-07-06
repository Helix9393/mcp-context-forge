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

  // The stable, never-morphed wrapper -- delegate all board click/edit handlers here
  // so they survive every swap without re-binding. (Optimistic border reflect was
  // removed with the <select>s in Batch A: chips fire an immediate hx-patch and the
  // fast local morph reconciles the border in ~one frame, so no pre-echo is needed.)
  var stableWrap = document.getElementById("work-board-content");

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

  // ===================================================================
  // Batch B -- live feedback: "Saved ✓" toast + green flash on the row you
  // just changed. Both fire only for a REAL user action (a click or a form
  // submit), never for the 20s board poll or the 5s launch-status poll, so
  // the board doesn't self-congratulate on background refreshes.
  // ===================================================================

  // A toast stack, created once and parented to <body> so it survives every
  // morph swap (it lives outside #work-board-content entirely).
  var toastWrap = null;
  function ensureToastWrap() {
    if (toastWrap && document.body.contains(toastWrap)) return toastWrap;
    toastWrap = document.createElement("div");
    toastWrap.className = "wb-toast-wrap";
    toastWrap.setAttribute("aria-live", "polite");
    document.body.appendChild(toastWrap);
    return toastWrap;
  }
  function showToast(msg, variant) {
    var wrap = ensureToastWrap();
    var t = document.createElement("div");
    t.className = "wb-toast wb-toast--" + (variant || "ok");
    t.textContent = msg;
    wrap.appendChild(t);
    // force reflow so the enter transition plays, then schedule exit
    void t.offsetWidth;
    t.classList.add("wb-toast--in");
    setTimeout(function () {
      t.classList.remove("wb-toast--in");
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 300);
    }, 1800);
  }

  // Was this htmx request triggered by a genuine user gesture (not a poll)?
  function isUserTriggered(evt) {
    var cfg = evt.detail && evt.detail.requestConfig;
    var te = cfg && cfg.triggeringEvent;
    return !!(te && (te.type === "click" || te.type === "submit"));
  }

  var pendingFlashId = null;   // id of the .work-board-item to flash after settle
  var pendingToast = false;    // whether to pop a "Saved ✓" toast after settle

  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    var elt = evt.detail && evt.detail.elt;

    // ---- Batch C: pause the auto-poll while the user is mid-edit ----
    // A poll morph would rip out an open inline-edit <input> (the server renders
    // the static title, not the input) or clobber text being typed into a note.
    // Skip this one poll cycle; the next fires on schedule.
    if (elt && elt.id === "wb-poller") {
      var active = document.activeElement;
      var typing = active && /^(INPUT|TEXTAREA)$/.test(active.tagName) && targetsBoard(active);
      if (typing || document.querySelector("#work-board-content .wb-inline-editing")) {
        evt.preventDefault();
      }
      return;
    }

    // ---- Batch B: remember what to celebrate after the swap settles ----
    if (isUserTriggered(evt) && targetsBoard(elt)) {
      pendingToast = true;
      var item = elt.closest ? elt.closest(".work-board-item") : null;
      pendingFlashId = item && item.id ? item.id : null;
    }
  });

  document.body.addEventListener("htmx:afterSettle", function (evt) {
    if (!targetsBoard(evt.detail && evt.detail.target)) return;
    if (pendingToast) { showToast("Saved ✓"); pendingToast = false; }
    if (pendingFlashId) {
      var row = document.getElementById(pendingFlashId);
      if (row) {
        row.classList.remove("wb-flash");
        void row.offsetWidth;          // restart the animation if it's mid-play
        row.classList.add("wb-flash");
        setTimeout(function () { row.classList.remove("wb-flash"); }, 1200);
      }
      pendingFlashId = null;
    }
  });

  // Surface backend errors as a red toast instead of a silent no-op.
  document.body.addEventListener("htmx:responseError", function (evt) {
    if (!targetsBoard(evt.detail && (evt.detail.target || evt.detail.elt))) return;
    showToast("Couldn't save — try again", "err");
  });

  // ===================================================================
  // Batch D -- inline title edit: click a title to rename in place.
  // The title carries data-wb-edit-title="<item_id>". Clicking swaps it for an
  // <input>; Enter or blur PATCHes /admin/board/set-title (morph swap), Escape
  // cancels. Only human-authored lanes (Now / Next / Side ideas) opt in.
  // ===================================================================
  function beginTitleEdit(titleEl) {
    if (titleEl.querySelector(".wb-inline-editing")) return; // already editing
    var itemId = titleEl.getAttribute("data-wb-edit-title");
    var original = titleEl.getAttribute("data-wb-title-text") || titleEl.textContent.trim();

    var input = document.createElement("input");
    input.type = "text";
    input.className = "wb-inline-editing";
    input.value = original;
    input.setAttribute("aria-label", "Edit title");

    titleEl.innerHTML = "";
    titleEl.appendChild(input);
    input.focus();
    input.select();

    var done = false;
    function cancel() {
      if (done) return;
      done = true;
      titleEl.textContent = original;
    }
    function commit() {
      if (done) return;
      var next = input.value.trim();
      if (!next || next === original) { cancel(); return; }
      done = true;
      // htmx.ajax drives the same morph swap the chips use, so open notes /
      // scroll / filter are preserved and the flash+toast fire as usual.
      if (window.htmx) {
        window.htmx.ajax("PATCH", stableWrapPath("/admin/board/set-title"), {
          target: "#work-board-content-inner",
          swap: "morph:outerHTML transition:true",
          values: { item_id: itemId, title: next },
        });
      }
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "Escape") { e.preventDefault(); cancel(); }
    });
    input.addEventListener("blur", commit);
  }

  // root_path prefix: read it off any board control that already carries a
  // resolved hx-post/hx-patch URL, so inline edit hits the same mount point.
  function stableWrapPath(suffix) {
    var probe = document.querySelector("#work-board-content [hx-patch], #work-board-content [hx-post]");
    if (probe) {
      var url = probe.getAttribute("hx-patch") || probe.getAttribute("hx-post");
      var marker = "/admin/board/";
      var i = url.indexOf(marker);
      if (i >= 0) return url.slice(0, i) + suffix;
    }
    return suffix; // root_path is empty -> bare path
  }

  if (stableWrap) {
    stableWrap.addEventListener("click", function (ev) {
      var titleEl = ev.target.closest("[data-wb-edit-title]");
      if (!titleEl) return;
      if (ev.target.closest(".wb-inline-editing")) return; // clicking the input itself
      beginTitleEdit(titleEl);
    });
  }
})();
