"""Media download + local filesystem storage.

Twilio media URLs require HTTP basic auth with the AccountSid + AuthToken.
For MVP we save to a local directory laid out by date and twilio_sid; the
S3 backend ships in Phase 7.
"""
from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT: Final = httpx.Timeout(30.0, connect=10.0)


@dataclass(frozen=True)
class StoredMedia:
    """Result of a media download."""

    path: Path
    content_type: str | None
    size_bytes: int


def _extension_for(content_type: str | None) -> str:
    """Map a MIME type to a sensible extension. Whisper accepts ogg/mp3/m4a/wav/mp4."""
    if not content_type:
        return ".bin"
    mapping = {
        "audio/ogg": ".ogg",
        "audio/opus": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/m4a": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/heic": ".heic",
        "video/mp4": ".mp4",
    }
    if content_type in mapping:
        return mapping[content_type]
    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
    return guessed or ".bin"


def _local_path_for(twilio_sid: str, content_type: str | None) -> Path:
    """Return a date-partitioned path under STORAGE_LOCAL_PATH for this SID."""
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    base = Path(settings.storage_local_path) / today
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{twilio_sid}{_extension_for(content_type)}"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def download_twilio_media(
    media_url: str,
    twilio_sid: str,
    *,
    expected_content_type: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> StoredMedia:
    """Fetch a Twilio media URL with basic auth and write it under storage_local_path.

    Twilio responds with a 307 redirect to a temporary S3-style URL; httpx
    follows redirects by default. We auth on the first hop only - the S3
    URL is presigned.
    """
    auth = (
        settings.twilio_account_sid,
        settings.twilio_auth_token.get_secret_value(),
    )
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True)
    try:
        resp = await client.get(media_url, auth=auth)
        resp.raise_for_status()
        content = resp.content
        content_type = expected_content_type or resp.headers.get("content-type")
        target = _local_path_for(twilio_sid, content_type)
        target.write_bytes(content)
        logger.info(
            "Downloaded Twilio media sid=%s bytes=%d ctype=%s -> %s",
            twilio_sid,
            len(content),
            content_type,
            target,
        )
        return StoredMedia(path=target, content_type=content_type, size_bytes=len(content))
    finally:
        if own_client:
            await client.aclose()
