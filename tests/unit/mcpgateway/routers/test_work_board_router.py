# -*- coding: utf-8 -*-
"""Location: ./tests/unit/mcpgateway/routers/test_work_board_router.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

Unit tests for the work-board router (mcpgateway.routers.work_board_router).

Uses an in-memory SQLite database and the real work_board_service so tests
exercise the full stack from router handler down to SQL, with no mocked
service responses. RBAC decorators are bypassed per-class via
tests.utils.rbac_mocks so tests call the router coroutines directly.

Tests cover:
    - Status-code mapping: 404/409/422 via HTTPException
    - get_board / get_next_move / get_backlog happy paths
    - create_item / update_item / delete_item / add_note / add_tangent
    - set_now / promote_next / demote_now, including the displace-missing 409
    - acknowledge endpoint and resolution-legality 409/422s
    - refresh_git soft-fail (unconfigured repo)
"""

# Standard
from unittest.mock import MagicMock

# Third-Party
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# First-Party
from mcpgateway.db import Base
from mcpgateway.db_work_board import WorkBoardItem  # noqa: F401 -- ensures model registration for create_all
from mcpgateway.routers.work_board_router import (
    WorkBoardDemoteIn,
    WorkBoardItemCreateIn,
    WorkBoardItemPatchIn,
    WorkBoardNoteIn,
    WorkBoardPromoteIn,
    WorkBoardSetNowIn,
    WorkBoardTangentIn,
    acknowledge_item,
    add_note,
    add_tangent,
    create_item,
    delete_item,
    demote_now,
    get_backlog,
    get_board,
    get_item,
    get_next_move,
    promote_next,
    set_now,
    update_item,
)
from tests.utils.rbac_mocks import patch_rbac_decorators, restore_rbac_decorators


