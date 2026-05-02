"""Helpers around the ReportSession aggregate.

A session is the unit of work-day-per-(user, project). The DB has a
UNIQUE(project_id, user_id, work_date) so we use ON CONFLICT to make
get_or_create atomic - safe under concurrent worker fan-out.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ReportSession, SessionStatus, User

logger = logging.getLogger(__name__)


def local_today(now: datetime | None = None) -> date:
    """Today's date in the configured TIMEZONE (default Europe/Berlin)."""
    tz = ZoneInfo(settings.timezone)
    moment = (now or datetime.now(timezone.utc)).astimezone(tz)
    return moment.date()


async def get_or_create_session(
    db: AsyncSession,
    *,
    user: User,
    work_date: date | None = None,
) -> ReportSession | None:
    """Return today's session for (user.current_project, user) or None.

    Returns None when the user has no current_project_id - the caller
    decides whether to log a warning or skip session-binding entirely.
    """
    if user.current_project_id is None:
        logger.warning("User id=%s has no current_project_id; skipping session bind", user.id)
        return None

    work_date = work_date or local_today()

    # ON CONFLICT DO NOTHING + SELECT pattern keeps the helper atomic
    # under concurrent worker invocations.
    new_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(ReportSession)
        .values(
            id=new_id,
            project_id=user.current_project_id,
            user_id=user.id,
            work_date=work_date,
            status=SessionStatus.open,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_session_project_user_date")
    )
    await db.execute(stmt)
    session = await db.scalar(
        select(ReportSession).where(
            ReportSession.project_id == user.current_project_id,
            ReportSession.user_id == user.id,
            ReportSession.work_date == work_date,
        )
    )
    return session


async def mark_session_pending_review(
    db: AsyncSession,
    session: ReportSession,
) -> None:
    """Flip an open session to pending_review (Phase 4 picks these up)."""
    if session.status == SessionStatus.open:
        session.status = SessionStatus.pending_review
        session.finalized_at = None  # not finalized yet, just queued for generation
        logger.info("Session id=%s marked pending_review", session.id)
