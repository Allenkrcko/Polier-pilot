"""Twilio WhatsApp webhook. Persists raw inbound and returns empty TwiML.

Processing (transcription, classification, replies) happens in workers in
later phases - this endpoint only validates the signature and writes to
the DB synchronously so retries are idempotent on twilio_sid.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.request_validator import RequestValidator

from app.config import settings
from app.db.models import Message, MessageType, User
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# Map of Twilio media MIME prefixes -> our MessageType enum.
# (Twilio sends e.g. "audio/ogg" for WhatsApp voice memos.)
def _classify_media(content_type: str | None) -> MessageType:
    if not content_type:
        return MessageType.text
    if content_type.startswith("audio/"):
        return MessageType.voice
    if content_type.startswith("image/"):
        return MessageType.photo
    if content_type.startswith("video/"):
        return MessageType.video
    if content_type.startswith("application/") or content_type.startswith("text/"):
        return MessageType.document
    return MessageType.other


async def _verify_twilio_signature(request: Request, form: dict[str, Any]) -> None:
    """Validate `X-Twilio-Signature` per Twilio's webhook security guide.

    Skipped when TWILIO_WEBHOOK_VALIDATE=false (useful for local curl tests).
    """
    if not settings.twilio_webhook_validate:
        return

    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Missing X-Twilio-Signature")

    validator = RequestValidator(settings.twilio_auth_token.get_secret_value())
    # Twilio signs the *public* URL the request was sent to; reconstruct from headers
    # so it works behind a reverse proxy / ngrok.
    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    forwarded_host = request.headers.get("X-Forwarded-Host", request.url.netloc)
    public_url = f"{forwarded_proto}://{forwarded_host}{request.url.path}"

    if not validator.validate(public_url, form, signature):
        logger.warning("Twilio signature validation failed for %s", public_url)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid Twilio signature")


@router.post("/twilio", status_code=status.HTTP_200_OK)
async def twilio_inbound(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Receive a WhatsApp message via Twilio.

    Twilio sends application/x-www-form-urlencoded. We persist the message
    (and any media URLs) and return an empty TwiML so Twilio does not
    auto-reply - the worker pipeline will respond asynchronously later.
    """
    # Twilio webhooks are application/x-www-form-urlencoded with single-valued
    # keys, so a flat dict is sufficient for both persistence and signature checks.
    form_data = await request.form()
    flat: dict[str, Any] = {k: str(v) for k, v in form_data.items()}

    await _verify_twilio_signature(request, flat)

    twilio_sid = flat.get("MessageSid") or flat.get("SmsMessageSid")
    if not twilio_sid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Missing MessageSid")

    # Idempotency: Twilio retries on 5xx; ignore duplicates.
    existing = await db.scalar(select(Message).where(Message.twilio_sid == twilio_sid))
    if existing is not None:
        logger.info("Duplicate Twilio webhook for sid=%s, ignoring", twilio_sid)
        return Response(content="<Response/>", media_type="application/xml")

    from_number = flat.get("From", "")
    to_number = flat.get("To", "")
    body = flat.get("Body") or None

    # Look up user by whatsapp number (created out-of-band by admin for MVP).
    user = await db.scalar(select(User).where(User.whatsapp_number == from_number))
    user_id = user.id if user is not None else None
    if user is None:
        logger.warning("Inbound from unknown number %s (sid=%s)", from_number, twilio_sid)

    # Twilio attaches up to N media items (NumMedia=N, MediaUrl0..MediaUrlN-1).
    num_media = int(flat.get("NumMedia") or 0)
    primary_media_url: str | None = None
    primary_media_type: str | None = None
    media_payload: list[dict[str, str]] = []
    for i in range(num_media):
        url = flat.get(f"MediaUrl{i}")
        ctype = flat.get(f"MediaContentType{i}")
        if url:
            media_payload.append({"url": url, "content_type": ctype or ""})
            if i == 0:
                primary_media_url = url
                primary_media_type = ctype

    msg_type = _classify_media(primary_media_type) if num_media > 0 else MessageType.text

    message = Message(
        user_id=user_id,
        twilio_sid=twilio_sid,
        type=msg_type,
        from_number=from_number,
        to_number=to_number,
        media_url=primary_media_url,
        media_content_type=primary_media_type,
        raw_text=body,
        payload={
            "profile_name": flat.get("ProfileName"),
            "wa_id": flat.get("WaId"),
            "num_media": num_media,
            "media": media_payload,
        },
        received_at=datetime.now(timezone.utc),
        processed=False,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    logger.info(
        "Persisted Twilio message sid=%s type=%s from=<phone> media=%d",
        twilio_sid,
        msg_type.value,
        num_media,
    )

    # Enqueue async processing. Voice -> transcription pipeline (Phase 2),
    # photo -> EXIF + storage pipeline (Phase 3). Text is Phase 4+.
    if message.media_url and msg_type in (MessageType.voice, MessageType.photo):
        try:
            from app.workers.queue import default_queue
            from app.workers.tasks import process_photo_message, process_voice_message

            queue = default_queue()
            if msg_type == MessageType.voice:
                queue.enqueue(process_voice_message, str(message.id))
                logger.info("Enqueued process_voice_message for id=%s", message.id)
            else:  # photo
                queue.enqueue(process_photo_message, str(message.id), "twilio")
                logger.info("Enqueued process_photo_message (twilio) for id=%s", message.id)
        except Exception:
            # Never fail the webhook on a queue hiccup - Twilio would retry forever.
            logger.exception("Failed to enqueue processing for id=%s", message.id)

    # Empty TwiML - reply happens via async worker -> Twilio REST in later phases.
    return Response(content="<Response/>", media_type="application/xml")
