"""HTML → PDF rendering with Jinja2 + WeasyPrint.

The template lives in app/templates/bautagebuch.html. We pre-resolve the
template directory once and cache the Environment to keep per-render cost
low (worker handles many sessions per day).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.config import settings
from app.db.models import Company, Project
from app.services.report_generator import BautagebuchData

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


@dataclass(frozen=True)
class RenderedReport:
    pdf_path: Path
    html: str
    pdf_bytes: bytes


def _local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone))


def _output_path_for(session_id: str) -> Path:
    today = _local_now().strftime("%Y/%m/%d")
    base = Path(settings.storage_local_path) / "reports" / today
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{session_id}.pdf"


def render_bautagebuch(
    data: BautagebuchData,
    *,
    company: Company,
    project: Project,
    session_id: str,
    template_name: str = "bautagebuch.html",
) -> RenderedReport:
    """Render Bautagebuch HTML + write PDF to storage. Returns paths + bytes."""
    template = _env().get_template(template_name)
    html_text = template.render(
        data=data,
        company=company,
        project=project,
        generated_at=_local_now(),
    )

    pdf_bytes = HTML(string=html_text, base_url=str(_TEMPLATES_DIR)).write_pdf()
    pdf_path = _output_path_for(session_id)
    pdf_path.write_bytes(pdf_bytes)

    logger.info(
        "Rendered Bautagebuch session=%s -> %s (%d bytes)",
        session_id,
        pdf_path,
        len(pdf_bytes),
    )
    return RenderedReport(pdf_path=pdf_path, html=html_text, pdf_bytes=pdf_bytes)
