"""Twilio WhatsApp REST sender.

Wraps the Programmable Messaging API directly with httpx instead of the
sync `twilio` SDK, so worker jobs (and any FastAPI background path) can
fire-and-forget cleanly inside an event loop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

_API_BASE: Final = "https://api.twilio.com/2010-04-01"
_DEFAULT_TIMEOUT: Final = httpx.Timeout(15.0, connect=5.0)
_MAX_BODY_LEN: Final = 1600  # WhatsApp limit for a single message segment


class TwilioSendError(RuntimeError):
    """Raised when Twilio rejects a message send (non-2xx response)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Twilio API {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class SentMessage:
    sid: str
    status: str
    to: str


def _normalize_to(to: str) -> str:
    """Ensure the recipient is in `whatsapp:+E164` form Twilio requires."""
    return to if to.startswith("whatsapp:") else f"whatsapp:{to}"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
async def send_text(
    to: str,
    body: str,
    *,
    media_urls: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> SentMessage:
    """Send a WhatsApp text (and optional media) via Twilio.

    Truncates `body` at the per-segment limit to avoid silent drops and
    retries 3 times on network/5xx errors. 4xx are surfaced as
    TwilioSendError without retry.
    """
    payload: dict[str, object] = {
        "From": settings.twilio_whatsapp_from,
        "To": _normalize_to(to),
        "Body": body[:_MAX_BODY_LEN],
    }
    if media_urls:
        # Twilio allows up to 10 media URLs per message; cap defensively.
        payload["MediaUrl"] = media_urls[:10]

    url = f"{_API_BASE}/Accounts/{settings.twilio_account_sid}/Messages.json"
    auth = (
        settings.twilio_account_sid,
        settings.twilio_auth_token.get_secret_value(),
    )
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    try:
        resp = await client.post(url, data=payload, auth=auth)
        if resp.status_code >= 400:
            # 4xx is a permanent error (bad number, missing template, etc.) - do not retry.
            if resp.status_code < 500:
                raise TwilioSendError(resp.status_code, resp.text)
            resp.raise_for_status()  # 5xx -> retry via tenacity
        data = resp.json()
        sent = SentMessage(sid=data["sid"], status=data.get("status", "queued"), to=payload["To"])  # type: ignore[arg-type]
        logger.info("Sent WhatsApp sid=%s status=%s to=<phone>", sent.sid, sent.status)
        return sent
    finally:
        if own_client:
            await client.aclose()
