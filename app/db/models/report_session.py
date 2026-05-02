"""ReportSession - one work day per Polier per project."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.message import Message
    from app.db.models.project import Project
    from app.db.models.report import Report
    from app.db.models.user import User


class SessionStatus(str, enum.Enum):
    open = "open"
    pending_review = "pending_review"
    finalized = "finalized"
    cancelled = "cancelled"


class ReportSession(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "report_sessions"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", "work_date", name="uq_session_project_user_date"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status"),
        nullable=False,
        default=SessionStatus.open,
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="sessions")
    user: Mapped[User] = relationship()
    messages: Mapped[list[Message]] = relationship(
        back_populates="session",
        order_by="Message.received_at",
    )
    reports: Mapped[list[Report]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
