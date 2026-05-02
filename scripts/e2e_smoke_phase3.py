"""End-to-end smoke for Phase 3 (Telegram path with seeded user).

Drives the full inbound voice + photo flow without contacting any
external services:

  1. Seed Company / Project / User with telegram_chat_id + current_project_id
  2. POST a voice Update -> assert classification + session bind
  3. POST a photo Update -> assert EXIF + weather + session bind
  4. POST a 'Feierabend' voice -> assert session.status = pending_review

Run with the FastAPI app already running on :8000:
    .venv/bin/python -m scripts.e2e_smoke_phase3
"""
from __future__ import annotations

import asyncio
import json
import struct
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path

import httpx
from PIL import Image, TiffImagePlugin
from sqlalchemy import select

from app.config import settings
from app.db.models import (
    Company,
    Message,
    Project,
    ProjectStatus,
    ReportSession,
    SessionStatus,
    User,
    UserRole,
)
from app.db.session import session_scope
from app.logging_config import configure_logging
from app.services.classifier import ClassificationResult
from app.services.telegram import SentMessage, StoredMedia
from app.services.transcription import TranscriptionResult
from app.workers import tasks
from app.workers.queue import default_queue

WEBHOOK_URL = "http://127.0.0.1:8000/webhooks/telegram"
CHAT_ID = 999_888_777

COMPANY_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


async def seed_db() -> None:
    step("Step 0: seed Company / Project / User")
    async with session_scope() as db:
        # Idempotent: drop any prior test user with our chat id first.
        existing = await db.scalar(select(User).where(User.telegram_chat_id == CHAT_ID))
        if existing is not None:
            await db.delete(existing)
            await db.flush()

        db.add(
            Company(
                id=COMPANY_ID, name="ASB Smoke Test GmbH", address="Berlin"
            )
        )
        db.add(
            Project(
                id=PROJECT_ID,
                company_id=COMPANY_ID,
                name="FTTH Bayreuth Los 3",
                project_number="2026-FTTH-003",
                status=ProjectStatus.active,
            )
        )
        db.add(
            User(
                id=USER_ID,
                company_id=COMPANY_ID,
                name="Mirzo Polier",
                role=UserRole.polier,
                telegram_chat_id=CHAT_ID,
                language_pref="bs",
                current_project_id=PROJECT_ID,
                active=True,
            )
        )
    print(f"  company={COMPANY_ID} project={PROJECT_ID} user={USER_ID}")


def install_mocks() -> None:
    step("Step 1: monkey-patch external services")

    fake_audio = Path("/tmp/polier-test-storage/p3-fake.ogg")
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"OggS" + b"\x00" * 256)

    # Build a real JPEG with EXIF GPS so the EXIF extractor has something to chew.
    fake_image = Path("/tmp/polier-test-storage/p3-fake.jpg")

    def _rational(num: int, den: int = 1) -> TiffImagePlugin.IFDRational:
        return TiffImagePlugin.IFDRational(num, den)

    img = Image.new("RGB", (32, 32), color="white")
    e = img.getexif()
    e[306] = "2026:05:02 14:23:00"
    e[36867] = "2026:05:02 14:23:00"
    e[34853] = {
        1: "N",
        2: (_rational(52), _rational(31), _rational(34, 10)),
        3: "E",
        4: (_rational(13), _rational(24), _rational(20, 10)),
        5: 0,
        6: _rational(34),
    }
    img.save(fake_image, exif=e.tobytes())

    async def fake_download_voice(file_id, *, expected_content_type=None, client=None):
        return StoredMedia(path=fake_audio, content_type="audio/ogg", size_bytes=fake_audio.stat().st_size)

    async def fake_download_photo(file_id, *, expected_content_type=None, client=None):
        return StoredMedia(
            path=fake_image, content_type="image/jpeg", size_bytes=fake_image.stat().st_size
        )

    async def fake_download_telegram_media(file_id, *, expected_content_type=None, client=None):
        if (expected_content_type or "").startswith("image/"):
            return await fake_download_photo(file_id)
        return await fake_download_voice(file_id)

    transcripts = iter(
        [
            (
                "Danas smo postavili 80 metara FTTH kabla u Birkenstrasse i pokrili rov.",
                "hr",
                "bautagebuch_entry",
                "Heute wurden 80 m FTTH-Kabel in der Birkenstraße verlegt.",
            ),
            (
                "Feierabend, gotovi smo za danas, javljam da generiram bautagebuch.",
                "hr",
                "feierabend",
                "Polier meldet Feierabend und fordert Bautagebuch an.",
            ),
        ]
    )

    next_intent = {"intent": "smalltalk", "summary": ""}

    async def fake_transcribe(audio_path, *, language_hint=None):
        text, lang, intent, summary = next(transcripts)
        next_intent["intent"] = intent
        next_intent["summary"] = summary
        return TranscriptionResult(text=text, language=lang, duration_seconds=12.0, segments=[])

    async def fake_classify(text, *, detected_language=None, client=None):
        return ClassificationResult(
            intent=next_intent["intent"],
            confidence=0.9,
            language=detected_language or "hr",
            summary=next_intent["summary"],
            mentions={},
        )

    sent: list[dict] = []

    async def fake_send_text(chat_id, body, *, client=None):
        sent.append({"chat_id": chat_id, "body": body})
        return SentMessage(message_id=1, chat_id=chat_id, text=body)

    # Open-Meteo mock
    async def fake_weather_fetch(*, lat, lon, work_date, client=None):
        from app.services.weather import WeatherSnapshot

        return WeatherSnapshot(
            date=work_date,
            temperature_min_c=8.4,
            temperature_max_c=17.2,
            temperature_mean_c=12.5,
            precipitation_mm=2.4,
            weather_code=61,
            weather_code_text="Leichter Regen",
        )

    tasks.download_telegram_media = fake_download_telegram_media  # type: ignore[assignment]
    tasks.transcribe = fake_transcribe  # type: ignore[assignment]
    tasks.telegram_send_text = fake_send_text  # type: ignore[assignment]
    tasks.classify = fake_classify  # type: ignore[assignment]
    tasks.weather_service.fetch = fake_weather_fetch  # type: ignore[assignment]
    tasks._E2E_SENT = sent  # type: ignore[attr-defined]

    print("  download / transcribe / classify / send_text / weather -> stubbed")


