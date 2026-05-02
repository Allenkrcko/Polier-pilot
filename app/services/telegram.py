"""Telegram Bot API client.

Two operations we care about for the MVP:

1. Download a media object (voice / photo / document) given its `file_id`.
   Telegram is two-step: getFile -> file_path -> https://api.telegram.org/
   file/bot{token}/{file_path}.

2. Send a text reply to a chat_id.

Both methods accept an optional httpx.AsyncClient for reuse and testing.
"""
from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

_API_BASE: Final = "https://api.telegram.org"
_FILE_BASE: Final = "https://api.telegram.org/file"
_DEFAULT_TIMEOUT: Final = httpx.Timeout(30.0, connect=10.0)
_MAX_BODY_LEN: Final = 4096  # Telegram max characters per sendMessage call


class TelegramError(RuntimeError):
    """Raised when Telegram returns ok=false or HTTP non-2xx."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Telegram API {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class StoredMedia:
    path: Path
    content_type: str | None
    size_bytes: int


@dataclass(frozen=True)
class SentMessage:
    message_id: int
    chat_id: int
    text: str


def _bot_token() -> str:
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    return settings.telegram_bot_token.get_secret_value()


def _local_path_for(file_id: str, suffix: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    base = Path(settings.storage_local_path) / "telegram" / today
    base.mkdir(parents=True, exist_ok=True)
    safe_id = file_id.replace("/", "_")[:200]
    return base / f"{safe_id}{suffix}"


def _suffix_from_path(file_path: str, fallback_mime: str | None) -> str:
    """Derive an extension from the Telegram-provided file_path."""
    if "." in Path(file_path).name:
        return "." + Path(file_path).name.split(".")[-1]
    if fallback_mime:
        guess = mimetypes.guess_extension(fallback_mime.split(";")[0].strip())
        if guess:
            return guess
    return ".bin"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
async def get_file_path(
    file_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Resolve a Telegram file_id to its server-side file_path."""
    url = f"{_API_BASE}/bot{_bot_token()}/getFile"
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    try:
        resp = await client.get(url, params={"file_id": file_id})
        if resp.status_code >= 400:
            raise TelegramError(resp.status_code, resp.text)
        body = resp.json()
        if not body.get("ok"):
            raise TelegramError(resp.status_code, resp.text)
        return body["result"]["file_path"]
    finally:
        if own_client:
            await client.aclose()


async def download_telegram_media(
    file_id: str,
    *,
    expected_content_type: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> StoredMedia:
    """Two-step download: getFile -> stream the file URL to local storage."""
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    try:
        file_path = await get_file_path(file_id, client=client)
        media_url = f"{_FILE_BASE}/bot{_bot_token()}/{file_path}"
        resp = await client.get(media_url)
        resp.raise_for_status()
        content = resp.content
        content_type = expected_content_type or resp.headers.get("content-type")
        target = _local_path_for(file_id, _suffix_from_path(file_path, content_type))
        target.write_bytes(content)
        logger.info(
            "Downloaded Telegram media file_id=%s bytes=%d -> %s",
            file_id,
            len(content),
            target,
        )
        return StoredMedia(path=target, content_type=content_type, size_bytes=len(content))
    finally:
        if own_client:
            await client.aclose()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
async def send_text(
    chat_id: int,
    body: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> SentMessage:
    """Send a text reply via the Bot API.

    Truncates at 4096 chars (Telegram limit) and retries on 5xx/transport
    errors. 4xx (e.g. blocked-by-user, chat-not-found) raises immediately.
    """
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": body[:_MAX_BODY_LEN],
        "disable_web_page_preview": True,
    }
    url = f"{_API_BASE}/bot{_bot_token()}/sendMessage"
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    try:
        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            if resp.status_code < 500:
                raise TelegramError(resp.status_code, resp.text)
            resp.raise_for_status()
        body_json = resp.json()
        if not body_json.get("ok"):
            raise TelegramError(resp.status_code, resp.text)
        result = body_json["result"]
        return SentMessage(
            message_id=int(result["message_id"]),
            chat_id=int(result["chat"]["id"]),
            text=result.get("text", ""),
        )
    finally:
        if own_client:
            await client.aclose()
