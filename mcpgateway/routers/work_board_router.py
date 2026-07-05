# -*- coding: utf-8 -*-
"""Location: ./mcpgateway/routers/work_board_router.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

Work Board Router.

FastAPI endpoints for the personal work-tracking board vertical slice: the
full JSON REST API (``work_board_router``, mounted at ``/board``) plus the
admin-UI sub-router (``work_board_admin_router``, mounted at
``/admin/board``, spec §5.3) that renders and mutates the board through
server-rendered HTMX partials -- no terminal required.

Auth posture mirrors ``toolops_router.py``: JSON reads require ``tools.read``,
JSON writes require ``admin.system_config``. The admin sub-router follows the
``llm_admin_router`` precedent (see ``mcpgateway/api/v1/__init__.py`` Group F):
mounted only when ``mcpgateway_admin_api_enabled`` is also true, with
``enforce_admin_csrf`` applied at include-time exactly like ``llm_admin_router``
-- no bespoke auth/CSRF logic lives in this module. ``/board`` (the JSON API)
is intentionally not added to the token-scoping allow-list in
``middleware/token_scoping.py`` (default-deny for unmapped protected paths is
the correct failure mode until a scoped API token needs direct REST access --
see AGENTS.md security invariants).
"""

# Standard
from typing import Any, Dict, List, Literal, Optional

# Third-Party
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# First-Party
from mcpgateway.main import get_db
from mcpgateway.middleware.rbac import get_current_user_with_permissions, require_permission
from mcpgateway.services.logging_service import LoggingService
from mcpgateway.services import work_board_service as service
from mcpgateway.services.work_board_service import (
    ATTENTION_STATES,
    BRANCH_VERDICTS,
    FINDING_SEVERITIES,
    FINDING_STATUSES,
    LANES,
    NOTE_AUTHORS,
    PR_VERDICTS,
    TANGENT_STATUSES,
    WorkBoardConflictError,
    WorkBoardError,
    WorkBoardNotFoundError,
    WorkBoardValidationError,
)

logging_service = LoggingService()
logger = logging_service.get_logger(__name__)

work_board_router = APIRouter(prefix="/board", tags=["Work Board"])
work_board_admin_router = APIRouter(prefix="/admin/board", tags=["Work Board Admin"])


# ---------------------------------------------------------------------------
# Pydantic request/response models (in-router, per ToolOps precedent -- no schemas.py edit)
# ---------------------------------------------------------------------------


class WorkBoardNoteIn(BaseModel):
    """Request body for adding a note to a work-board item."""

    text: str = Field(..., min_length=1, description="Note body")
    author: Literal["operator", "agent"] = Field(default="operator", description="Note author")
    resolution: Optional[Literal["addressed", "followup_requested"]] = Field(default=None, description="Agent-only attention resolution")


class WorkBoardTangentIn(BaseModel):
    """Request body for creating a new tangent item."""

    title: str = Field(..., min_length=1, description="Tangent title/description")


class WorkBoardItemCreateIn(BaseModel):
    """Request body for creating a new work-board item in a given lane."""

    lane: Literal["now", "next", "branches", "prs", "tangents", "findings"] = Field(..., description="Target lane")
    title: str = Field(..., min_length=1, description="Item title")
    priority: Optional[int] = Field(default=None, description="NEXT lane only: 1..5 dense priority")
    branch: Optional[str] = Field(default=None, description="Free-text working-branch context (now/next)")
    started: Optional[str] = Field(default=None, description="ISO date (now lane)")
    captured: Optional[str] = Field(default=None, description="ISO date (tangents lane)")
    status: Optional[str] = Field(default=None, description="tangents: parked|promoted|dropped; findings: open|fixing|done|wontfix")
    verdict: Optional[str] = Field(default=None, description="branches: land|rebase|abandon|unknown; prs: review|land|close|unknown")
    severity: Optional[str] = Field(default=None, description="findings: advisory|warning|critical")
    source: Optional[str] = Field(default=None, description="findings provenance, e.g. 'repo-survey'")
    git_branch: Optional[str] = Field(default=None, description="branches lane key")
    git_ahead: Optional[int] = Field(default=None)
    git_behind: Optional[int] = Field(default=None)
    git_last_commit: Optional[str] = Field(default=None)
    pr_number: Optional[int] = Field(default=None, description="prs lane key")
    pr_state: Optional[str] = Field(default=None, description="Pass-through from gh: OPEN/CLOSED/MERGED")


