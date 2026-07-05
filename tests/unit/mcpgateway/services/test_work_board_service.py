# -*- coding: utf-8 -*-
"""Location: ./tests/unit/mcpgateway/services/test_work_board_service.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

Unit tests for the work-board service (mcpgateway.services.work_board_service).

Tests cover:
    - CRUD: create/get/update/delete items, add_note, add_tangent
    - Single-NOW invariant (service-level 409)
    - NEXT cap (5) + dense renumbering
    - Enum rejection per lane
    - promote_next with an occupied NOW: all three displace modes + missing-displace 409
    - demote_now: to next and to tangent, including next-full 409
    - next_move ordering: now > next-priority > finding-severity > idle
    - refresh_git: soft-fail when repo path is unset/invalid; happy path with a tmp git repo
    - Attention state machine (§6.2): operator note -> needs_attention (manual note, named-op
      system note, status/verdict-change note); agent note without resolution leaves state
      untouched; addressed/followup_requested legal only from needs_attention (409 otherwise);
      followup_requested without a question mark -> 422; operator note with resolution -> 422;
      operator reply on followup_requested -> needs_attention; acknowledge only from addressed;
      update_item rejects an attention patch (422); every flip has a same-transaction note.
    - get_backlog: ordering, exclusion of non-needs_attention items, empty-second-pass no-op.
"""

# Standard
import subprocess

# Third-Party
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# First-Party
from mcpgateway.db import Base
from mcpgateway.db_work_board import WorkBoardItem  # noqa: F401 -- ensures model registration for create_all
import mcpgateway.services.work_board_service as wbs


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


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCrud:
    """Basic create/get/update/delete/add_note/add_tangent behavior."""

    def test_create_item_next_lane_assigns_priority(self, db_session):
        """New next-lane items get sequential dense priorities."""
        i1 = wbs.create_item(db_session, "next", {"title": "First"})
        i2 = wbs.create_item(db_session, "next", {"title": "Second"})
        assert i1.priority == 1
        assert i2.priority == 2

    def test_create_item_requires_title(self, db_session):
        """Blank title is rejected."""
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.create_item(db_session, "next", {"title": "   "})

    def test_create_item_unknown_lane_rejected(self, db_session):
        """An unrecognized lane raises a validation error."""
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.create_item(db_session, "bogus", {"title": "x"})

    def test_get_item_not_found(self, db_session):
        """Fetching a nonexistent id raises WorkBoardNotFoundError."""
        with pytest.raises(wbs.WorkBoardNotFoundError):
            wbs.get_item(db_session, "w-999")

    def test_update_item_title(self, db_session):
        """A simple title patch is applied and persisted."""
        item = wbs.create_item(db_session, "next", {"title": "Old"})
        updated = wbs.update_item(db_session, item.id, {"title": "New"})
        assert updated.title == "New"

    def test_update_item_rejects_id(self, db_session):
        """Patching id is illegal."""
        item = wbs.create_item(db_session, "next", {"title": "X"})
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.update_item(db_session, item.id, {"id": "w-999"})

    def test_update_item_rejects_lane(self, db_session):
        """Patching lane is illegal -- only named ops move lanes."""
        item = wbs.create_item(db_session, "next", {"title": "X"})
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.update_item(db_session, item.id, {"lane": "now"})

    def test_update_item_rejects_attention(self, db_session):
        """Patching attention directly is illegal -- state machine only."""
        item = wbs.create_item(db_session, "next", {"title": "X"})
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.update_item(db_session, item.id, {"attention": "acknowledged"})

    def test_update_item_enum_rejection_per_lane(self, db_session):
        """An illegal verdict value for the branches lane is rejected."""
        item = wbs.create_item(db_session, "branches", {"title": "feature-x"})
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.update_item(db_session, item.id, {"verdict": "bogus"})

    def test_delete_item_cascades_notes(self, db_session):
        """Deleting an item removes it; notes cascade via FK ondelete."""
        item = wbs.create_item(db_session, "next", {"title": "X"})
        wbs.add_note(db_session, item.id, "a note", author="operator")
        wbs.delete_item(db_session, item.id)
        with pytest.raises(wbs.WorkBoardNotFoundError):
            wbs.get_item(db_session, item.id)

    def test_delete_item_not_found(self, db_session):
        """Deleting a nonexistent id raises WorkBoardNotFoundError."""
        with pytest.raises(wbs.WorkBoardNotFoundError):
            wbs.delete_item(db_session, "w-999")

    def test_add_tangent_defaults(self, db_session):
        """add_tangent creates a parked tangent captured today."""
        tangent = wbs.add_tangent(db_session, "Investigate X")
        assert tangent.lane == "tangents"
        assert tangent.status == "parked"
        assert tangent.captured is not None


