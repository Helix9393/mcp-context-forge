# -*- coding: utf-8 -*-
"""Location: ./mcpgateway/services/work_board_service.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

Work Board Service.

Business logic for the personal work-tracking board vertical slice: CRUD on
board items/notes, the named lane-transition operations (set_now,
promote_next, demote_now), the read-only ``next_move`` projection, the
agent-backlog queue (``get_backlog`` / ``acknowledge`` / the attention state
machine), and the best-effort git/PR refresh.

Synchronous SQLAlchemy sessions throughout, per this repo's explicit design
decision (AGENTS.md "Synchronous SQLAlchemy in Async Handlers") -- callers
pass a ``db: Session`` as the first argument to every function; no
independent sessions are opened here (this is not audit/observability
writing, see AGENTS.md Issue #2871 caveat).
"""

# Standard
from datetime import datetime, timezone
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed-argv git/gh calls only, see refresh_git()
from typing import Any, Dict, List, Optional

# Third-Party
from sqlalchemy import func
from sqlalchemy.orm import Session

# First-Party
from mcpgateway.db_work_board import WorkBoardItem, WorkBoardNote

# ---------------------------------------------------------------------------
# Enum vocabulary (frozen -- §2.3 of the spec). Declared once here and reused
# by the router's Pydantic Literal types and the MCP tool input schemas.
# ---------------------------------------------------------------------------

LANES = ("now", "next", "branches", "prs", "tangents", "findings")
BRANCH_VERDICTS = ("land", "rebase", "abandon", "unknown")
PR_VERDICTS = ("review", "land", "close", "unknown")
TANGENT_STATUSES = ("parked", "promoted", "dropped")
FINDING_STATUSES = ("open", "fixing", "done", "wontfix")
FINDING_SEVERITIES = ("advisory", "warning", "critical")
NOTE_AUTHORS = ("operator", "agent")
ATTENTION_STATES = ("needs_attention", "addressed", "followup_requested", "acknowledged")

_SEVERITY_RANK = {"critical": 0, "warning": 1, "advisory": 2}

_NEXT_LANE_CAP = 5

# Legal (from_state, resolution) -> to_state transitions for agent notes.
# "resolution=None" (plain commentary) never appears here -- it always leaves
# attention untouched, checked separately in add_note().
_AGENT_RESOLUTION_TRANSITIONS = {
    ("needs_attention", "addressed"): "addressed",
    ("needs_attention", "followup_requested"): "followup_requested",
}


class WorkBoardError(Exception):
    """Base exception for all work-board service errors."""


class WorkBoardNotFoundError(WorkBoardError):
    """Raised when a requested item does not exist. Maps to HTTP 404."""


class WorkBoardConflictError(WorkBoardError):
    """Raised on a state conflict (single-NOW, NEXT cap, illegal attention transition). Maps to HTTP 409."""


class WorkBoardValidationError(WorkBoardError):
    """Raised on invalid input (bad enum value, malformed patch, illegal field). Maps to HTTP 422."""


def _today() -> str:
    """Return today's date as an ISO ``YYYY-MM-DD`` string.

    Returns:
        str: Today's date in ISO 8601 date format (UTC).
    """
    return datetime.now(timezone.utc).date().isoformat()


def _next_seq_id(db: Session, prefix: str) -> str:
    """Generate the next dense sequential id for a given prefix (``w-``, ``t-``, ``f-``).

    Args:
        db: SQLAlchemy session.
        prefix: Id prefix, e.g. ``"w-"``, ``"t-"``, ``"f-"``.

    Returns:
        str: A new id of the form ``"{prefix}NNN"`` not already present in the table.
    """
    existing = [row[0] for row in db.query(WorkBoardItem.id).filter(WorkBoardItem.id.like(f"{prefix}%")).all()]
    max_n = 0
    for item_id in existing:
        suffix = item_id[len(prefix) :]
        if suffix.isdigit():
            max_n = max(max_n, int(suffix))
    return f"{prefix}{max_n + 1:03d}"