class WorkBoardItemPatchIn(BaseModel):
    """Request body for patching mutable fields on a work-board item.

    ``id``, ``lane``, and ``attention`` are deliberately absent -- they are
    immutable / state-machine-only and rejected by the service if present.
    """

    title: Optional[str] = None
    priority: Optional[int] = None
    branch: Optional[str] = None
    started: Optional[str] = None
    captured: Optional[str] = None
    status: Optional[str] = None
    verdict: Optional[str] = None
    severity: Optional[str] = None
    source: Optional[str] = None
    git_branch: Optional[str] = None
    git_ahead: Optional[int] = None
    git_behind: Optional[int] = None
    git_last_commit: Optional[str] = None
    pr_number: Optional[int] = None
    pr_state: Optional[str] = None

    def to_patch_dict(self) -> Dict[str, Any]:
        """Return only the fields explicitly set on the request.

        Returns:
            Dict[str, Any]: Field name -> value for every field the client actually sent.
        """
        return self.model_dump(exclude_unset=True)


class WorkBoardSetNowIn(BaseModel):
    """Request body for POST /board/now/set."""

    item_id: str = Field(..., description="The next-lane item id to promote to NOW")
    displace: Optional[Literal["next", "tangent", "drop"]] = Field(default=None, description="Required only when NOW is occupied")


class WorkBoardPromoteIn(BaseModel):
    """Request body for POST /board/next/{item_id}/promote."""

    displace: Optional[Literal["next", "tangent", "drop"]] = Field(default=None, description="Required only when NOW is occupied")


class WorkBoardDemoteIn(BaseModel):
    """Request body for POST /board/now/demote."""

    to: Literal["next", "tangent"] = Field(..., description="Target lane for the demoted NOW item")
    priority: Optional[int] = Field(default=None, description="Target priority when to='next' (default 1)")


# ---------------------------------------------------------------------------
# Error mapping helper
# ---------------------------------------------------------------------------