# ---------------------------------------------------------------------------
# Single-NOW invariant
# ---------------------------------------------------------------------------


class TestSingleNow:
    """The board may hold at most one NOW item at a time."""

    def test_create_now_when_occupied_conflicts(self, db_session):
        """Creating a second now-lane item raises a 409-mapped conflict."""
        wbs.create_item(db_session, "now", {"title": "First"})
        with pytest.raises(wbs.WorkBoardConflictError):
            wbs.create_item(db_session, "now", {"title": "Second"})


# ---------------------------------------------------------------------------
# NEXT cap + dense renumbering
# ---------------------------------------------------------------------------


class TestNextCap:
    """NEXT lane is capped at 5 items with dense 1..n priority renumbering."""

    def test_next_cap_at_five(self, db_session):
        """A 6th next-lane item is rejected with a conflict."""
        for i in range(5):
            wbs.create_item(db_session, "next", {"title": f"item-{i}"})
        with pytest.raises(wbs.WorkBoardConflictError):
            wbs.create_item(db_session, "next", {"title": "overflow"})

    def test_delete_renumbers_next(self, db_session):
        """Deleting a middle next-lane item compacts priorities to 1..n."""
        items = [wbs.create_item(db_session, "next", {"title": f"item-{i}"}) for i in range(3)]
        wbs.delete_item(db_session, items[0].id)
        remaining = db_session.query(WorkBoardItem).filter(WorkBoardItem.lane == "next").order_by(WorkBoardItem.priority).all()
        assert [r.priority for r in remaining] == [1, 2]


# ---------------------------------------------------------------------------
# promote_next / set_now / demote_now
# ---------------------------------------------------------------------------


