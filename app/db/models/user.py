"""User - Polier, Bauleiter, Geschäftsführer, Admin."""
from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.company import Company
    from app.db.models.message import Message
    from app.db.models.project import Project


class UserRole(str, enum.Enum):
    polier = "polier"
    bauleiter = "bauleiter"
    gf = "gf"
    admin = "admin"


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.polier,
    )
    # Stored in E.164 format including the "whatsapp:" prefix Twilio uses.
    # Optional - users may exist with only a telegram_chat_id once we have multi-provider support.
    whatsapp_number: Mapped[str | None] = mapped_column(
        String(32), nullable=True, unique=True, index=True
    )
    # Telegram numeric chat id (BIGINT - can exceed 32-bit on supergroups/channels).
    telegram_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True, index=True
    )
    language_pref: Mapped[str] = mapped_column(String(8), nullable=False, default="de")
    email: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # MVP: each user has at most one currently-active project. Phase 4+ will
    # let Claude infer the project from message content for multi-project Poliers.
    current_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    company: Mapped[Company] = relationship(back_populates="users")
    current_project: Mapped[Project | None] = relationship(foreign_keys=[current_project_id])
    messages: Mapped[list[Message]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
