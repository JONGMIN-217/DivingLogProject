"""add operational indexes

Revision ID: 6e0f9db9b2c1
Revises: d80d19508a75
Create Date: 2026-06-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "6e0f9db9b2c1"
down_revision: Union[str, Sequence[str], None] = "d80d19508a75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_dive_logs_user_date_time",
        "dive_logs",
        ["user_id", "dive_date", "entry_time", "dive_time", "exit_time", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_dive_logs_point_date",
        "dive_logs",
        ["dive_point_id", "dive_date"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_dive_logs_import_identity",
        "dive_logs",
        ["import_source", "import_source_file_hash", "import_external_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_import_runs_user_created",
        "import_runs",
        ["user_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_import_runs_user_created", table_name="import_runs", if_exists=True)
    op.drop_index("ix_dive_logs_import_identity", table_name="dive_logs", if_exists=True)
    op.drop_index("ix_dive_logs_point_date", table_name="dive_logs", if_exists=True)
    op.drop_index("ix_dive_logs_user_date_time", table_name="dive_logs", if_exists=True)