class TestPromoteDemote:
    """Named lane-transition operations, including the displace contract."""

    def test_promote_next_no_now_occupied(self, db_session):
        """Promoting when NOW is empty needs no displace argument."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        promoted = wbs.promote_next(db_session, item.id)
        assert promoted.lane == "now"
        assert promoted.started is not None
        assert promoted.priority is None

    def test_promote_next_missing_displace_conflicts(self, db_session):
        """Promoting into an occupied NOW without displace raises a conflict naming the occupant."""
        first = wbs.create_item(db_session, "next", {"title": "A"})
        second = wbs.create_item(db_session, "next", {"title": "B"})
        wbs.promote_next(db_session, first.id)
        with pytest.raises(wbs.WorkBoardConflictError) as exc_info:
            wbs.promote_next(db_session, second.id)
        assert first.id in str(exc_info.value)

    def test_promote_next_displace_next(self, db_session):
        """displace='next' swaps the old NOW item back into next at its old priority slot."""
        first = wbs.create_item(db_session, "next", {"title": "A"})
        second = wbs.create_item(db_session, "next", {"title": "B"})
        wbs.promote_next(db_session, first.id)
        promoted = wbs.promote_next(db_session, second.id, displace="next")
        assert promoted.lane == "now"
        displaced = wbs.get_item(db_session, first.id)
        assert displaced.lane == "next"
        assert displaced.priority == 1
        assert any("Displaced from NOW -> next" in n.text for n in displaced.notes)

    def test_promote_next_displace_tangent(self, db_session):
        """displace='tangent' parks the old NOW item."""
        first = wbs.create_item(db_session, "next", {"title": "A"})
        second = wbs.create_item(db_session, "next", {"title": "B"})
        wbs.promote_next(db_session, first.id)
        wbs.promote_next(db_session, second.id, displace="tangent")
        displaced = wbs.get_item(db_session, first.id)
        assert displaced.lane == "tangents"
        assert displaced.status == "parked"
        assert any("Displaced from NOW -> parked" in n.text for n in displaced.notes)

    def test_promote_next_displace_drop(self, db_session):
        """displace='drop' deletes the old NOW item -- only via this explicit value."""
        first = wbs.create_item(db_session, "next", {"title": "A"})
        second = wbs.create_item(db_session, "next", {"title": "B"})
        wbs.promote_next(db_session, first.id)
        wbs.promote_next(db_session, second.id, displace="drop")
        with pytest.raises(wbs.WorkBoardNotFoundError):
            wbs.get_item(db_session, first.id)

    def test_promote_next_invalid_displace_value(self, db_session):
        """An unrecognized displace value is a validation error, not silently ignored."""
        first = wbs.create_item(db_session, "next", {"title": "A"})
        second = wbs.create_item(db_session, "next", {"title": "B"})
        wbs.promote_next(db_session, first.id)
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.promote_next(db_session, second.id, displace="bogus")

    def test_promote_next_not_in_next_lane(self, db_session):
        """Promoting an item that isn't in the next lane is a validation error."""
        item = wbs.create_item(db_session, "tangents", {"title": "T"})
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.promote_next(db_session, item.id)

    def test_set_now_delegates_to_promote_next(self, db_session):
        """set_now behaves identically to promote_next."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        promoted = wbs.set_now(db_session, item.id)
        assert promoted.lane == "now"

    def test_demote_now_to_next(self, db_session):
        """Demoting NOW to next assigns default priority 1 and appends a system note."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.promote_next(db_session, item.id)
        demoted = wbs.demote_now(db_session, to="next")
        assert demoted.lane == "next"
        assert demoted.priority == 1
        assert any("Demoted from NOW" in n.text for n in demoted.notes)

    def test_demote_now_to_tangent(self, db_session):
        """Demoting NOW to tangent parks it."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.promote_next(db_session, item.id)
        demoted = wbs.demote_now(db_session, to="tangent")
        assert demoted.lane == "tangents"
        assert demoted.status == "parked"

    def test_demote_now_empty_raises_not_found(self, db_session):
        """Demoting when NOW is empty raises WorkBoardNotFoundError."""
        with pytest.raises(wbs.WorkBoardNotFoundError):
            wbs.demote_now(db_session, to="next")

    def test_demote_now_invalid_to(self, db_session):
        """An unrecognized 'to' value is a validation error."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.promote_next(db_session, item.id)
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.demote_now(db_session, to="bogus")

    def test_demote_now_to_next_when_full_conflicts(self, db_session):
        """Demoting to next when the next lane is already at cap raises a conflict."""
        promoted_item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.promote_next(db_session, promoted_item.id)
        for i in range(5):
            wbs.create_item(db_session, "next", {"title": f"filler-{i}"})
        with pytest.raises(wbs.WorkBoardConflictError):
            wbs.demote_now(db_session, to="next")


# ---------------------------------------------------------------------------
# next_move ordering
# ---------------------------------------------------------------------------


class TestNextMove:
    """next_move: now > next-priority > finding-severity > idle."""

    def test_idle_when_board_empty(self, db_session):
        """An empty board reports idle with no move."""
        result = wbs.next_move(db_session)
        assert result["move"] is None
        assert result["lane"] is None

    def test_prefers_open_finding_when_no_now_or_next(self, db_session):
        """With no NOW/NEXT, the highest-severity open finding is recommended."""
        wbs.create_item(db_session, "findings", {"title": "minor", "severity": "advisory", "status": "open"})
        critical = wbs.create_item(db_session, "findings", {"title": "urgent", "severity": "critical", "status": "open"})
        result = wbs.next_move(db_session)
        assert result["lane"] == "findings"
        assert result["move"]["id"] == critical.id

    def test_prefers_next_over_findings(self, db_session):
        """A NEXT item outranks any open finding."""
        wbs.create_item(db_session, "findings", {"title": "urgent", "severity": "critical", "status": "open"})
        next_item = wbs.create_item(db_session, "next", {"title": "top"})
        result = wbs.next_move(db_session)
        assert result["lane"] == "next"
        assert result["move"]["id"] == next_item.id

    def test_prefers_now_over_everything(self, db_session):
        """An occupied NOW always wins."""
        wbs.create_item(db_session, "findings", {"title": "urgent", "severity": "critical", "status": "open"})
        next_item = wbs.create_item(db_session, "next", {"title": "top"})
        now_item = wbs.promote_next(db_session, next_item.id)
        result = wbs.next_move(db_session)
        assert result["lane"] == "now"
        assert result["move"]["id"] == now_item.id

    def test_ignores_non_open_findings(self, db_session):
        """A 'done' finding is not eligible for next_move even if critical."""
        wbs.create_item(db_session, "findings", {"title": "resolved", "severity": "critical", "status": "done"})
        result = wbs.next_move(db_session)
        assert result["move"] is None