async def post_update(update_id: int, message_id: int, *, voice: bool = True, has_photo: bool = False) -> None:
    secret = (
        settings.telegram_webhook_secret.get_secret_value()
        if settings.telegram_webhook_secret
        else None
    )
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}

    common_msg = {
        "message_id": message_id,
        "from": {"id": CHAT_ID, "is_bot": False, "first_name": "Mirzo", "language_code": "hr"},
        "chat": {"id": CHAT_ID, "type": "private"},
        "date": 1761600000,
    }
    if voice:
        common_msg["voice"] = {
            "file_id": f"AwAC-{message_id}",
            "duration": 12,
            "mime_type": "audio/ogg",
            "file_size": 8000,
        }
    if has_photo:
        common_msg["photo"] = [
            {"file_id": f"PHOTO-{message_id}", "width": 320, "height": 320},
            {"file_id": f"PHOTO-{message_id}-big", "width": 1280, "height": 1280},
        ]

    payload = {"update_id": update_id, "message": common_msg}
    async with httpx.AsyncClient() as client:
        resp = await client.post(WEBHOOK_URL, json=payload, headers=headers)
    assert resp.status_code == 200, resp.text


async def run_queued_tasks() -> None:
    """Drain the RQ queue by running each job's func body inline."""
    queue = default_queue()
    drained = 0
    for job in list(queue.jobs):
        if job.func_name.endswith("process_telegram_voice_message"):
            await tasks._process_telegram_voice_message(uuid.UUID(job.args[0]))
        elif job.func_name.endswith("process_photo_message"):
            await tasks._process_photo_message(
                uuid.UUID(job.args[0]), provider=job.args[1] if len(job.args) > 1 else "twilio"
            )
        job.delete()
        drained += 1
    print(f"  drained {drained} jobs")


async def main() -> None:
    configure_logging()
    await seed_db()
    install_mocks()

    step("Step 2: voice update (bautagebuch_entry)")
    await post_update(8001, 101, voice=True)

    step("Step 3: photo update (with EXIF GPS)")
    await post_update(8002, 102, voice=False, has_photo=True)

    step("Step 4: voice update (Feierabend trigger)")
    await post_update(8003, 103, voice=True)

    step("Step 5: drain RQ jobs")
    await run_queued_tasks()

    step("Step 6: verify DB state")
    async with session_scope() as db:
        msgs = (
            await db.execute(
                select(Message).where(Message.user_id == USER_ID).order_by(Message.received_at)
            )
        ).scalars().all()

        for m in msgs:
            cls = (m.payload or {}).get("classification", {})
            print(
                f"  msg id={m.id} type={m.type.value} intent={m.classified_intent} "
                f"session={m.session_id} processed={m.processed}"
            )
            print(f"    summary={cls.get('summary')!r}")

        photo_msg = next(m for m in msgs if m.type.value == "photo")
        weather = (photo_msg.payload or {}).get("weather", {})
        exif_in_payload = (photo_msg.payload or {}).get("exif", {})
        print(f"\n  photo.exif.gps_lat={exif_in_payload.get('gps_lat')}")
        print(f"  photo.weather.summary_de={weather.get('summary_de')}")

        sessions = (
            await db.execute(select(ReportSession).where(ReportSession.user_id == USER_ID))
        ).scalars().all()
        for s in sessions:
            print(f"  session id={s.id} date={s.work_date} status={s.status.value}")

        # Assertions
        intents = {m.classified_intent for m in msgs}
        assert "bautagebuch_entry" in intents, intents
        assert "feierabend" in intents, intents
        assert all(m.session_id for m in msgs), "all messages must be bound to a session"
        assert any(m.classified_intent == "feierabend" for m in msgs)
        assert weather.get("summary_de") == "Leichter Regen, 8–17 °C, 2.4 mm Niederschlag"
        assert exif_in_payload.get("gps_lat") is not None
        assert sessions and sessions[0].status == SessionStatus.pending_review

        sent: list[dict] = getattr(tasks, "_E2E_SENT", [])
        print(f"\n  send_text invocations: {len(sent)}")
        for s in sent:
            print(f"    -> chat={s['chat_id']} body={s['body']!r}")
        assert len(sent) == 3  # two voice replies + one photo reply

    step("ALL CHECKS PASSED (Phase 3)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        sys.exit(1)
