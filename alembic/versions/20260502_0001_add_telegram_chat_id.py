"""add telegram_chat_id to users; relax whatsapp_number to nullable

Revision ID: 20260502_0001
Revises: 20260502_0000
Create Date: 2026-05-02 21:30:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260502_0001"
down_revision: str | None = "20260502_0000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Telegram chat id is a BIGINT (Telegram's IDs can exceed 32-bit).
    op.add_column(
        "users",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
    )
    op.create_unique_constraint("uq_users_telegram_chat_id", "users", ["telegram_chat_id"])
    op.create_index("ix_users_telegram_chat_id", "users", ["telegram_chat_id"])

    # whatsapp_number was NOT NULL; relax it so a Telegram-only user is valid.
    op.alter_column("users", "whatsapp_number", existing_type=sa.String(32), nullable=True)


def downgrade() -> None:
    op.alter_column("users", "whatsapp_number", existing_type=sa.String(32), nullable=False)
    op.drop_index("ix_users_telegram_chat_id", table_name="users")
    op.drop_constraint("uq_users_telegram_chat_id", "users", type_="unique")
    op.drop_column("users", "telegram_chat_id")