# ---------------------------------------------------------------------------
# refresh_git
# ---------------------------------------------------------------------------


class TestRefreshGit:
    """Best-effort git/PR refresh; soft-fails cleanly when unconfigured."""

    def test_refresh_git_unset_repo_soft_fails(self, db_session):
        """An empty repo path returns refreshed=False without raising."""
        result = wbs.refresh_git(db_session, "")
        assert result["refreshed"] is False
        assert "reason" in result

    def test_refresh_git_nonexistent_path_soft_fails(self, db_session):
        """A path that isn't a directory returns refreshed=False without raising."""
        result = wbs.refresh_git(db_session, "/no/such/path/at/all")
        assert result["refreshed"] is False

    def test_refresh_git_happy_path(self, db_session, tmp_path):
        """A real tmp git repo with one branch is refreshed into a branches-lane item."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

        result = wbs.refresh_git(db_session, str(repo))
        assert result["refreshed"] is True
        assert result["branches_updated"] >= 1

        branch_items = db_session.query(WorkBoardItem).filter(WorkBoardItem.lane == "branches").all()
        assert any(b.git_branch == "main" for b in branch_items)


# ---------------------------------------------------------------------------
# Attention state machine (§6.2)
# ---------------------------------------------------------------------------


class TestAttentionStateMachine:
    """The backlog attention state machine: the service is the single writer."""

    def test_operator_note_flips_to_needs_attention(self, db_session):
        """A plain operator note always flips attention to needs_attention."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        assert item.attention == "acknowledged"
        wbs.add_note(db_session, item.id, "please look at this", author="operator")
        assert wbs.get_item(db_session, item.id).attention == "needs_attention"

    def test_named_op_system_note_flips_to_needs_attention(self, db_session):
        """An operator-attributed displaced-NOW system note also flips attention."""
        first = wbs.create_item(db_session, "next", {"title": "A"})
        second = wbs.create_item(db_session, "next", {"title": "B"})
        wbs.promote_next(db_session, first.id)
        wbs.promote_next(db_session, second.id, displace="next", author="operator")
        displaced = wbs.get_item(db_session, first.id)
        assert displaced.attention == "needs_attention"

    def test_status_change_note_flips_to_needs_attention(self, db_session):
        """An operator-driven status change appends a system note and flips attention."""
        item = wbs.create_item(db_session, "tangents", {"title": "T"})
        # Acknowledge first so we can observe the flip cleanly.
        item.attention = "acknowledged"
        db_session.commit()
        wbs.update_item(db_session, item.id, {"status": "dropped"}, author="operator")
        updated = wbs.get_item(db_session, item.id)
        assert updated.attention == "needs_attention"
        assert any("status:" in n.text for n in updated.notes)

    def test_agent_note_without_resolution_leaves_state_untouched(self, db_session):
        """An agent commentary note (no resolution) does not change attention."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "operator comment", author="operator")
        assert wbs.get_item(db_session, item.id).attention == "needs_attention"
        wbs.add_note(db_session, item.id, "PROPOSED: do X", author="agent")
        assert wbs.get_item(db_session, item.id).attention == "needs_attention"

    def test_agent_addressed_from_needs_attention(self, db_session):
        """resolution='addressed' from needs_attention flips to addressed."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "please check", author="operator")
        wbs.add_note(db_session, item.id, "done", author="agent", resolution="addressed")
        assert wbs.get_item(db_session, item.id).attention == "addressed"

    def test_agent_followup_requested_from_needs_attention(self, db_session):
        """resolution='followup_requested' with a question mark flips to followup_requested."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "please check", author="operator")
        wbs.add_note(db_session, item.id, "which environment do you mean?", author="agent", resolution="followup_requested")
        assert wbs.get_item(db_session, item.id).attention == "followup_requested"

    def test_resolution_illegal_outside_needs_attention(self, db_session):
        """A resolution note is rejected (409) when the item isn't needs_attention."""
        item = wbs.create_item(db_session, "next", {"title": "A"})  # attention=acknowledged
        with pytest.raises(wbs.WorkBoardConflictError):
            wbs.add_note(db_session, item.id, "done", author="agent", resolution="addressed")

    def test_followup_without_question_mark_rejected(self, db_session):
        """followup_requested without a '?' in the note text is a validation error."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "please check", author="operator")
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.add_note(db_session, item.id, "no question here", author="agent", resolution="followup_requested")

    def test_operator_note_with_resolution_rejected(self, db_session):
        """An operator note may never carry a resolution."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        with pytest.raises(wbs.WorkBoardValidationError):
            wbs.add_note(db_session, item.id, "text", author="operator", resolution="addressed")

    def test_operator_reply_on_followup_requested_reopens(self, db_session):
        """An operator reply while followup_requested flips back to needs_attention."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "please check", author="operator")
        wbs.add_note(db_session, item.id, "which env?", author="agent", resolution="followup_requested")
        assert wbs.get_item(db_session, item.id).attention == "followup_requested"
        wbs.add_note(db_session, item.id, "prod", author="operator")
        assert wbs.get_item(db_session, item.id).attention == "needs_attention"

    def test_acknowledge_only_from_addressed(self, db_session):
        """acknowledge() succeeds from addressed and fails otherwise."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "please check", author="operator")
        wbs.add_note(db_session, item.id, "done", author="agent", resolution="addressed")
        acked = wbs.acknowledge(db_session, item.id)
        assert acked.attention == "acknowledged"
        assert any("Acknowledged" in n.text for n in acked.notes)

    def test_acknowledge_illegal_outside_addressed(self, db_session):
        """acknowledge() on a non-addressed item raises a conflict."""
        item = wbs.create_item(db_session, "next", {"title": "A"})  # acknowledged already
        with pytest.raises(wbs.WorkBoardConflictError):
            wbs.acknowledge(db_session, item.id)

    def test_every_flip_has_same_transaction_note(self, db_session):
        """No attention flip occurs without an explanatory note in the same call (R9)."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "please check", author="operator")
        before_notes = len(wbs.get_item(db_session, item.id).notes)
        wbs.add_note(db_session, item.id, "done", author="agent", resolution="addressed")
        after = wbs.get_item(db_session, item.id)
        assert after.attention == "addressed"
        assert len(after.notes) == before_notes + 1


# ---------------------------------------------------------------------------
# get_backlog
# ---------------------------------------------------------------------------


class TestGetBacklog:
    """Backlog enumeration: ordering, exclusion, and no-op idempotence."""

    def test_excludes_non_needs_attention_items(self, db_session):
        """Items that are acknowledged/addressed/followup_requested are excluded."""
        wbs.create_item(db_session, "next", {"title": "acknowledged-item"})
        backlog = wbs.get_backlog(db_session)
        assert backlog == []

    def test_orders_now_before_next_before_findings_before_tangents(self, db_session):
        """Backlog order is now -> next -> findings -> tangents (branches/prs trail)."""
        tangent = wbs.add_tangent(db_session, "T")
        wbs.add_note(db_session, tangent.id, "comment", author="operator")

        finding = wbs.create_item(db_session, "findings", {"title": "F", "severity": "critical", "status": "open"})
        wbs.add_note(db_session, finding.id, "comment", author="operator")

        next_item = wbs.create_item(db_session, "next", {"title": "N"})
        wbs.add_note(db_session, next_item.id, "comment", author="operator")

        now_item_source = wbs.create_item(db_session, "next", {"title": "will-be-now"})
        now_item = wbs.promote_next(db_session, now_item_source.id, displace=None)
        wbs.add_note(db_session, now_item.id, "comment", author="operator")

        backlog = wbs.get_backlog(db_session)
        lanes_in_order = [item["lane"] for item in backlog]
        assert lanes_in_order == ["now", "next", "findings", "tangents"]

    def test_second_pass_is_noop_when_no_new_input(self, db_session):
        """A second get_backlog call after full resolution returns empty (R4/R10)."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "please check", author="operator")
        assert len(wbs.get_backlog(db_session)) == 1

        wbs.add_note(db_session, item.id, "done", author="agent", resolution="addressed")
        assert wbs.get_backlog(db_session) == []

        wbs.acknowledge(db_session, item.id)
        assert wbs.get_backlog(db_session) == []

    def test_backlog_includes_full_note_thread(self, db_session):
        """Each backlog item carries its complete notes list."""
        item = wbs.create_item(db_session, "next", {"title": "A"})
        wbs.add_note(db_session, item.id, "first", author="operator")
        wbs.add_note(db_session, item.id, "PROPOSED: x", author="agent")
        backlog = wbs.get_backlog(db_session)
        assert len(backlog) == 1
        assert len(backlog[0]["notes"]) == 2


