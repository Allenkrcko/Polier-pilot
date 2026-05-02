"""ORM models. Importing this module registers every model with the Base metadata."""
from app.db.models.company import Company
from app.db.models.message import Message, MessageType
from app.db.models.photo import Photo
from app.db.models.project import Project, ProjectStatus
from app.db.models.report import Report, ReportType
from app.db.models.report_session import ReportSession, SessionStatus
from app.db.models.user import User, UserRole

__all__ = [
    "Company",
    "Message",
    "MessageType",
    "Photo",
    "Project",
    "ProjectStatus",
    "Report",
    "ReportSession",
    "ReportType",
    "SessionStatus",
    "User",
    "UserRole",
]
