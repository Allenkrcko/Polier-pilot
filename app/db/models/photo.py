"""Photo - image attached to a Message, with EXIF and AI metadata."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.message import Message


class Photo(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "photos"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    content_type: Mapped[str | None] = mapped_column(String(128))
    file_size_bytes: Mapped[int | None] = mapped_column()

    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lon: Mapped[float | None] = mapped_column(Float)
    exif_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ocr_text: Mapped[str | None] = mapped_column(Text)
    ai_caption: Mapped[str | None] = mapped_column(Text)

    message: Mapped[Message] = relationship(back_populates="photos")
