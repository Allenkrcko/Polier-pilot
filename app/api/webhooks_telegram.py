"""Telegram Bot webhook.

Telegram sends a JSON Update on every inbound event. We persist the
relevant ones (message + media) and enqueue the worker for voice/audio.

Security:
- Verified via the X-Telegram-Bot-Api-Secret-Token request header that
  Telegram echoes back from setWebhook. Plain shared secret comparison
  with constant-time check.
"""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Message, MessageType, User
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_secret(request: Request) -> None:
    """Reject requests without the secret token Telegram echoes from setWebhook."""
    if settings.telegram_webhook_secret is None:
        # Allow when no secret is configured - dev convenience only.
        return
    expected = settings.telegram_webhook_secret.get_secret_value()
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(expected, received):
        logger.warning("Telegram webhook rejected: bad/missing secret token")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid secret token")


def _classify(message: dict[str, Any]) -> tuple[MessageType, str | None, str | None]:
    """Return (type, file_id, mime_type) for the most relevant attachment.

    Telegram fields we care about (mutually exclusive in practice):
      - voice: PTT recording (always opus, mime "audio/ogg")
      - audio: regular audio file (mp3 etc.)
      - photo: array of PhotoSize - we pick the largest
      - video / video_note
      - document: anything else
      - text only otherwise
    """
    if (voice := message.get("voice")) is not None:
        return MessageType.voice, voice.get("file_id"), voice.get("mime_type") or "audio/ogg"
    if (audio := message.get("audio")) is not None:
        return MessageType.voice, audio.get("file_id"), audio.get("mime_type")
    if (photo := message.get("photo")):
        # Pick the largest variant (last in the array per Telegram API).
        largest = photo[-1]
        return MessageType.photo, largest.get("file_id"), "image/jpeg"
    if (video := message.get("video")) is not None:
        return MessageType.video, video.get("file_id"), video.get("mime_type") or "video/mp4"
    if (note := message.get("video_note")) is not None:
        return MessageType.video, note.get("file_id"), "video/mp4"
    if (doc := message.get("document")) is not None:
        return MessageType.document, doc.get("file_id"), doc.get("mime_type")
    return MessageType.text, None, None


@router.post("/telegram", status_code=status.HTTP_200_OK)
async def telegram_inbound(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Receive a Telegram Update. Always returns ok so Telegram does not retry."""
    _verify_secret(request)
    update: dict[str, Any] = await request.json()

    # Telegram sends many event types; we only care about message-bearing ones.
    message: dict[str, Any] | None = (
        update.get("message")
        or update.get("edited_message")
        or update.get("channel_post")
    )
    if message is None:
        # Other events (callback_query, inline_query, ...) get acked but ignored.
        return {"ok": "ignored"}

    update_id = update.get("update_id")
    tg_message_id = message.get("message_id")
    twilio_sid = f"tg-{update_id}-{tg_message_id}"  # synthetic SID kept unique

    # Idempotency: setWebhook retries on non-2xx within ~5s, dedupe.
    existing = await db.scalar(select(Message).where(Message.twilio_sid == twilio_sid))
    if existing is not None:
        logger.info("Duplicate Telegram update_id=%s ignored", update_id)
        return {"ok": "duplicate"}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    from_user = message.get("from") or {}
    from_chat_id = from_user.get("id") or chat_id

    msg_type, file_id, mime = _classify(message)

    # Bind to a known user via telegram_chat_id (created out-of-band by admin for MVP).
    user = await db.scalar(select(User).where(User.telegram_chat_id == from_chat_id))
    if user is None:
        logger.warning("Inbound from unknown chat_id=%s", from_chat_id)

    text = message.get("text") or message.get("caption")
    payload = {
        "provider": "telegram",
        "update_id": update_id,
        "tg_message_id": tg_message_id,
        "chat_id": chat_id,
        "from": {
            "id": from_chat_id,
            "username": from_user.get("username"),
            "first_name": from_user.get("first_name"),
            "last_name": from_user.get("last_name"),
            "language_code": from_user.get("language_code"),
        },
        "file_id": file_id,
        "mime_type": mime,
    }

    persisted = Message(
        user_id=user.id if user else None,
        twilio_sid=twilio_sid,
        type=msg_type,
        from_number=str(from_chat_id),  # we reuse this column for both providers
        to_number="telegram",
        media_url=file_id,  # store the Telegram file_id where we previously stored URL
        media_content_type=mime,
        raw_text=text,
        payload=payload,
        received_at=datetime.now(timezone.utc),
        processed=False,
    )
    db.add(persisted)
    await db.commit()
    await db.refresh(persisted)

    logger.info(
        "Persisted Telegram message id=%s type=%s chat=<phone> file_id=%s",
        persisted.id,
        msg_type.value,
        file_id,
    )

    try:
        from app.workers.queue import default_queue
        from app.workers.tasks import (
            process_photo_message,
            process_telegram_voice_message,
            process_text_message,
        )

        queue = default_queue()
        if msg_type == MessageType.voice and file_id:
            queue.enqueue(process_telegram_voice_message, str(persisted.id))
            logger.info("Enqueued process_telegram_voice_message for id=%s", persisted.id)
        elif msg_type == MessageType.photo and file_id:
            queue.enqueue(process_photo_message, str(persisted.id), "telegram")
            logger.info("Enqueued process_photo_message (telegram) for id=%s", persisted.id)
        elif msg_type == MessageType.text and text:
            queue.enqueue(process_text_message, str(persisted.id), "telegram")
            logger.info("Enqueued process_text_message (telegram) for id=%s", persisted.id)
    except Exception:
        logger.exception("Failed to enqueue Telegram processing for id=%s", persisted.id)

    return {"ok": "received"}
