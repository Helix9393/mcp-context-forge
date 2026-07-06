# -*- coding: utf-8 -*-
"""Location: ./mcpgateway/alembic/versions/e3d48e0f3f0f_add_work_board_meta_table.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

e3d48e0f3f0f_add_work_board_meta_table

Revision ID: e3d48e0f3f0f
Revises: 5e72814c91e5
Create Date: 2026-07-06 00:00:00.000000

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
