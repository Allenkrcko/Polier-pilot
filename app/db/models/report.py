"""Report - the generated artifact (Bautagebuch / Aufmaß / Mängel / Tagesbericht)."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.report_session import ReportSession


class ReportType(str, enum.Enum):
    bautagebuch = "bautagebuch"
    aufmass = "aufmass"
    maengel = "maengel"
    tagesbericht = "tagesbericht"


class Report(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "reports"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[ReportType] = mapped_column(
        SAEnum(ReportType, name="report_type"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    content_json: Mapped[dict | None] = mapped_column(JSONB)
    content_md: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(1024))

    sent_to: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[ReportSession] = relationship(back_populates="reports")