def _slugify(text_value: str) -> str:
    """Slugify a branch name for use in a ``b-<slug>`` item id.

    Args:
        text_value: Free-text branch name.

    Returns:
        str: Lowercased, alphanumeric-and-hyphen-only slug.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text_value.lower()).strip("-")
    return slug or "branch"


def _renumber_next(db: Session) -> None:
    """Renumber all ``next``-lane items to a dense 1..n priority sequence.

    Existing relative order (by current ``priority``, nulls last, then id as a
    stable tiebreak) is preserved; only the numeric values are compacted.

    Args:
        db: SQLAlchemy session.
    """
    items = db.query(WorkBoardItem).filter(WorkBoardItem.lane == "next").order_by(WorkBoardItem.priority.is_(None), WorkBoardItem.priority, WorkBoardItem.id).all()
    for idx, item in enumerate(items, start=1):
        if item.priority != idx:
            item.priority = idx


def _append_note(db: Session, item: WorkBoardItem, text_value: str, author: str) -> WorkBoardNote:
    """Append a note row to an item without committing.

    Args:
        db: SQLAlchemy session.
        item: The item the note belongs to.
        text_value: Note body.
        author: ``"operator"`` or ``"agent"``.

    Returns:
        WorkBoardNote: The newly created (uncommitted) note row.
    """
    note = WorkBoardNote(item_id=item.id, text=text_value, author=author)
    db.add(note)
    return note


def _set_attention_for_operator_event(item: WorkBoardItem) -> None:
    """Flip attention to ``needs_attention`` for any operator-attributed event.

    Per §6.2: any operator note -- manual comment, UI reply, or an
    operator-attributed system note from a named op / status change -- always
    flips attention to ``needs_attention``, regardless of current state.

    Args:
        item: The item whose attention state to update.
    """
    item.attention = "needs_attention"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get_item(db: Session, item_id: str) -> WorkBoardItem:
    """Fetch a single item by id.

    Args:
        db: SQLAlchemy session.
        item_id: Item id.

    Returns:
        WorkBoardItem: The matching item.

    Raises:
        WorkBoardNotFoundError: If no item with ``item_id`` exists.
    """
    item = db.get(WorkBoardItem, item_id)
    if item is None:
        raise WorkBoardNotFoundError(f"No work-board item with id '{item_id}'")
    return item


def _validate_lane_fields(lane: str, payload: Dict[str, Any]) -> None:
    """Validate lane-specific enum fields in a create/update payload.

    Args:
        lane: Target lane.
        payload: Field values being written (already lane-scoped by the caller).

    Raises:
        WorkBoardValidationError: If lane is unknown or an enum field holds an illegal value.
    """
    if lane not in LANES:
        raise WorkBoardValidationError(f"Unknown lane '{lane}'. Must be one of {LANES}.")

    def _check(field: str, allowed: tuple) -> None:
        """Raise if ``payload[field]`` is set to a value outside ``allowed``.

        Args:
            field: Payload key to check (e.g. ``"verdict"``, ``"status"``).
            allowed: Tuple of legal values for this field on the current lane.

        Raises:
            WorkBoardValidationError: If the field is present and its value is not in ``allowed``.
        """
        value = payload.get(field)
        if value is not None and value not in allowed:
            raise WorkBoardValidationError(f"Invalid {field} '{value}' for lane '{lane}'. Must be one of {allowed}.")

    if lane == "branches":
        _check("verdict", BRANCH_VERDICTS)
    elif lane == "prs":
        _check("verdict", PR_VERDICTS)
    elif lane == "tangents":
        _check("status", TANGENT_STATUSES)
    elif lane == "findings":
        _check("status", FINDING_STATUSES)
        _check("severity", FINDING_SEVERITIES)


def create_item(db: Session, lane: str, payload: Dict[str, Any]) -> WorkBoardItem:
    """Create a new work-board item in the given lane.

    Args:
        db: SQLAlchemy session.
        lane: One of :data:`LANES`.
        payload: Field values for the new item (``title`` required; lane-specific fields optional).

    Returns:
        WorkBoardItem: The newly created, committed item.

    Raises:
        WorkBoardValidationError: If the lane or an enum field is invalid, or ``title`` is missing/blank.
        WorkBoardConflictError: If ``lane == "next"`` and the lane is already at the 5-item cap,
            or if ``lane == "now"`` and NOW is already occupied.
    """
    _validate_lane_fields(lane, payload)

    title = (payload.get("title") or "").strip()
    if not title:
        raise WorkBoardValidationError("title is required and must be non-empty.")

    if lane == "now":
        existing_now = db.query(WorkBoardItem).filter(WorkBoardItem.lane == "now").first()
        if existing_now is not None:
            raise WorkBoardConflictError(f"NOW is already occupied by '{existing_now.id}'.")

    if lane == "next":
        count = db.query(func.count(WorkBoardItem.id)).filter(WorkBoardItem.lane == "next").scalar()  # pylint: disable=not-callable
        if count >= _NEXT_LANE_CAP:
            raise WorkBoardConflictError(f"NEXT lane is at its {_NEXT_LANE_CAP}-item cap; drop or promote an item first.")

    if lane == "branches":
        git_branch = payload.get("git_branch") or title
        item_id = f"b-{_slugify(git_branch)}"
    elif lane == "prs":
        pr_number = payload.get("pr_number")
        if pr_number is None:
            raise WorkBoardValidationError("pr_number is required for prs-lane items.")
        item_id = f"p-{pr_number}"
    elif lane == "tangents":
        item_id = _next_seq_id(db, "t-")
    elif lane == "findings":
        item_id = _next_seq_id(db, "f-")
    else:
        item_id = _next_seq_id(db, "w-")

    item = WorkBoardItem(id=item_id, lane=lane, title=title, attention="acknowledged")

    for field in ("priority", "branch", "started", "captured", "status", "verdict", "severity", "source", "git_branch", "git_ahead", "git_behind", "git_last_commit", "pr_number", "pr_state"):
        if field in payload and payload[field] is not None:
            setattr(item, field, payload[field])

    if lane == "tangents":
        item.status = item.status or "parked"
        item.captured = item.captured or _today()
    if lane == "branches":
        item.git_branch = item.git_branch or title
        item.verdict = item.verdict or "unknown"
    if lane == "prs":
        item.verdict = item.verdict or "review"

    db.add(item)

    if lane == "next":
        db.flush()
        _renumber_next(db)

    db.commit()
    db.refresh(item)
    return item


_PATCHABLE_FIELDS = (
    "title",
    "priority",
    "branch",
    "started",
    "captured",
    "status",
    "verdict",
    "severity",
    "source",
    "git_branch",
    "git_ahead",
    "git_behind",
    "git_last_commit",
    "pr_number",
    "pr_state",
)


def update_item(db: Session, item_id: str, patch: Dict[str, Any], author: str = "operator") -> WorkBoardItem:
    """Update mutable fields on an item.

    ``id`` and ``lane`` are immutable here -- lane moves only happen via the
    named operations (:func:`set_now`, :func:`promote_next`, :func:`demote_now`).
    ``attention`` is rejected outright: transitions only happen through the
    §6.2 state machine (:func:`add_note`, :func:`acknowledge`).

    When the patch changes ``status`` or ``verdict``, a system note is
    appended recording the transition, attributed to ``author`` -- this is
    how operator-driven status changes enter the backlog per §3.1/§6.2.

    Args:
        db: SQLAlchemy session.
        item_id: Item to update.
        patch: Field values to change. Must not contain ``id``, ``lane``, or ``attention``.
        author: ``"operator"`` (default) or ``"agent"`` -- attributed on the system note.

    Returns:
        WorkBoardItem: The updated, committed item.

    Raises:
        WorkBoardNotFoundError: If no item with ``item_id`` exists.
        WorkBoardValidationError: If the patch contains an immutable/forbidden field or an illegal enum value.
    """
    item = get_item(db, item_id)

    if "id" in patch:
        raise WorkBoardValidationError("id is immutable.")
    if "lane" in patch:
        raise WorkBoardValidationError("lane is immutable; use set_now/promote_next/demote_now to move items between lanes.")
    if "attention" in patch:
        raise WorkBoardValidationError("attention cannot be set directly; it changes only via add_note()/acknowledge().")

    unknown = set(patch) - set(_PATCHABLE_FIELDS)
    if unknown:
        raise WorkBoardValidationError(f"Unknown/unsupported patch field(s): {sorted(unknown)}")

    merged = {f: getattr(item, f) for f in ("verdict", "status", "severity")}
    merged.update({k: v for k, v in patch.items() if k in ("verdict", "status", "severity")})
    _validate_lane_fields(item.lane, merged)

    status_changed = "status" in patch and patch["status"] != item.status
    verdict_changed = "verdict" in patch and patch["verdict"] != item.verdict

    old_status, old_verdict = item.status, item.verdict

    for field, value in patch.items():
        setattr(item, field, value)

    if item.lane == "next" and "priority" in patch:
        db.flush()
        _renumber_next(db)

    if status_changed:
        _append_note(db, item, f"status: {old_status} -> {item.status} {_today()}", author)
        if author == "operator":
            _set_attention_for_operator_event(item)
    if verdict_changed:
        _append_note(db, item, f"verdict: {old_verdict} -> {item.verdict} {_today()}", author)
        if author == "operator":
            _set_attention_for_operator_event(item)

    db.commit()
    db.refresh(item)
    return item


def delete_item(db: Session, item_id: str) -> None:
    """Delete an item and cascade-delete its notes.

    Args:
        db: SQLAlchemy session.
        item_id: Item to delete.

    Raises:
        WorkBoardNotFoundError: If no item with ``item_id`` exists.
    """
    item = get_item(db, item_id)
    lane = item.lane
    db.delete(item)
    db.flush()
    if lane == "next":
        _renumber_next(db)
    db.commit()


def add_note(db: Session, item_id: str, text_value: str, author: str = "operator", resolution: Optional[str] = None) -> WorkBoardNote:
    """Append a note to an item, applying the §6.2 attention state machine.

    - ``author="operator"``: any ``resolution`` value is illegal (422). Sets
      attention to ``needs_attention`` unconditionally (R3).
    - ``author="agent"``, ``resolution=None``: plain commentary/proposal note.
      Leaves attention untouched.
    - ``author="agent"``, ``resolution="addressed"``: legal only from
      ``needs_attention`` (else 409). Flips to ``addressed``.
    - ``author="agent"``, ``resolution="followup_requested"``: legal only from
      ``needs_attention`` (else 409); the note text must contain a question
      mark (else 422). Flips to ``followup_requested``.

    The note and any attention flip commit in the same transaction (R9): an
    item can never carry a flipped state without its explanatory note.

    Args:
        db: SQLAlchemy session.
        item_id: Item to comment on.
        text_value: Note body; must be non-empty after stripping whitespace.
        author: ``"operator"`` (default) or ``"agent"``.
        resolution: ``None`` (default), ``"addressed"``, or ``"followup_requested"``.

    Returns:
        WorkBoardNote: The newly created, committed note.

    Raises:
        WorkBoardValidationError: On empty text, invalid author/resolution combination,
            or a ``followup_requested`` note missing a question mark.
        WorkBoardConflictError: If a resolution is given but the item is not in ``needs_attention``.
        WorkBoardNotFoundError: If no item with ``item_id`` exists.
    """
    item = get_item(db, item_id)

    stripped = (text_value or "").strip()
    if not stripped:
        raise WorkBoardValidationError("Note text must be non-empty.")

    if author not in NOTE_AUTHORS:
        raise WorkBoardValidationError(f"Invalid author '{author}'. Must be one of {NOTE_AUTHORS}.")

    if author == "operator":
        if resolution is not None:
            raise WorkBoardValidationError("resolution is only valid for author='agent' notes.")
        note = _append_note(db, item, stripped, author)
        _set_attention_for_operator_event(item)
    else:
        if resolution is not None:
            if resolution not in ("addressed", "followup_requested"):
                raise WorkBoardValidationError(f"Invalid resolution '{resolution}'. Must be 'addressed' or 'followup_requested'.")
            if item.attention != "needs_attention":
                raise WorkBoardConflictError(f"Cannot apply resolution '{resolution}': item '{item_id}' attention is '{item.attention}', not 'needs_attention'.")
            if resolution == "followup_requested" and "?" not in stripped:
                raise WorkBoardValidationError("followup_requested notes must contain a clarifying question ('?').")
            note = _append_note(db, item, stripped, author)
            item.attention = _AGENT_RESOLUTION_TRANSITIONS[("needs_attention", resolution)]
        else:
            note = _append_note(db, item, stripped, author)

    db.commit()
    db.refresh(note)
    return note


def add_tangent(db: Session, title: str) -> WorkBoardItem:
    """Create a new tangent item.

    Args:
        db: SQLAlchemy session.
        title: Tangent title/description.

    Returns:
        WorkBoardItem: The newly created tangent, ``status="parked"``, ``captured=today``.

    Raises:
        WorkBoardValidationError: If title is empty.
    """
    return create_item(db, "tangents", {"title": title})


# ---------------------------------------------------------------------------
# Named board operations
# ---------------------------------------------------------------------------


def _get_now_item(db: Session) -> Optional[WorkBoardItem]:
    """Fetch the current NOW item, if any.

    Args:
        db: SQLAlchemy session.

    Returns:
        Optional[WorkBoardItem]: The NOW item, or ``None`` if NOW is empty.
    """
    return db.query(WorkBoardItem).filter(WorkBoardItem.lane == "now").first()


def promote_next(db: Session, item_id: str, displace: Optional[str] = None, author: str = "operator") -> WorkBoardItem:
    """Promote a ``next``-lane item to NOW, handling the currently-occupied-NOW case explicitly.

    Fixes the prototype bug (``core.mjs promoteNext``) that silently dropped
    the displaced NOW item. If NOW is occupied, ``displace`` is required and
    must be one of:

    - ``"next"``: the displaced NOW item moves into ``next`` at the promoted
      item's old priority slot (a swap, so the cap cannot overflow).
    - ``"tangent"``: the displaced NOW item is parked in ``tangents``.
    - ``"drop"``: the displaced NOW item is deleted (only via this explicit value).

    Args:
        db: SQLAlchemy session.
        item_id: The ``next``-lane item to promote.
        displace: Required (one of ``"next"``/``"tangent"``/``"drop"``) only when NOW is occupied.
        author: Attribution for the system notes this operation writes.

    Returns:
        WorkBoardItem: The promoted item, now in lane ``"now"``.

    Raises:
        WorkBoardNotFoundError: If ``item_id`` does not exist.
        WorkBoardValidationError: If the item is not in the ``next`` lane, or ``displace`` holds an
            unrecognized value.
        WorkBoardConflictError: If NOW is occupied and ``displace`` is ``None`` -- the error message
            names the occupied item and the three options.
    """
    item = get_item(db, item_id)
    if item.lane != "next":
        raise WorkBoardValidationError(f"Item '{item_id}' is not in the 'next' lane (currently '{item.lane}').")

    if displace is not None and displace not in ("next", "tangent", "drop"):
        raise WorkBoardValidationError(f"Invalid displace value '{displace}'. Must be one of 'next', 'tangent', 'drop'.")

    now_item = _get_now_item(db)
    today = _today()

    if now_item is not None:
        if displace is None:
            raise WorkBoardConflictError(f"NOW is occupied by '{now_item.id}'. Specify displace='next'|'tangent'|'drop' to resolve.")

        old_priority = item.priority

        if displace == "next":
            now_item.lane = "next"
            now_item.priority = old_priority
            now_item.started = None
            _append_note(db, now_item, f"Displaced from NOW -> next {today}", author)
        elif displace == "tangent":
            now_item.lane = "tangents"
            now_item.status = "parked"
            now_item.captured = today
            now_item.priority = None
            now_item.started = None
            _append_note(db, now_item, f"Displaced from NOW -> parked {today}", author)
        else:  # drop
            db.delete(now_item)
            # Flush the delete immediately: the unit-of-work does not guarantee
            # DELETE-before-INSERT/UPDATE ordering within a single flush, and the
            # single-NOW partial unique index would otherwise transiently see
            # both rows claiming lane='now' in the same statement batch.
            db.flush()

        if author == "operator":
            if displace != "drop":
                _set_attention_for_operator_event(now_item)

    item.lane = "now"
    item.started = today
    item.priority = None

    db.flush()
    _renumber_next(db)
    db.commit()
    db.refresh(item)
    return item


def set_now(db: Session, item_id: str, displace: Optional[str] = None, author: str = "operator") -> WorkBoardItem:
    """Set NOW to a specific ``next``-lane item (prototype semantics: item must already be in ``next``).

    Delegates to :func:`promote_next`.

    Args:
        db: SQLAlchemy session.
        item_id: The ``next``-lane item to promote to NOW.
        displace: Required only when NOW is occupied -- see :func:`promote_next`.
        author: Attribution for the system notes this operation writes.

    Returns:
        WorkBoardItem: The promoted item, now in lane ``"now"``.
    """
    return promote_next(db, item_id, displace=displace, author=author)


def demote_now(db: Session, to: str, priority: Optional[int] = None, author: str = "operator") -> WorkBoardItem:
    """Demote the current NOW item to ``next`` or ``tangents``.

    Required capability missing from the prototype (previously done by hand-editing JSON).

    Args:
        db: SQLAlchemy session.
        to: ``"next"`` or ``"tangent"``.
        priority: Target priority when ``to="next"`` (default 1).
        author: Attribution for the system note this operation writes.

    Returns:
        WorkBoardItem: The demoted item, in its new lane.

    Raises:
        WorkBoardNotFoundError: If NOW is currently empty.
        WorkBoardValidationError: If ``to`` is not ``"next"`` or ``"tangent"``.
        WorkBoardConflictError: If ``to="next"`` and the ``next`` lane is already at its 5-item cap.
    """
    if to not in ("next", "tangent"):
        raise WorkBoardValidationError(f"Invalid 'to' value '{to}'. Must be 'next' or 'tangent'.")

    now_item = _get_now_item(db)
    if now_item is None:
        raise WorkBoardNotFoundError("NOW is empty; nothing to demote.")

    today = _today()

    if to == "next":
        count = db.query(func.count(WorkBoardItem.id)).filter(WorkBoardItem.lane == "next").scalar()  # pylint: disable=not-callable
        if count >= _NEXT_LANE_CAP:
            raise WorkBoardConflictError(f"NEXT lane is at its {_NEXT_LANE_CAP}-item cap; drop or promote a 'next' item before demoting NOW.")
        now_item.lane = "next"
        now_item.priority = priority if priority is not None else 1
        now_item.started = None
    else:
        now_item.lane = "tangents"
        now_item.status = "parked"
        now_item.captured = today
        now_item.priority = None
        now_item.started = None

    _append_note(db, now_item, f"Demoted from NOW {today}", author)
    if author == "operator":
        _set_attention_for_operator_event(now_item)

    db.flush()
    if to == "next":
        _renumber_next(db)
    db.commit()
    db.refresh(now_item)
    return now_item


def next_move(db: Session) -> Dict[str, Any]:
    """Compute the recommended next move: NOW if set, else highest-priority ``next`` item,
    else highest-severity open finding, else idle.

    Args:
        db: SQLAlchemy session.

    Returns:
        Dict[str, Any]: ``{"move": <item dict or None>, "lane": <str or None>, "rationale": <str>}``.
    """
    now_item = _get_now_item(db)
    if now_item is not None:
        return {"move": _item_to_dict(now_item), "lane": "now", "rationale": f"Continue current NOW: {now_item.title}"}

    next_item = db.query(WorkBoardItem).filter(WorkBoardItem.lane == "next").order_by(WorkBoardItem.priority.is_(None), WorkBoardItem.priority, WorkBoardItem.id).first()
    if next_item is not None:
        return {"move": _item_to_dict(next_item), "lane": "next", "rationale": f"Promote top NEXT item: {next_item.title}"}

    findings = db.query(WorkBoardItem).filter(WorkBoardItem.lane == "findings", WorkBoardItem.status == "open").all()
    if findings:
        best = min(findings, key=lambda f: (_SEVERITY_RANK.get(f.severity, 99), f.id))
        return {"move": _item_to_dict(best), "lane": "findings", "rationale": f"Address highest-severity open finding: {best.title}"}

    return {"move": None, "lane": None, "rationale": "Board is idle: no NOW, no NEXT items, no open findings."}


# ---------------------------------------------------------------------------
# Backlog queue
# ---------------------------------------------------------------------------


def get_backlog(db: Session) -> List[Dict[str, Any]]:
    """Return only ``needs_attention`` items with their full note thread.

    Ordering: ``now`` -> ``next`` (by priority) -> ``findings`` (severity rank,
    then id) -> ``tangents`` (captured, then id), with ``branches``/``prs``
    trailing (by id). Everything not ``needs_attention`` is excluded, so a
    pass with no new operator input reads an empty list (R4, R10).

    Args:
        db: SQLAlchemy session.

    Returns:
        List[Dict[str, Any]]: Ordered list of item dicts (each including its ``notes``).
    """
    items = db.query(WorkBoardItem).filter(WorkBoardItem.attention == "needs_attention").all()

    def _lane_rank(item: WorkBoardItem) -> int:
        order = {"now": 0, "next": 1, "findings": 2, "tangents": 3, "branches": 4, "prs": 5}
        return order.get(item.lane, 99)

    def _sort_key(item: WorkBoardItem):
        if item.lane == "next":
            return (_lane_rank(item), item.priority if item.priority is not None else 999, item.id)
        if item.lane == "findings":
            return (_lane_rank(item), _SEVERITY_RANK.get(item.severity, 99), item.id)
        if item.lane == "tangents":
            return (_lane_rank(item), item.captured or "", item.id)
        return (_lane_rank(item), item.id)

    items.sort(key=_sort_key)
    return [_item_to_dict(item) for item in items]


def acknowledge(db: Session, item_id: str, author: str = "operator") -> WorkBoardItem:
    """Acknowledge an ``addressed`` item, closing the backlog loop (``addressed`` -> ``acknowledged``).

    Appends its own system note inside the transition -- this does not
    re-trigger the operator-note-flips-to-``needs_attention`` rule, since the
    flip target here is ``acknowledged``, not ``needs_attention``.

    Args:
        db: SQLAlchemy session.
        item_id: Item to acknowledge.
        author: Attribution for the system note (always ``"operator"`` in practice -- this endpoint
            is deliberately not exposed as an MCP tool, see §6.2).

    Returns:
        WorkBoardItem: The acknowledged item.

    Raises:
        WorkBoardNotFoundError: If no item with ``item_id`` exists.
        WorkBoardConflictError: If the item's attention is not currently ``addressed``.
    """
    item = get_item(db, item_id)
    if item.attention != "addressed":
        raise WorkBoardConflictError(f"Cannot acknowledge item '{item_id}': attention is '{item.attention}', not 'addressed'.")

    item.attention = "acknowledged"
    _append_note(db, item, f"Acknowledged {_today()}", author)

    db.commit()
    db.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Board read
# ---------------------------------------------------------------------------


def _item_to_dict(item: WorkBoardItem) -> Dict[str, Any]:
    """Convert an ORM item (with its notes) to a plain dict for API/service consumers.

    Args:
        item: ORM item instance.

    Returns:
        Dict[str, Any]: Plain-dict projection of the item, including its ``notes`` list.
    """
    return {
        "id": item.id,
        "lane": item.lane,
        "title": item.title,
        "priority": item.priority,
        "branch": item.branch,
        "started": item.started,
        "captured": item.captured,
        "status": item.status,
        "verdict": item.verdict,
        "severity": item.severity,
        "source": item.source,
        "git_branch": item.git_branch,
        "git_ahead": item.git_ahead,
        "git_behind": item.git_behind,
        "git_last_commit": item.git_last_commit,
        "pr_number": item.pr_number,
        "pr_state": item.pr_state,
        "attention": item.attention,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "notes": [{"id": n.id, "at": n.at, "text": n.text, "author": n.author} for n in item.notes],
    }


def get_board(db: Session) -> Dict[str, Any]:
    """Return the full board grouped by lane, plus the ``next_move`` projection.

    Args:
        db: SQLAlchemy session.

    Returns:
        Dict[str, Any]: ``{"now": <dict|None>, "next": [...], "branches": [...], "prs": [...],
        "tangents": [...], "findings": [...], "next_move": {...}, "updated": <datetime|None>}``.
    """
    all_items = db.query(WorkBoardItem).all()

    now_item = next((i for i in all_items if i.lane == "now"), None)
    next_items = sorted((i for i in all_items if i.lane == "next"), key=lambda i: (i.priority if i.priority is not None else 999, i.id))
    branches = sorted((i for i in all_items if i.lane == "branches"), key=lambda i: i.id)
    prs = sorted((i for i in all_items if i.lane == "prs"), key=lambda i: i.id)
    tangents = sorted((i for i in all_items if i.lane == "tangents"), key=lambda i: i.id)
    findings = sorted((i for i in all_items if i.lane == "findings"), key=lambda i: (_SEVERITY_RANK.get(i.severity, 99), i.id))

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


# ---------------------------------------------------------------------------
# Git/PR refresh (§3.4) -- soft dependency, fixed-argv subprocess only.
# ---------------------------------------------------------------------------


def _run_git(args: List[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a fixed-argv git subcommand against ``cwd``.

    Security posture: argv is always a hardcoded list built by this module;
    ``cwd`` comes only from ``settings.work_board_git_repo`` (operator-configured
    env, never request-supplied). ``shell=False`` (the default for a list argv).

    Args:
        args: Full argv, e.g. ``["git", "status"]``.
        cwd: Repository path to run the command in.

    Returns:
        subprocess.CompletedProcess: Completed process with captured stdout/stderr.
    """
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=15, check=True)  # nosec B603 - fixed argv, operator-configured cwd only