# ---------------------------------------------------------------------------
# launch_impl / run_status (design doc §4) -- subprocess/shutil.which ALWAYS
# mocked here. No real `claude` process may be spawned by this test module.
# ---------------------------------------------------------------------------


def _make_impl_item(db_session, tmp_path, title="Do the thing"):
    """Create a change_kind='impl' item with a non-empty note, pointed at a real tmp dir repo."""
    # First-Party
    from mcpgateway.db import Server

    item = wbs.create_item(db_session, "next", {"title": title})
    wbs.add_note(db_session, item.id, "please implement this", author="operator")
    wbs.set_change_state(db_session, item.id, change_kind="impl", author="agent")

    repo = tmp_path / "repo"
    repo.mkdir()

    server = Server(id="server-uuid-1234", name="work-board", description="test")
    db_session.add(server)
    db_session.commit()

    return wbs.get_item(db_session, item.id), str(repo)


class TestLaunchImpl:
    """launch_impl: 4 safety guards + happy-path spawn parsing. subprocess/shutil.which mocked throughout."""

    def test_guard1_not_impl_or_empty_notes_fails_no_spawn(self, db_session, monkeypatch):
        """A non-'impl' item (or one with no notes) fails guard 1: run_state='failed', no subprocess call."""
        item = wbs.create_item(db_session, "next", {"title": "No notes, not impl"})

        called = {"subprocess": False}
        monkeypatch.setattr(wbs.subprocess, "run", lambda *a, **k: called.__setitem__("subprocess", True))
        monkeypatch.setattr(wbs.shutil, "which", lambda name: "/usr/bin/claude")

        wbs.launch_impl(db_session, item.id)
        refreshed = wbs.get_item(db_session, item.id)
        assert refreshed.run_state == "failed"
        assert called["subprocess"] is False

    def test_guard2_git_repo_missing_fails_no_spawn(self, db_session, monkeypatch):
        """settings.work_board_git_repo unset/not-a-dir fails guard 2: run_state='failed', no subprocess call."""
        item = wbs.create_item(db_session, "next", {"title": "Impl item"})
        wbs.add_note(db_session, item.id, "please implement", author="operator")
        wbs.set_change_state(db_session, item.id, change_kind="impl", author="agent")

        # First-Party
        from mcpgateway.config import settings

        monkeypatch.setattr(settings, "work_board_git_repo", "/no/such/path/at/all")

        called = {"subprocess": False}
        monkeypatch.setattr(wbs.subprocess, "run", lambda *a, **k: called.__setitem__("subprocess", True))
        monkeypatch.setattr(wbs.shutil, "which", lambda name: "/usr/bin/claude")

        wbs.launch_impl(db_session, item.id)
        refreshed = wbs.get_item(db_session, item.id)
        assert refreshed.run_state == "failed"
        assert called["subprocess"] is False

    def test_guard3_already_running_raises_conflict_no_spawn(self, db_session, monkeypatch):
        """run_state already 'running' raises a conflict (idempotency guard); no subprocess call."""
        item = wbs.create_item(db_session, "next", {"title": "Impl item"})
        wbs.add_note(db_session, item.id, "please implement", author="operator")
        wbs.set_change_state(db_session, item.id, change_kind="impl", run_state="running", author="agent")

        called = {"subprocess": False}
        monkeypatch.setattr(wbs.subprocess, "run", lambda *a, **k: called.__setitem__("subprocess", True))
        monkeypatch.setattr(wbs.shutil, "which", lambda name: "/usr/bin/claude")

        with pytest.raises(wbs.WorkBoardConflictError):
            wbs.launch_impl(db_session, item.id)
        assert called["subprocess"] is False

    def test_guard4_claude_missing_fails_no_spawn(self, db_session, monkeypatch, tmp_path):
        """shutil.which('claude') returning None fails guard 4: run_state='failed', no subprocess call."""
        # First-Party
        from mcpgateway.config import settings

        item = wbs.create_item(db_session, "next", {"title": "Impl item"})
        wbs.add_note(db_session, item.id, "please implement", author="operator")
        wbs.set_change_state(db_session, item.id, change_kind="impl", author="agent")

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setattr(settings, "work_board_git_repo", str(repo))

        called = {"subprocess": False}
        monkeypatch.setattr(wbs.subprocess, "run", lambda *a, **k: called.__setitem__("subprocess", True))
        monkeypatch.setattr(wbs.shutil, "which", lambda name: None)

        wbs.launch_impl(db_session, item.id)
        refreshed = wbs.get_item(db_session, item.id)
        assert refreshed.run_state == "failed"
        assert called["subprocess"] is False

    def test_happy_path_parses_bg_json_and_sets_running(self, db_session, monkeypatch, tmp_path):
        """All 4 guards pass + work-board server registered: parses a fake --bg json, sets
        run_state='running', appends agent-id note. No real subprocess is spawned (subprocess.run
        is mocked)."""
        # First-Party
        from mcpgateway.config import settings

        item, repo_path = _make_impl_item(db_session, tmp_path)
        monkeypatch.setattr(settings, "work_board_git_repo", repo_path)

        captured_argv = {}

        class _FakeCompletedProcess:
            stdout = '{"agent_id": "bg-agent-42"}'
            stderr = ""
            returncode = 0

        def _fake_run(argv, **kwargs):
            captured_argv["argv"] = argv
            captured_argv["cwd"] = kwargs.get("cwd")
            return _FakeCompletedProcess()

        monkeypatch.setattr(wbs.subprocess, "run", _fake_run)
        monkeypatch.setattr(wbs.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)

        result = wbs.launch_impl(db_session, item.id)

        assert result["agent_id"] == "bg-agent-42"
        refreshed = wbs.get_item(db_session, item.id)
        assert refreshed.run_state == "running"
        assert any("launched impl subagent bg-agent-42" in (n.text or "") for n in refreshed.notes)

        # Confirm the exact argv contract (design doc §4a) and that cwd is the configured repo only.
        argv = captured_argv["argv"]
        assert argv[0] == "/usr/bin/claude"
        assert "--bg" in argv
        assert "-p" in argv
        assert "--mcp-config" in argv
        assert "--allowedTools" in argv
        assert "mcp__work-board__* Edit Bash(git *)" in argv
        assert "--permission-mode" in argv
        assert "acceptEdits" in argv
        assert "--output-format" in argv
        assert "json" in argv
        assert captured_argv["cwd"] == repo_path


