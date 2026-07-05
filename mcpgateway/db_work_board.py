# -*- coding: utf-8 -*-
"""Location: ./mcpgateway/db_work_board.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

Work Board ORM Models.

Defines the two tables backing the personal work-tracking board vertical
slice: ``work_board_items`` (six-lane board: now/next/branches/prs/tangents/
findings, plus the backlog ``attention`` state machine) and
``work_board_notes`` (append-only note thread per item).

This module is intentionally separate from the ~2k-line ``mcpgateway/db.py``
so the churn-risk upstream file only needs a single import line to register
these models on ``Base.metadata`` (see the bottom of ``db.py``).
"""

# Standard
from datetime import datetime
from typing import List, Optional

# Third-Party
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

# First-Party
from mcpgateway.db import Base, utc_now


class WorkBoardItem(Base):
    """ORM model for a single work-board item.

    One row represents an item in exactly one of six lanes (``now``,
    ``next``, ``branches``, ``prs``, ``tangents``, ``findings``). Lane-specific
    fields are sparse nullable columns rather than per-lane tables, matching
    the shared CRUD/notes shape every lane needs and keeping lane transitions
    (e.g. ``next`` -> ``now``) a single-row column update instead of a
    cross-table move.

    The ``attention`` column drives the agent-backlog state machine (see
    ``mcpgateway.services.work_board_service``); it is never client-settable
    directly and only ever changes inside a service method that writes the
    explaining note in the same transaction.
    """

    __tablename__ = "work_board_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    lane: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)

    # next-lane only: dense priority 1..5, renumbered by the service on every write.
    priority: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # now/next free-text working-branch context (distinct from the git_branch key on branches-lane rows).
    branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # now lane: ISO date the item was promoted to NOW.
    started: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # tangents lane: ISO date captured.
    captured: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # tangents: parked|promoted|dropped. findings: open|fixing|done|wontfix.
    status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # branches: land|rebase|abandon|unknown. prs: review|land|close|unknown.
    verdict: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # findings: advisory|warning|critical.
    severity: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # findings: free-text provenance, e.g. "repo-survey".
    source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # branches lane key.
    git_branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    git_ahead: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    git_behind: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    git_last_commit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # prs lane key.
    pr_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Pass-through from `gh pr list` (OPEN/CLOSED/MERGED) -- not a closed enum.
    pr_state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # Backlog attention state machine (see work_board_service._ATTENTION_TRANSITIONS).
    attention: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'acknowledged'"))

    # Pending-changes discriminator: null (ordinary item) | 'doc' | 'impl'. Set only by
    # work_board_service.classify_change(); orthogonal to `attention` (see module docstring
    # and pending-changes-launch-design.md §1) so it gets its own column, not an overload.
    change_kind: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # Repo-relative path targeted when change_kind='doc'. Re-validated against
    # settings.work_board_git_repo at write time in classify_change/apply_doc_change.
    target_doc: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Lifecycle of the doc-write or spawned subagent: null -> queued -> running ->
    # applied|failed. Orthogonal to `attention`; set only by the dedicated setter.
    run_state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=utc_now, onupdate=utc_now, nullable=False)

    notes: Mapped[List["WorkBoardNote"]] = relationship(
        "WorkBoardNote",
        back_populates="item",
        order_by="WorkBoardNote.at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "lane IN ('now','next','branches','prs','tangents','findings')",
            name="ck_work_board_lane",
        ),
        CheckConstraint(
            "attention IN ('needs_attention','addressed','followup_requested','acknowledged')",
            name="ck_work_board_attention",
        ),
        CheckConstraint(
            "change_kind IS NULL OR change_kind IN ('doc','impl')",
            name="ck_work_board_change_kind",
        ),
        CheckConstraint(
            "run_state IS NULL OR run_state IN ('queued','running','applied','failed')",
            name="ck_work_board_run_state",
        ),
        # Single-NOW invariant, DB-level backstop (the service also enforces this as a clean 409).
        Index(
            "uq_work_board_single_now",
            "lane",
            unique=True,
            sqlite_where=text("lane = 'now'"),
            postgresql_where=text("lane = 'now'"),
        ),
    )

    def __repr__(self) -> str:
        """String representation.

        Returns:
            str: String representation of the WorkBoardItem instance.
        """
        return f"<WorkBoardItem(id='{self.id}', lane='{self.lane}', title='{self.title[:40]!r}')>"


class WorkBoardNote(Base):
    """ORM model for a single append-only note on a work-board item.

    Notes have no update/delete endpoint anywhere in the service or router,
    so they are append-only by construction. ``author`` distinguishes
    operator commentary from agent responses for the backlog state machine.
    """

    __tablename__ = "work_board_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(64), ForeignKey("work_board_items.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), default=utc_now, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(16), nullable=False)

    item: Mapped["WorkBoardItem"] = relationship("WorkBoardItem", back_populates="notes")

    __table_args__ = (CheckConstraint("author IN ('operator','agent')", name="ck_work_board_note_author"),)

    def __repr__(self) -> str:
        """String representation.

        Returns:
            str: String representation of the WorkBoardNote instance.
        """
        return f"<WorkBoardNote(id={self.id}, item_id='{self.item_id}', author='{self.author}')>"
