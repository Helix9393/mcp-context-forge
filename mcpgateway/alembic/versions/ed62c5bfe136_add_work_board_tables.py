# -*- coding: utf-8 -*-
"""Location: ./mcpgateway/alembic/versions/ed62c5bfe136_add_work_board_tables.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

ed62c5bfe136_add_work_board_tables

Revision ID: ed62c5bfe136
Revises: e198602c3c1e
Create Date: 2026-07-04 23:34:29.782126

Creates ``work_board_items`` and ``work_board_notes`` -- the personal
work-tracking board vertical slice (six lanes: now/next/branches/prs/
tangents/findings, plus the backlog attention-state-machine columns). This
is a single initial revision folding in the full §2.1/§2.2 schema (including
the backlog ``attention``/``author`` columns); nothing has shipped yet, so
there is no legacy state to migrate from.
"""

# Standard
from typing import Sequence, Union

# Third-Party
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ed62c5bfe136"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "e198602c3c1e"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create work_board_items and work_board_notes tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "work_board_items" not in existing_tables:
        op.create_table(
            "work_board_items",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("lane", sa.String(16), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("priority", sa.Integer(), nullable=True),
            sa.Column("branch", sa.String(255), nullable=True),
            sa.Column("started", sa.String(10), nullable=True),
            sa.Column("captured", sa.String(10), nullable=True),
            sa.Column("status", sa.String(16), nullable=True),
            sa.Column("verdict", sa.String(16), nullable=True),
            sa.Column("severity", sa.String(16), nullable=True),
            sa.Column("source", sa.String(64), nullable=True),
            sa.Column("git_branch", sa.String(255), nullable=True),
            sa.Column("git_ahead", sa.Integer(), nullable=True),
            sa.Column("git_behind", sa.Integer(), nullable=True),
            sa.Column("git_last_commit", sa.String(64), nullable=True),
            sa.Column("pr_number", sa.Integer(), nullable=True),
            sa.Column("pr_state", sa.String(16), nullable=True),
            sa.Column("attention", sa.String(20), nullable=False, server_default=sa.text("'acknowledged'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.CheckConstraint("lane IN ('now','next','branches','prs','tangents','findings')", name="ck_work_board_lane"),
            sa.CheckConstraint("attention IN ('needs_attention','addressed','followup_requested','acknowledged')", name="ck_work_board_attention"),
        )
        op.create_index(
            "uq_work_board_single_now",
            "work_board_items",
            ["lane"],
            unique=True,
            sqlite_where=sa.text("lane = 'now'"),
            postgresql_where=sa.text("lane = 'now'"),
        )

    if "work_board_notes" not in existing_tables:
        op.create_table(
            "work_board_notes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("item_id", sa.String(64), sa.ForeignKey("work_board_items.id", ondelete="CASCADE"), nullable=False),
            sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("author", sa.String(16), nullable=False),
            sa.CheckConstraint("author IN ('operator','agent')", name="ck_work_board_note_author"),
        )


def downgrade() -> None:
    """Reverse the work-board tables (child table first)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "work_board_notes" in existing_tables:
        try:
            op.drop_table("work_board_notes")
        except Exception as e:  # pylint: disable=broad-except
            print(f"Warning: Could not drop table work_board_notes: {e}")

    if "work_board_items" in existing_tables:
        try:
            op.drop_index("uq_work_board_single_now", table_name="work_board_items")
        except Exception as e:  # pylint: disable=broad-except
            print(f"Warning: Could not drop index uq_work_board_single_now: {e}")
        try:
            op.drop_table("work_board_items")
        except Exception as e:  # pylint: disable=broad-except
            print(f"Warning: Could not drop table work_board_items: {e}")
