# -*- coding: utf-8 -*-
"""Location: ./mcpgateway/alembic/versions/5e72814c91e5_add_work_board_pending_change_columns.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

5e72814c91e5_add_work_board_pending_change_columns

Revision ID: 5e72814c91e5
Revises: ed62c5bfe136
Create Date: 2026-07-05 00:00:00.000000

Adds three nullable columns to ``work_board_items`` for the pending-changes
view / deterministic doc-update / launch-control feature set (Phase 1 of
``todo/work-board/pending-changes-launch-design.md``):

- ``change_kind`` -- discriminator, null (ordinary item) | 'doc' | 'impl'.
- ``target_doc`` -- repo-relative path targeted when ``change_kind='doc'``.
- ``run_state`` -- lifecycle: null | 'queued' | 'running' | 'applied' | 'failed'.

All three are nullable and orthogonal to the existing ``attention`` state
machine (see ``db_work_board.py`` module docstring / design doc §1) --
overloading ``attention`` would break its single-writer invariant, so these
are separate columns rather than new enum values on an existing one.
"""

# Standard
from typing import Sequence, Union

# Third-Party
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "5e72814c91e5"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "ed62c5bfe136"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add change_kind, target_doc, run_state columns to work_board_items (ALTER-add only)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("work_board_items")}

    if "change_kind" not in existing_columns:
        op.add_column("work_board_items", sa.Column("change_kind", sa.String(16), nullable=True))
    if "target_doc" not in existing_columns:
        op.add_column("work_board_items", sa.Column("target_doc", sa.String(512), nullable=True))
    if "run_state" not in existing_columns:
        op.add_column("work_board_items", sa.Column("run_state", sa.String(16), nullable=True))

    # CHECK constraints for change_kind/run_state are intentionally NOT created here.
    # SQLite does not support ALTER-add of CHECK constraints (op.create_check_constraint
    # raises NotImplementedError, requiring batch/table-recreate, which would risk the
    # notes FK). Enum integrity is enforced at the service layer -- set_change_state is the
    # single writer and validates both enums. Keeping this ALTER-add only lets the gateway's
    # auto-migration (bootstrap_db) run it natively on SQLite at startup.
    # See db_work_board.py __table_args__ note.


def downgrade() -> None:
    """Drop the CHECK constraints and the three pending-change columns."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_constraints = {ck["name"] for ck in inspector.get_check_constraints("work_board_items")}

    if "ck_work_board_run_state" in existing_constraints:
        try:
            op.drop_constraint("ck_work_board_run_state", "work_board_items", type_="check")
        except Exception as e:  # pylint: disable=broad-except
            print(f"Warning: Could not drop constraint ck_work_board_run_state: {e}")
    if "ck_work_board_change_kind" in existing_constraints:
        try:
            op.drop_constraint("ck_work_board_change_kind", "work_board_items", type_="check")
        except Exception as e:  # pylint: disable=broad-except
            print(f"Warning: Could not drop constraint ck_work_board_change_kind: {e}")

    existing_columns = {col["name"] for col in inspector.get_columns("work_board_items")}

    for column_name in ("run_state", "target_doc", "change_kind"):
        if column_name in existing_columns:
            try:
                op.drop_column("work_board_items", column_name)
            except Exception as e:  # pylint: disable=broad-except
                print(f"Warning: Could not drop column {column_name}: {e}")
