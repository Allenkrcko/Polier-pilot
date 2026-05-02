"""initial schema

Revision ID: 20260502_0000
Revises:
Create Date: 2026-05-02 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260502_0000"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Native PG enums declared once and reused across columns / drops.
# We pre-create them in upgrade() and pass create_type=False on the
# postgresql.ENUM column types so Alembic does not retry CREATE TYPE
# inside each create_table call.
project_status = postgresql.ENUM(
    "planned", "active", "paused", "completed", "archived",
    name="project_status",
)
user_role = postgresql.ENUM("polier", "bauleiter", "gf", "admin", name="user_role")
message_type = postgresql.ENUM(
    "voice", "photo", "text", "video", "document", "other",
    name="message_type",
)
session_status = postgresql.ENUM(
    "open", "pending_review", "finalized", "cancelled",
    name="session_status",
)
report_type = postgresql.ENUM(
    "bautagebuch", "aufmass", "maengel", "tagesbericht",
    name="report_type",
)


def _project_status_col() -> postgresql.ENUM:
    return postgresql.ENUM(
        "planned", "active", "paused", "completed", "archived",
        name="project_status",
        create_type=False,
    )


def _user_role_col() -> postgresql.ENUM:
    return postgresql.ENUM(
        "polier", "bauleiter", "gf", "admin",
        name="user_role",
        create_type=False,
    )


def _message_type_col() -> postgresql.ENUM:
    return postgresql.ENUM(
        "voice", "photo", "text", "video", "document", "other",
        name="message_type",
        create_type=False,
    )


def _session_status_col() -> postgresql.ENUM:
    return postgresql.ENUM(
        "open", "pending_review", "finalized", "cancelled",
        name="session_status",
        create_type=False,
    )


def _report_type_col() -> postgresql.ENUM:
    return postgresql.ENUM(
        "bautagebuch", "aufmass", "maengel", "tagesbericht",
        name="report_type",
        create_type=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (project_status, user_role, message_type, session_status, report_type):
        enum.create(bind, checkfirst=True)

    # ---- companies ----
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address", sa.String(length=512)),
        sa.Column("ust_id", sa.String(length=64), unique=True),
        sa.Column("vob_template_id", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_companies_name", "companies", ["name"])

    # ---- projects ----
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("auftraggeber", sa.String(length=255)),
        sa.Column("project_number", sa.String(length=64)),
        sa.Column("address", sa.String(length=512)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("vob_clauses", postgresql.JSONB()),
        sa.Column("status", _project_status_col(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_company_id", "projects", ["company_id"])
    op.create_index("ix_projects_name", "projects", ["name"])
    op.create_index("ix_projects_project_number", "projects", ["project_number"])

    # ---- users ----
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", _user_role_col(), nullable=False),
        sa.Column("whatsapp_number", sa.String(length=32), nullable=False, unique=True),
        sa.Column("language_pref", sa.String(length=8), nullable=False),
        sa.Column("email", sa.String(length=255)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_company_id", "users", ["company_id"])
    op.create_index("ix_users_whatsapp_number", "users", ["whatsapp_number"])

    # ---- report_sessions ----
    op.create_table(
        "report_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("status", _session_status_col(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "user_id", "work_date",
            name="uq_session_project_user_date",
        ),
    )
    op.create_index("ix_report_sessions_project_id", "report_sessions", ["project_id"])
    op.create_index("ix_report_sessions_user_id", "report_sessions", ["user_id"])
    op.create_index("ix_report_sessions_work_date", "report_sessions", ["work_date"])

    # ---- messages ----
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column("twilio_sid", sa.String(length=64), nullable=False, unique=True),
        sa.Column("type", _message_type_col(), nullable=False),
        sa.Column("from_number", sa.String(length=32), nullable=False),
        sa.Column("to_number", sa.String(length=32), nullable=False),
        sa.Column("media_url", sa.String(length=1024)),
        sa.Column("media_content_type", sa.String(length=128)),
        sa.Column("raw_text", sa.Text()),
        sa.Column("transcript", sa.Text()),
        sa.Column("detected_language", sa.String(length=8)),
        sa.Column("classified_intent", sa.String(length=64)),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("processing_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_messages_twilio_sid", "messages", ["twilio_sid"])
    op.create_index("ix_messages_from_number", "messages", ["from_number"])

    # ---- photos ----
    op.create_table(
        "photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("storage_backend", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=128)),
        sa.Column("file_size_bytes", sa.Integer()),
        sa.Column("gps_lat", sa.Float()),
        sa.Column("gps_lon", sa.Float()),
        sa.Column("exif_timestamp", sa.DateTime(timezone=True)),
        sa.Column("ocr_text", sa.Text()),
        sa.Column("ai_caption", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_photos_message_id", "photos", ["message_id"])

    # ---- reports ----
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", _report_type_col(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_json", postgresql.JSONB()),
        sa.Column("content_md", sa.Text()),
        sa.Column("pdf_path", sa.String(length=1024)),
        sa.Column("sent_to", postgresql.ARRAY(sa.String())),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reports_session_id", "reports", ["session_id"])
    op.create_index("ix_reports_type", "reports", ["type"])


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_table("photos")
    op.drop_table("messages")
    op.drop_table("report_sessions")
    op.drop_table("users")
    op.drop_table("projects")
    op.drop_table("companies")

    bind = op.get_bind()
    for enum in (report_type, session_status, message_type, user_role, project_status):
        enum.drop(bind, checkfirst=True)
