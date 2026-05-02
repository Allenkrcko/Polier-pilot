"""add users.current_project_id

Revision ID: 20260502_0002
Revises: 20260502_0001
Create Date: 2026-05-02 22:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260502_0002"
down_revision: str | None = "20260502_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "current_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_users_current_project_id",
        "users",
        ["current_project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_users_current_project_id", table_name="users")
    op.drop_column("users", "current_project_id")