@pytest.fixture
def db_session():
    """In-memory SQLite session shared across all connections within one test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def user_ctx():
    """Authenticated admin user context (RBAC bypassed at the class level anyway)."""
    return {"email": "admin@example.com", "is_admin": True, "token_teams": None}


@pytest.mark.usefixtures("db_session")
class TestWorkBoardRouter:
    """Router tests using in-memory SQLite and the real work_board_service."""

    @pytest.fixture(autouse=True)
    def setup_rbac_mocks(self):
        """Bypass RBAC decorators for every test in this class."""
        originals = patch_rbac_decorators()
        yield
        restore_rbac_decorators(originals)

    # ------------------------------------------------------------------
    # GET /board, /board/next-move, /board/backlog
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_board_empty(self, db_session, user_ctx):
        """An empty board returns all-empty lanes and an idle next_move."""
        result = await get_board(db=db_session, _user=user_ctx)
        assert result["now"] is None
        assert result["next"] == []
        assert result["next_move"]["move"] is None

    @pytest.mark.asyncio
    async def test_get_next_move(self, db_session, user_ctx):
        """next-move endpoint proxies the service projection."""
        result = await get_next_move(db=db_session, _user=user_ctx)
        assert result["lane"] is None

    @pytest.mark.asyncio
    async def test_get_backlog_empty(self, db_session, user_ctx):
        """An empty board's backlog is an empty list."""
        result = await get_backlog(db=db_session, _user=user_ctx)
        assert result == []

    # ------------------------------------------------------------------
    # GET /board/items/{id}, POST /board/items
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_item_not_found_maps_404(self, db_session, user_ctx):
        """A missing item maps WorkBoardNotFoundError to HTTP 404."""
        with pytest.raises(HTTPException) as exc_info:
            await get_item("w-999", db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_item_success(self, db_session, user_ctx):
        """Creating a next-lane item returns the created dict."""
        body = WorkBoardItemCreateIn(lane="next", title="New task")
        result = await create_item(body, db=db_session, _user=user_ctx)
        assert result["lane"] == "next"
        assert result["title"] == "New task"
        assert result["priority"] == 1

    @pytest.mark.asyncio
    async def test_create_item_validation_maps_422(self, db_session, user_ctx):
        """An invalid enum value maps WorkBoardValidationError to HTTP 422."""
        body = WorkBoardItemCreateIn(lane="branches", title="feature-x", verdict="bogus")
        with pytest.raises(HTTPException) as exc_info:
            await create_item(body, db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_create_second_now_maps_409(self, db_session, user_ctx):
        """A second now-lane item maps WorkBoardConflictError to HTTP 409."""
        await create_item(WorkBoardItemCreateIn(lane="now", title="First"), db=db_session, _user=user_ctx)
        with pytest.raises(HTTPException) as exc_info:
            await create_item(WorkBoardItemCreateIn(lane="now", title="Second"), db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 409

    # ------------------------------------------------------------------
    # PATCH /board/items/{id}, DELETE /board/items/{id}
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_update_item_success(self, db_session, user_ctx):
        """A title patch is applied."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="Old"), db=db_session, _user=user_ctx)
        result = await update_item(created["id"], WorkBoardItemPatchIn(title="New"), db=db_session, _user=user_ctx)
        assert result["title"] == "New"

    @pytest.mark.asyncio
    async def test_update_item_not_found_maps_404(self, db_session, user_ctx):
        """Patching a nonexistent item maps to HTTP 404."""
        with pytest.raises(HTTPException) as exc_info:
            await update_item("w-999", WorkBoardItemPatchIn(title="x"), db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_item_success(self, db_session, user_ctx):
        """Deleting an item succeeds and a subsequent GET 404s."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="X"), db=db_session, _user=user_ctx)
        await delete_item(created["id"], db=db_session, _user=user_ctx)
        with pytest.raises(HTTPException) as exc_info:
            await get_item(created["id"], db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 404

    # ------------------------------------------------------------------
    # POST /board/items/{id}/notes, /board/items/{id}/acknowledge
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_add_note_operator_flips_attention(self, db_session, user_ctx):
        """An operator note flips the item to needs_attention, visible via GET."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="X"), db=db_session, _user=user_ctx)
        await add_note(created["id"], WorkBoardNoteIn(text="please check"), db=db_session, _user=user_ctx)
        item = await get_item(created["id"], db=db_session, _user=user_ctx)
        assert item["attention"] == "needs_attention"

    @pytest.mark.asyncio
    async def test_add_note_resolution_conflict_maps_409(self, db_session, user_ctx):
        """An agent resolution on a non-needs_attention item maps to HTTP 409."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="X"), db=db_session, _user=user_ctx)
        with pytest.raises(HTTPException) as exc_info:
            await add_note(created["id"], WorkBoardNoteIn(text="done", author="agent", resolution="addressed"), db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_add_note_followup_without_question_mark_maps_422(self, db_session, user_ctx):
        """followup_requested without a question mark maps to HTTP 422."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="X"), db=db_session, _user=user_ctx)
        await add_note(created["id"], WorkBoardNoteIn(text="please check"), db=db_session, _user=user_ctx)
        with pytest.raises(HTTPException) as exc_info:
            await add_note(created["id"], WorkBoardNoteIn(text="no question", author="agent", resolution="followup_requested"), db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_acknowledge_success(self, db_session, user_ctx):
        """acknowledge succeeds after an agent 'addressed' resolution."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="X"), db=db_session, _user=user_ctx)
        await add_note(created["id"], WorkBoardNoteIn(text="please check"), db=db_session, _user=user_ctx)
        await add_note(created["id"], WorkBoardNoteIn(text="done", author="agent", resolution="addressed"), db=db_session, _user=user_ctx)
        result = await acknowledge_item(created["id"], db=db_session, _user=user_ctx)
        assert result["attention"] == "acknowledged"

    @pytest.mark.asyncio
    async def test_acknowledge_illegal_maps_409(self, db_session, user_ctx):
        """acknowledge on a non-addressed item maps to HTTP 409."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="X"), db=db_session, _user=user_ctx)
        with pytest.raises(HTTPException) as exc_info:
            await acknowledge_item(created["id"], db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 409

    # ------------------------------------------------------------------
    # POST /board/tangents
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_add_tangent(self, db_session, user_ctx):
        """A tangent is created parked and captured today."""
        result = await add_tangent(WorkBoardTangentIn(title="Investigate Y"), db=db_session, _user=user_ctx)
        assert result["lane"] == "tangents"
        assert result["status"] == "parked"

    # ------------------------------------------------------------------
    # POST /board/now/set, /board/next/{id}/promote, /board/now/demote
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_set_now(self, db_session, user_ctx):
        """set_now promotes a next-lane item when NOW is empty."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="X"), db=db_session, _user=user_ctx)
        result = await set_now(WorkBoardSetNowIn(item_id=created["id"]), db=db_session, _user=user_ctx)
        assert result["lane"] == "now"

    @pytest.mark.asyncio
    async def test_promote_next_missing_displace_maps_409(self, db_session, user_ctx):
        """Promoting into an occupied NOW without displace maps to HTTP 409."""
        first = await create_item(WorkBoardItemCreateIn(lane="next", title="A"), db=db_session, _user=user_ctx)
        second = await create_item(WorkBoardItemCreateIn(lane="next", title="B"), db=db_session, _user=user_ctx)
        await promote_next(first["id"], WorkBoardPromoteIn(), db=db_session, _user=user_ctx)
        with pytest.raises(HTTPException) as exc_info:
            await promote_next(second["id"], WorkBoardPromoteIn(), db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_promote_next_with_displace_next(self, db_session, user_ctx):
        """Promoting with displace='next' succeeds and swaps the old NOW item back."""
        first = await create_item(WorkBoardItemCreateIn(lane="next", title="A"), db=db_session, _user=user_ctx)
        second = await create_item(WorkBoardItemCreateIn(lane="next", title="B"), db=db_session, _user=user_ctx)
        await promote_next(first["id"], WorkBoardPromoteIn(), db=db_session, _user=user_ctx)
        result = await promote_next(second["id"], WorkBoardPromoteIn(displace="next"), db=db_session, _user=user_ctx)
        assert result["lane"] == "now"
        displaced = await get_item(first["id"], db=db_session, _user=user_ctx)
        assert displaced["lane"] == "next"

    @pytest.mark.asyncio
    async def test_demote_now_success(self, db_session, user_ctx):
        """demote_now moves the current NOW item to next."""
        created = await create_item(WorkBoardItemCreateIn(lane="next", title="A"), db=db_session, _user=user_ctx)
        await set_now(WorkBoardSetNowIn(item_id=created["id"]), db=db_session, _user=user_ctx)
        result = await demote_now(WorkBoardDemoteIn(to="next"), db=db_session, _user=user_ctx)
        assert result["lane"] == "next"

    @pytest.mark.asyncio
    async def test_demote_now_empty_maps_404(self, db_session, user_ctx):
        """demote_now with no NOW item maps WorkBoardNotFoundError to HTTP 404."""
        with pytest.raises(HTTPException) as exc_info:
            await demote_now(WorkBoardDemoteIn(to="next"), db=db_session, _user=user_ctx)
        assert exc_info.value.status_code == 404

    # ------------------------------------------------------------------
    # POST /board/refresh-git
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_refresh_git_soft_fail_when_unconfigured(self, db_session, user_ctx, monkeypatch):
        """With work_board_git_repo unset, refresh-git returns refreshed=False, not an error."""
        # First-Party
        from mcpgateway.routers.work_board_router import refresh_git

        # Hermetic: the router reads settings.work_board_git_repo at call time, and a
        # developer .env may populate it (see WORK_BOARD_GIT_REPO). Force it empty so
        # this test exercises the unconfigured soft-fail path regardless of environment.
        monkeypatch.setattr("mcpgateway.config.settings.work_board_git_repo", "")

        result = await refresh_git(db=db_session, _user=user_ctx)
        assert result["refreshed"] is False