class TestRunStatus:
    """run_status: reconcile via mocked 'claude agents --json'; never fabricates a terminal state."""

    def test_not_running_is_noop(self, db_session):
        """An item that isn't currently 'running' reports reconciled=False without shelling out."""
        item = wbs.create_item(db_session, "next", {"title": "Idle item"})
        result = wbs.run_status(db_session, item.id)
        assert result["reconciled"] is False

    def test_claude_missing_leaves_state_unchanged(self, db_session, monkeypatch):
        """shutil.which('claude') returning None leaves run_state unchanged and reports why."""
        item = wbs.create_item(db_session, "next", {"title": "Impl item"})
        wbs.set_change_state(db_session, item.id, change_kind="impl", run_state="running", author="agent")
        wbs.add_note(db_session, item.id, "launched impl subagent bg-agent-42 2026-07-05", author="agent")

        monkeypatch.setattr(wbs.shutil, "which", lambda name: None)
        result = wbs.run_status(db_session, item.id)
        assert result["reconciled"] is False
        assert result["run_state"] == "running"

    def test_happy_path_flips_running_to_applied(self, db_session, monkeypatch):
        """A matching agent id with a terminal 'completed' status flips running -> applied."""
        item = wbs.create_item(db_session, "next", {"title": "Impl item"})
        wbs.set_change_state(db_session, item.id, change_kind="impl", run_state="running", author="agent")
        wbs.add_note(db_session, item.id, "launched impl subagent bg-agent-42 2026-07-05", author="agent")

        class _FakeCompletedProcess:
            stdout = '[{"id": "bg-agent-42", "status": "completed"}]'
            stderr = ""
            returncode = 0

        monkeypatch.setattr(wbs.shutil, "which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr(wbs.subprocess, "run", lambda argv, **kwargs: _FakeCompletedProcess())

        result = wbs.run_status(db_session, item.id)
        assert result["reconciled"] is True
        assert result["run_state"] == "applied"
        refreshed = wbs.get_item(db_session, item.id)
        assert refreshed.run_state == "applied"