def _raise_http(exc: WorkBoardError) -> None:
    """Translate a WorkBoardError subclass into the matching HTTPException.

    Args:
        exc: The caught service-layer exception.

    Raises:
        HTTPException: 404 for not-found, 409 for conflict, 422 for validation, 500 otherwise.
    """
    if isinstance(exc, WorkBoardNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, WorkBoardConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, WorkBoardValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# JSON REST API
# ---------------------------------------------------------------------------


@work_board_router.get("")
@require_permission("tools.read")
async def get_board(db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Return the full board (all lanes) plus the next-move projection.

    Args:
        db: Database session.
        _user: Authenticated user context (RBAC only; unused directly).

    Returns:
        Dict[str, Any]: The full board, grouped by lane.
    """
    return service.get_board(db)


@work_board_router.get("/next-move")
@require_permission("tools.read")
async def get_next_move(db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Return the recommended next move (NOW > NEXT top priority > highest-severity open finding > idle).

    Args:
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: ``{"move": ..., "lane": ..., "rationale": ...}``.
    """
    return service.next_move(db)


@work_board_router.get("/backlog")
@require_permission("tools.read")
async def get_backlog(db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> List[Dict[str, Any]]:
    """Return only ``needs_attention`` items with their full note thread, in backlog-pass order.

    Args:
        db: Database session.
        _user: Authenticated user context.

    Returns:
        List[Dict[str, Any]]: Ordered backlog items.
    """
    return service.get_backlog(db)


@work_board_router.get("/items/{item_id}")
@require_permission("tools.read")
async def get_item(item_id: str, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Fetch a single work-board item by id.

    Args:
        item_id: Item id.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The item.

    Raises:
        HTTPException: 404 if the item does not exist.
    """
    try:
        return service._item_to_dict(service.get_item(db, item_id))  # pylint: disable=protected-access
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.post("/items", status_code=status.HTTP_201_CREATED)
@require_permission("admin.system_config")
async def create_item(body: WorkBoardItemCreateIn, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Create a new work-board item in the given lane.

    Args:
        body: Item fields, including the target ``lane``.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The newly created item.

    Raises:
        HTTPException: 422 on invalid lane/enum/title, 409 on single-NOW or NEXT-cap conflict.
    """
    payload = body.model_dump(exclude={"lane"}, exclude_unset=True)
    try:
        item = service.create_item(db, body.lane, payload)
        return service._item_to_dict(item)  # pylint: disable=protected-access
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.patch("/items/{item_id}")
@require_permission("admin.system_config")
async def update_item(item_id: str, body: WorkBoardItemPatchIn, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Patch mutable fields on a work-board item.

    Args:
        item_id: Item to update.
        body: Patch fields (id/lane/attention are rejected by the service if present).
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The updated item.

    Raises:
        HTTPException: 404 if not found, 422 on an illegal/invalid field.
    """
    try:
        item = service.update_item(db, item_id, body.to_patch_dict(), author="operator")
        return service._item_to_dict(item)  # pylint: disable=protected-access
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_permission("admin.system_config")
async def delete_item(item_id: str, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> None:
    """Delete a work-board item (notes cascade).

    Args:
        item_id: Item to delete.
        db: Database session.
        _user: Authenticated user context.

    Raises:
        HTTPException: 404 if the item does not exist.
    """
    try:
        service.delete_item(db, item_id)
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.post("/items/{item_id}/notes", status_code=status.HTTP_201_CREATED)
@require_permission("admin.system_config")
async def add_note(item_id: str, body: WorkBoardNoteIn, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Append a note to an item, applying the attention state machine.

    Args:
        item_id: Item to comment on.
        body: Note text, author, and optional resolution.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The newly created note.

    Raises:
        HTTPException: 404 if not found, 409 on an illegal resolution transition,
            422 on empty text / invalid author-resolution combination / missing question mark.
    """
    try:
        note = service.add_note(db, item_id, body.text, author=body.author, resolution=body.resolution)
        return {"id": note.id, "item_id": note.item_id, "at": note.at, "text": note.text, "author": note.author}
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.post("/items/{item_id}/acknowledge")
@require_permission("admin.system_config")
async def acknowledge_item(item_id: str, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Acknowledge an ``addressed`` item, closing the backlog loop.

    Not exposed as an MCP tool (§6.2) -- a backlog runner can never launder
    its own ``addressed`` into ``acknowledged``; closing the loop stays a human act.

    Args:
        item_id: Item to acknowledge.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The acknowledged item.

    Raises:
        HTTPException: 404 if not found, 409 if attention is not currently ``addressed``.
    """
    try:
        item = service.acknowledge(db, item_id, author="operator")
        return service._item_to_dict(item)  # pylint: disable=protected-access
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.post("/tangents", status_code=status.HTTP_201_CREATED)
@require_permission("admin.system_config")
async def add_tangent(body: WorkBoardTangentIn, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Create a new tangent item.

    Args:
        body: Tangent title.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The newly created tangent.
    """
    try:
        item = service.add_tangent(db, body.title)
        return service._item_to_dict(item)  # pylint: disable=protected-access
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.post("/now/set")
@require_permission("admin.system_config")
async def set_now(body: WorkBoardSetNowIn, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Set NOW to a specific next-lane item.

    Args:
        body: Target item id and optional displace mode.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The promoted item.

    Raises:
        HTTPException: 404 if not found, 422 if not in the next lane or displace is invalid,
            409 if NOW is occupied and displace is missing.
    """
    try:
        item = service.set_now(db, body.item_id, displace=body.displace, author="operator")
        return service._item_to_dict(item)  # pylint: disable=protected-access
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.post("/next/{item_id}/promote")
@require_permission("admin.system_config")
async def promote_next(item_id: str, body: WorkBoardPromoteIn, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Promote a next-lane item to NOW.

    Args:
        item_id: The next-lane item to promote.
        body: Optional displace mode (required only if NOW is occupied).
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The promoted item.

    Raises:
        HTTPException: 404 if not found, 422 if not in the next lane or displace is invalid,
            409 if NOW is occupied and displace is missing. The 409 body names the occupied
            item and the three displacement options so agent clients can self-correct.
    """
    try:
        item = service.promote_next(db, item_id, displace=body.displace, author="operator")
        return service._item_to_dict(item)  # pylint: disable=protected-access
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.post("/now/demote")
@require_permission("admin.system_config")
async def demote_now(body: WorkBoardDemoteIn, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Demote the current NOW item to ``next`` or ``tangents``.

    Args:
        body: Target lane (``to``) and optional priority.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: The demoted item.

    Raises:
        HTTPException: 404 if NOW is empty, 422 if ``to`` is invalid, 409 if the next lane is full.
    """
    try:
        item = service.demote_now(db, body.to, priority=body.priority, author="operator")
        return service._item_to_dict(item)  # pylint: disable=protected-access
    except WorkBoardError as exc:
        _raise_http(exc)


@work_board_router.post("/refresh-git")
@require_permission("admin.system_config")
async def refresh_git(db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> Dict[str, Any]:
    """Refresh ``branches``/``prs`` lane items from the configured git repo (best-effort).

    Args:
        db: Database session.
        _user: Authenticated user context.

    Returns:
        Dict[str, Any]: ``{"refreshed": bool, ...}`` -- 200 even when refresh is a soft no-op
        (e.g. ``work_board_git_repo`` unset).
    """
    # First-Party
    from mcpgateway.config import settings  # pylint: disable=import-outside-toplevel

    return service.refresh_git(db, settings.work_board_git_repo)


# ---------------------------------------------------------------------------
# Admin UI sub-router (spec §5) -- HTMX partials, no terminal required.
#
# Every endpoint here renders the *same* work_board_partial.html template
# (full board context) and returns it as the HTMX response body, exactly the
# ToolOps wrapper pattern: one panel-content div re-renders wholesale on any
# control's hx-get/hx-post/hx-patch. All writes hard-code author="operator"
# -- the UI IS the operator; author is never a client-supplied field here.
# ---------------------------------------------------------------------------


def _render_board_partial(request: Request, db, error: Optional[str] = None) -> HTMLResponse:
    """Render ``work_board_partial.html`` with the full board context.

    Args:
        request: Incoming request (used to resolve ``app.state.templates`` and ``root_path``).
        db: Database session.
        error: Optional inline error message (e.g. a caught 409) to render as a banner.

    Returns:
        HTMLResponse: The rendered partial.
    """
    # First-Party
    from mcpgateway.utils.paths import resolve_root_path  # pylint: disable=import-outside-toplevel

    board = service.get_board(db)
    return request.app.state.templates.TemplateResponse(
        request,
        "work_board_partial.html",
        {
            "request": request,
            "board": board,
            "root_path": resolve_root_path(request),
            "error": error,
        },
    )


def _board_partial_after(request: Request, db, action: Any) -> HTMLResponse:
    """Run a service mutation and return the re-rendered board partial, or an inline error banner.

    Args:
        request: Incoming request.
        db: Database session.
        action: A zero-argument callable performing the service-layer mutation.

    Returns:
        HTMLResponse: The re-rendered partial on success, or with an inline error banner
        on a caught :class:`WorkBoardError` (409/422/404 messages render in-page rather
        than surfacing as a raw HTMX error response, since these are UI form posts).
    """
    try:
        action()
    except WorkBoardError as exc:
        return _render_board_partial(request, db, error=str(exc))
    return _render_board_partial(request, db)


@work_board_admin_router.get("/partial", response_class=HTMLResponse)
@require_permission("tools.read", allow_admin_bypass=False)
async def admin_board_partial(request: Request, db=Depends(get_db), _user=Depends(get_current_user_with_permissions)) -> HTMLResponse:
    """Render the full work-board admin partial (spec §5.1).

    Auth posture mirrors ``admin_tool_ops_partial``: ``tools.read`` with
    ``allow_admin_bypass=False``, served through the admin API surface.

    Args:
        request: Incoming request.
        db: Database session.
        _user: Authenticated user context (RBAC only; unused directly).

    Returns:
        HTMLResponse: The rendered board partial.
    """
    return _render_board_partial(request, db)


@work_board_admin_router.post("/note", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_add_note(
    request: Request,
    item_id: str = Form(...),
    text: str = Form(...),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Add an operator note (plain comment, or a reply to a ``followup_requested`` question).

    Posts to the same endpoint regardless of the item's current attention state --
    there is no dedicated reply endpoint; an operator note on a ``followup_requested``
    item flips it back to ``needs_attention`` via the ordinary state machine.

    Args:
        request: Incoming request.
        item_id: Target item id.
        text: Note body.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial.
    """
    return _board_partial_after(request, db, lambda: service.add_note(db, item_id, text, author="operator"))


@work_board_admin_router.post("/tangent", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_add_tangent(
    request: Request,
    title: str = Form(...),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Create a new tangent item from the inline tangent-lane form.

    Args:
        request: Incoming request.
        title: Tangent title/description.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial.
    """
    return _board_partial_after(request, db, lambda: service.add_tangent(db, title))


@work_board_admin_router.post("/promote", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_promote_next(
    request: Request,
    item_id: str = Form(...),
    displace: Optional[str] = Form(default=None),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Promote a NEXT item to NOW, optionally displacing the occupied NOW item.

    Args:
        request: Incoming request.
        item_id: The next-lane item to promote.
        displace: ``"next"``/``"tangent"``/``"drop"`` -- required only when NOW is occupied;
            the inline displace ``<select>`` only renders when NOW exists, server 409 is the backstop.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial, or an inline 409 error banner.
    """
    return _board_partial_after(request, db, lambda: service.promote_next(db, item_id, displace=displace, author="operator"))


@work_board_admin_router.post("/demote", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_demote_now(
    request: Request,
    to: str = Form(...),
    priority: Optional[int] = Form(default=None),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Demote the current NOW item to ``next`` or ``tangents``.

    Args:
        request: Incoming request.
        to: ``"next"`` or ``"tangent"``.
        priority: Target priority when ``to="next"`` (default 1, via the NOW card's priority select).
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial, or an inline error banner.
    """
    return _board_partial_after(request, db, lambda: service.demote_now(db, to, priority=priority, author="operator"))


@work_board_admin_router.patch("/set-verdict", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_set_verdict(
    request: Request,
    item_id: str = Form(...),
    verdict: str = Form(...),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Patch the ``verdict`` field on a branches/prs-lane item (hx-patch on ``<select>`` change).

    Args:
        request: Incoming request.
        item_id: Target item id.
        verdict: New verdict value (validated against the item's lane by the service).
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial, or an inline error banner.
    """
    return _board_partial_after(request, db, lambda: service.update_item(db, item_id, {"verdict": verdict}, author="operator"))


@work_board_admin_router.patch("/set-status", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_set_status(
    request: Request,
    item_id: str = Form(...),
    status_value: str = Form(..., alias="status"),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Patch the ``status`` field on a tangents/findings-lane item (hx-patch on ``<select>`` change).

    Args:
        request: Incoming request.
        item_id: Target item id.
        status_value: New status value (validated against the item's lane by the service).
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial, or an inline error banner.
    """
    return _board_partial_after(request, db, lambda: service.update_item(db, item_id, {"status": status_value}, author="operator"))


@work_board_admin_router.patch("/set-severity", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_set_severity(
    request: Request,
    item_id: str = Form(...),
    severity: str = Form(...),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Patch the ``severity`` field on a findings-lane item (hx-patch on ``<select>`` change).

    Args:
        request: Incoming request.
        item_id: Target item id.
        severity: New severity value (``advisory``/``warning``/``critical``).
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial, or an inline error banner.
    """
    return _board_partial_after(request, db, lambda: service.update_item(db, item_id, {"severity": severity}, author="operator"))


@work_board_admin_router.patch("/set-priority", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_set_priority(
    request: Request,
    item_id: str = Form(...),
    priority: int = Form(...),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Patch the ``priority`` field on a NEXT-lane item (hx-patch on ``<select>`` change).

    Args:
        request: Incoming request.
        item_id: Target item id.
        priority: New dense priority 1..5 (the service renumbers the whole lane).
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial, or an inline error banner.
    """
    return _board_partial_after(request, db, lambda: service.update_item(db, item_id, {"priority": priority}, author="operator"))


@work_board_admin_router.post("/acknowledge", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_acknowledge(
    request: Request,
    item_id: str = Form(...),
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Acknowledge an ``addressed`` item, closing the backlog loop (the per-badge Acknowledge button).

    Args:
        request: Incoming request.
        item_id: Target item id.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial, or an inline error banner.
    """
    return _board_partial_after(request, db, lambda: service.acknowledge(db, item_id, author="operator"))


@work_board_admin_router.post("/refresh", response_class=HTMLResponse)
@require_permission("admin.system_config", allow_admin_bypass=False)
async def admin_refresh_git(
    request: Request,
    db=Depends(get_db),
    _user=Depends(get_current_user_with_permissions),
) -> HTMLResponse:
    """Refresh ``branches``/``prs`` lane items from the configured git repo (the "Refresh from git" button).

    Args:
        request: Incoming request.
        db: Database session.
        _user: Authenticated user context.

    Returns:
        HTMLResponse: The re-rendered board partial (soft no-op renders normally, not as an error,
        matching the JSON endpoint's 200-even-when-unconfigured contract).
    """
    # First-Party
    from mcpgateway.config import settings  # pylint: disable=import-outside-toplevel

    service.refresh_git(db, settings.work_board_git_repo)
    return _render_board_partial(request, db)


# NOTE: __all__ documents the frozen enum vocabulary re-exported for the MCP
# tool-registration script (§4) and the admin partial template context.
__all__ = [
    "work_board_router",
    "work_board_admin_router",
    "LANES",
    "BRANCH_VERDICTS",
    "PR_VERDICTS",
    "TANGENT_STATUSES",
    "FINDING_STATUSES",
    "FINDING_SEVERITIES",
    "NOTE_AUTHORS",
    "ATTENTION_STATES",
]
