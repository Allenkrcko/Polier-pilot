"""Project - one Baustelle (construction site)."""
from __future__ import annotations

import enum
import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.company import Company
    from app.db.models.report_session import ReportSession


class ProjectStatus(str, enum.Enum):
    planned = "planned"
    active = "active"
    paused = "paused"
    completed = "completed"
    archived = "archived"


class Project(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "projects"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    auftraggeber: Mapped[str | None] = mapped_column(String(255))
    project_number: Mapped[str | None] = mapped_column(String(64), index=True)
    address: Mapped[str | None] = mapped_column(String(512))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    vob_clauses: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, name="project_status"),
        nullable=False,
        default=ProjectStatus.active,
    )

    company: Mapped[Company] = relationship(back_populates="projects")
    sessions: Mapped[list[ReportSession]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
