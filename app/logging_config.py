"""Lightweight stdlib logging config. Keeps PII scrubbing simple for MVP."""
from __future__ import annotations

import logging
import re
import sys
from logging import LogRecord

from app.config import settings

# Patterns we never want in logs (basic DSGVO hygiene; not exhaustive).
_PHONE_RE = re.compile(r"\+?\d{6,15}")
_API_KEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{20,})")


class PIIScrubber(logging.Filter):
    """Scrubs the *fully formatted* message so %s-substituted phone numbers
    and API keys (which only exist after argument interpolation) are masked."""

    def filter(self, record: LogRecord) -> bool:
        try:
            formatted = record.getMessage()
        except Exception:
            return True
        scrubbed = _PHONE_RE.sub("<phone>", formatted)
        scrubbed = _API_KEY_RE.sub("<api-key>", scrubbed)
        if scrubbed != formatted:
            record.msg = scrubbed
            record.args = ()
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(PIIScrubber())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # Quiet down chatty libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
