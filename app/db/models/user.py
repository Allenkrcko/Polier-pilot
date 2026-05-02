"""User - Polier, Bauleiter, Geschäftsführer, Admin."""
from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.company import Company
    from app.db.models.message import Message


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
    whatsapp_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    language_pref: Mapped[str] = mapped_column(String(8), nullable=False, default="de")
    email: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    company: Mapped[Company] = relationship(back_populates="users")
    messages: Mapped[list[Message]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
