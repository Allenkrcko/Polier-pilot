"""Company - the Auftragnehmer (general contractor) using Polier-Pilot."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.project import Project
    from app.db.models.user import User


class Company(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    address: Mapped[str | None] = mapped_column(String(512))
    ust_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    vob_template_id: Mapped[str | None] = mapped_column(String(64))

    projects: Mapped[list[Project]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
    )
    users: Mapped[list[User]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
    )
