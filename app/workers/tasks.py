"""RQ worker tasks. Bridge sync RQ entrypoints to async services via asyncio.run."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db.models import Message, MessageType
from app.db.session import session_scope
from app.services.storage import download_twilio_media
from app.services.telegram import (
    download_telegram_media,
    send_text as telegram_send_text,
)
from app.services.transcription import transcribe
from app.services.whatsapp import send_text

logger = logging.getLogger(__name__)


# Short language-code -> Polier-friendly label for the confirmation reply.
_LANG_LABEL = {
    "de": "Deutsch",
    "hr": "Hrvatski",
    "bs": "Bosanski",
    "sr": "Srpski",
    "pl": "Polski",
    "ro": "Română",
    "tr": "Türkçe",
    "en": "English",
}


def _label_for(lang: str) -> str:
    return _LANG_LABEL.get(lang, lang.upper())


def _confirmation_text(transcript: str, language: str) -> str:
    """Build the German confirmation reply per spec: 'Verstanden ({lang}): ...'"""
    snippet = transcript.strip().replace("\n", " ")
    if len(snippet) > 80:
        snippet = snippet[:77].rstrip() + "..."
    return f"✅ Verstanden ({_label_for(language)}): {snippet}"


async def _process_voice_message(message_id: uuid.UUID) -> None:
    """Download → transcribe → persist transcript → reply Polieru."""
    async with session_scope() as db:
        message = await db.get(Message, message_id)
        if message is None:
            logger.warning("process_voice_message: id=%s not found", message_id)
            return
        if message.processed:
            logger.info("process_voice_message: id=%s already processed", message_id)
            return
        if message.type != MessageType.voice or not message.media_url:
            logger.info("process_voice_message: id=%s is not voice/media", message_id)
            return

        try:
            stored = await download_twilio_media(
                message.media_url,
                twilio_sid=message.twilio_sid,
                expected_content_type=message.media_content_type,
            )
            result = await transcribe(stored.path)

            message.transcript = result.text
            message.detected_language = result.language
            payload = dict(message.payload or {})
            payload["transcription"] = {
                "duration_seconds": result.duration_seconds,
                "language": result.language,
                "stored_path": str(stored.path),
            }
            message.payload = payload
            message.processed = True
            message.processed_at = datetime.now(timezone.utc)

            reply = _confirmation_text(result.text, result.language)
            await send_text(message.from_number, reply)
            logger.info(
                "Transcribed message id=%s lang=%s duration=%.1fs",
                message.id,
                result.language,
                result.duration_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - record any failure on the message row
            logger.exception("Voice processing failed for id=%s", message_id)
            message.processing_error = f"{type(exc).__name__}: {exc}"[:1000]
            message.processed = False
            raise


def process_voice_message(message_id: str) -> None:
    """Sync RQ entrypoint. RQ serialises args, so we accept a string UUID."""
    asyncio.run(_process_voice_message(uuid.UUID(message_id)))


async def _process_telegram_voice_message(message_id: uuid.UUID) -> None:
    """Telegram counterpart: file_id -> getFile -> stream -> Whisper -> sendMessage."""
    async with session_scope() as db:
        message = await db.get(Message, message_id)
        if message is None:
            logger.warning("process_telegram_voice_message: id=%s not found", message_id)
            return
        if message.processed:
            logger.info("process_telegram_voice_message: id=%s already processed", message_id)
            return
        if message.type != MessageType.voice or not message.media_url:
            logger.info(
                "process_telegram_voice_message: id=%s is not voice/media", message_id
            )
            return

        chat_id = int(message.from_number)
        try:
            stored = await download_telegram_media(
                message.media_url,  # for Telegram, media_url holds the file_id
                expected_content_type=message.media_content_type,
            )
            result = await transcribe(stored.path)

            message.transcript = result.text
            message.detected_language = result.language
            payload = dict(message.payload or {})
            payload["transcription"] = {
                "duration_seconds": result.duration_seconds,
                "language": result.language,
                "stored_path": str(stored.path),
            }
            message.payload = payload
            message.processed = True
            message.processed_at = datetime.now(timezone.utc)

            reply = _confirmation_text(result.text, result.language)
            await telegram_send_text(chat_id, reply)
            logger.info(
                "Transcribed Telegram message id=%s lang=%s duration=%.1fs",
                message.id,
                result.language,
                result.duration_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Telegram voice processing failed for id=%s", message_id)
            message.processing_error = f"{type(exc).__name__}: {exc}"[:1000]
            message.processed = False
            raise


def process_telegram_voice_message(message_id: str) -> None:
    """Sync RQ entrypoint for Telegram voice messages."""
    asyncio.run(_process_telegram_voice_message(uuid.UUID(message_id)))
