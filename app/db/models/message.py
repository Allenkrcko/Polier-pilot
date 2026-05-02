"""Message - raw inbound WhatsApp message persisted before processing."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.photo import Photo
    from app.db.models.report_session import ReportSession
    from app.db.models.user import User


class MessageType(str, enum.Enum):
    voice = "voice"
    photo = "photo"
    text = "text"
    video = "video"
    document = "document"
    other = "other"


class Message(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "messages"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    twilio_sid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    type: Mapped[MessageType] = mapped_column(
        SAEnum(MessageType, name="message_type"),
        nullable=False,
        default=MessageType.text,
    )

    # Twilio fields captured from the webhook before any processing
    from_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    to_number: Mapped[str] = mapped_column(String(32), nullable=False)
    media_url: Mapped[str | None] = mapped_column(String(1024))
    media_content_type: Mapped[str | None] = mapped_column(String(128))

    raw_text: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    detected_language: Mapped[str | None] = mapped_column(String(8))

    classified_intent: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSONB)

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_error: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User | None] = relationship(back_populates="messages")
    session: Mapped[ReportSession | None] = relationship(back_populates="messages")
    photos: Mapped[list[Photo]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )
