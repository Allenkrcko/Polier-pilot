"""RQ worker tasks. Bridge sync RQ entrypoints to async services via asyncio.run."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Message, MessageType, Photo, ReportSession, SessionStatus, User
from app.db.session import session_scope
from app.services import weather as weather_service
from app.services.classifier import ClassificationResult, classify
from app.services.exif import ExifData, extract as extract_exif
from app.services.sessions import (
    get_or_create_session,
    local_today,
    mark_session_pending_review,
)
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


# Short German label per intent for the operator-facing log line / payload.
_INTENT_LABEL_DE: dict[str, str] = {
    "bautagebuch_entry": "Bautagebuch-Eintrag",
    "aufmass": "Aufmaß",
    "maengel": "Mängel",
    "behinderung": "Behinderung",
    "feierabend": "Feierabend",
    "question": "Frage",
    "smalltalk": "Smalltalk",
}


async def _load_user(db: AsyncSession, message: Message) -> User | None:
    """Load the User who sent this message, if any."""
    if message.user_id is None:
        return None
    return await db.get(User, message.user_id)


async def _classify_and_bind_session(
    db: AsyncSession,
    message: Message,
    *,
    text: str,
    detected_language: str | None,
) -> tuple[ClassificationResult, ReportSession | None]:
    """Run the classifier on `text`, persist to message, then bind a session."""
    cls = await classify(text, detected_language=detected_language)

    payload = dict(message.payload or {})
    payload["classification"] = {
        "intent": cls.intent,
        "confidence": cls.confidence,
        "language": cls.language,
        "summary": cls.summary,
        "mentions": cls.mentions,
    }
    message.payload = payload
    message.classified_intent = cls.intent

    user = await _load_user(db, message)
    session: ReportSession | None = None
    if user is not None:
        session = await get_or_create_session(db, user=user)
        if session is not None:
            message.session_id = session.id
            if cls.intent == "feierabend":
                await mark_session_pending_review(db, session)
    else:
        logger.info(
            "Skipping session bind for message id=%s (no resolved user)", message.id
        )

    logger.info(
        "Classified message id=%s intent=%s lang=%s session=%s",
        message.id,
        cls.intent,
        cls.language,
        session.id if session else None,
    )
    return cls, session


async def _enrich_message_with_weather(
    message: Message,
    *,
    lat: float,
    lon: float,
    work_date,  # date - imported via from datetime import below
) -> None:
    """Cache an Open-Meteo snapshot on message.payload['weather']. Best-effort."""
    payload = dict(message.payload or {})
    if payload.get("weather"):
        return
    try:
        snapshot = await weather_service.fetch(lat=lat, lon=lon, work_date=work_date)
    except Exception as exc:  # noqa: BLE001 - weather is best-effort
        logger.warning("Weather fetch failed for message %s: %s", message.id, exc)
        return
    payload["weather"] = {
        "date": snapshot.date.isoformat(),
        "lat": lat,
        "lon": lon,
        "temperature_min_c": snapshot.temperature_min_c,
        "temperature_max_c": snapshot.temperature_max_c,
        "temperature_mean_c": snapshot.temperature_mean_c,
        "precipitation_mm": snapshot.precipitation_mm,
        "weather_code": snapshot.weather_code,
        "weather_code_text": snapshot.weather_code_text,
        "summary_de": snapshot.summary_de,
    }
    message.payload = payload


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

            await _classify_and_bind_session(
                db, message, text=result.text, detected_language=result.language
            )

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

            await _classify_and_bind_session(
                db, message, text=result.text, detected_language=result.language
            )

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


def _photo_reply(exif: Any, lang: str = "de") -> str:
    """German confirmation for a photo, including GPS + capture time when present."""
    parts = ["📸 Foto erhalten."]
    if exif.gps_lat is not None and exif.gps_lon is not None:
        parts.append(f"Standort: {exif.gps_lat:.5f}°N, {exif.gps_lon:.5f}°E.")
    if exif.captured_at is not None:
        parts.append(f"Aufnahme: {exif.captured_at.strftime('%d.%m.%Y %H:%M')}.")
    if exif.gps_lat is None and exif.captured_at is None:
        parts.append("Hinweis: keine GPS- oder Zeitangaben im Bild gefunden.")
    return " ".join(parts)


async def _process_photo_message(
    message_id: uuid.UUID,
    *,
    provider: str,
) -> None:
    """Photo pipeline: download → EXIF → persist Photo row → reply confirmation."""
    async with session_scope() as db:
        message = await db.get(Message, message_id)
        if message is None:
            logger.warning("process_photo_message: id=%s not found", message_id)
            return
        if message.processed:
            logger.info("process_photo_message: id=%s already processed", message_id)
            return
        if message.type != MessageType.photo or not message.media_url:
            logger.info("process_photo_message: id=%s is not photo/media", message_id)
            return

        try:
            if provider == "telegram":
                stored = await download_telegram_media(
                    message.media_url,
                    expected_content_type=message.media_content_type,
                )
            else:
                stored = await download_twilio_media(
                    message.media_url,
                    twilio_sid=message.twilio_sid,
                    expected_content_type=message.media_content_type,
                )

            exif = extract_exif(stored.path)

            photo = Photo(
                message_id=message.id,
                file_path=str(stored.path),
                storage_backend="local",
                content_type=stored.content_type,
                file_size_bytes=stored.size_bytes,
                gps_lat=exif.gps_lat,
                gps_lon=exif.gps_lon,
                exif_timestamp=exif.captured_at,
            )
            db.add(photo)

            payload = dict(message.payload or {})
            payload["exif"] = {
                "gps_lat": exif.gps_lat,
                "gps_lon": exif.gps_lon,
                "altitude_m": exif.gps_altitude_m,
                "captured_at": exif.captured_at.isoformat() if exif.captured_at else None,
                "camera_make": exif.camera_make,
                "camera_model": exif.camera_model,
                "width": exif.width,
                "height": exif.height,
            }
            message.payload = payload

            # Bind to today's session before enriching weather (so we can use
            # the session's work_date for the historical lookup).
            user = await _load_user(db, message)
            session: ReportSession | None = None
            if user is not None:
                session = await get_or_create_session(db, user=user)
                if session is not None:
                    message.session_id = session.id

            if (
                session is not None
                and exif.gps_lat is not None
                and exif.gps_lon is not None
            ):
                await _enrich_message_with_weather(
                    message,
                    lat=exif.gps_lat,
                    lon=exif.gps_lon,
                    work_date=session.work_date,
                )

            message.processed = True
            message.processed_at = datetime.now(timezone.utc)

            reply = _photo_reply(exif)
            if provider == "telegram":
                await telegram_send_text(int(message.from_number), reply)
            else:
                await send_text(message.from_number, reply)

            logger.info(
                "Stored photo for message id=%s gps=(%s,%s) provider=%s",
                message.id,
                exif.gps_lat,
                exif.gps_lon,
                provider,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Photo processing failed for id=%s", message_id)
            message.processing_error = f"{type(exc).__name__}: {exc}"[:1000]
            message.processed = False
            raise


def process_photo_message(message_id: str, provider: str = "twilio") -> None:
    """Sync RQ entrypoint for photo messages."""
    asyncio.run(_process_photo_message(uuid.UUID(message_id), provider=provider))