def refresh_git(db: Session, repo_path: str) -> Dict[str, Any]:
    """Refresh ``branches``/``prs`` lane items from the live git repo (and ``gh``, if available).

    Not a hard dependency: callers should pass an empty/invalid ``repo_path``
    to get a soft-fail response rather than raising.

    Args:
        db: SQLAlchemy session.
        repo_path: Absolute path to the git repo (``settings.work_board_git_repo``); empty or
            not-a-directory returns a soft-fail response instead of raising.

    Returns:
        Dict[str, Any]: ``{"refreshed": bool, "reason": <str, if not refreshed>,
        "branches_updated": <int>, "prs_updated": <int>}`` on success.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return {"refreshed": False, "reason": "work_board_git_repo not configured"}

    try:
        result = _run_git(["git", "for-each-ref", "--format=%(refname:short)\t%(objectname:short)", "refs/heads/"], repo_path)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"refreshed": False, "reason": f"git for-each-ref failed: {exc}"}

    branch_lines = [line for line in result.stdout.splitlines() if line.strip()]
    live_branches: Dict[str, str] = {}
    for line in branch_lines:
        parts = line.split("\t")
        if len(parts) == 2:
            live_branches[parts[0]] = parts[1]

    base_branch = None
    for candidate in ("main", "master"):
        try:
            _run_git(["git", "show-ref", "--verify", f"refs/heads/{candidate}"], repo_path)
            base_branch = candidate
            break
        except (subprocess.SubprocessError, OSError):
            continue

    existing_branch_items = {i.git_branch: i for i in db.query(WorkBoardItem).filter(WorkBoardItem.lane == "branches").all()}

    branches_updated = 0
    for name, last_commit in live_branches.items():
        ahead = behind = None
        if base_branch is not None and name != base_branch:
            try:
                counts = _run_git(["git", "rev-list", "--left-right", "--count", f"{base_branch}...{name}"], repo_path)
                left, right = counts.stdout.strip().split()
                behind, ahead = int(left), int(right)
            except (subprocess.SubprocessError, OSError, ValueError):
                ahead = behind = None
        else:
            ahead, behind = 0, 0

        existing = existing_branch_items.get(name)
        if existing is not None:
            existing.git_ahead = ahead
            existing.git_behind = behind
            existing.git_last_commit = last_commit
            existing.title = existing.title or name
        else:
            new_item = WorkBoardItem(
                id=f"b-{_slugify(name)}",
                lane="branches",
                title=name,
                git_branch=name,
                git_ahead=ahead,
                git_behind=behind,
                git_last_commit=last_commit,
                verdict="unknown",
                attention="acknowledged",
            )
            db.add(new_item)
        branches_updated += 1

    for name, item in existing_branch_items.items():
        if name not in live_branches:
            db.delete(item)

    prs_updated = 0
    if shutil.which("gh") is not None:
        try:
            pr_result = subprocess.run(  # nosec B603 B607 - fixed argv, operator-configured cwd only
                ["gh", "pr", "list", "--json", "number,title,state"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            prs = json.loads(pr_result.stdout or "[]")
            existing_pr_items = {i.pr_number: i for i in db.query(WorkBoardItem).filter(WorkBoardItem.lane == "prs").all()}
            for pr in prs:
                number = pr.get("number")
                existing = existing_pr_items.get(number)
                if existing is not None:
                    existing.title = pr.get("title", existing.title)
                    existing.pr_state = pr.get("state", existing.pr_state)
                else:
                    db.add(
                        WorkBoardItem(
                            id=f"p-{number}",
                            lane="prs",
                            title=pr.get("title", f"PR #{number}"),
                            pr_number=number,
                            pr_state=pr.get("state"),
                            verdict="review",
                            attention="acknowledged",
                        )
                    )
                prs_updated += 1
        except (subprocess.SubprocessError, OSError, ValueError):
            # gh missing/erroring leaves prs untouched (prototype behavior).
            pass

    db.commit()
    return {"refreshed": True, "branches_updated": branches_updated, "prs_updated": prs_updated}
