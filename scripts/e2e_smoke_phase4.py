"""End-to-end smoke for Phase 4 (Bautagebuch pipeline).

Drives the full inbound → report generation → PDF → confirmation flow
without contacting any external services:

  1. Seed Company / Project / User (telegram_chat_id)
  2. Send 2 voice updates (bautagebuch_entry, then aufmass)
  3. Send a photo with EXIF GPS (drives weather enrichment)
  4. Send a "Feierabend" voice → triggers generate_bautagebuch
  5. Drain queued tasks (voice, photo, generate_bautagebuch)
  6. Verify Report row + PDF exist + sent_document was called
  7. Send a "✅" text → confirmation handler runs, session finalized
  8. Verify final state: report.confirmed_at + session.status = finalized

Run with the FastAPI app already running on :8000:
    .venv/bin/python -m scripts.e2e_smoke_phase4
"""
from __future__ import annotations

import asyncio
import sys
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
    Report,
    ReportSession,
    SessionStatus,
    User,
    UserRole,
)
from app.db.session import session_scope
from app.logging_config import configure_logging
from app.services.classifier import ClassificationResult
from app.services.report_generator import BautagebuchData
from app.services.telegram import SentMessage, StoredMedia
from app.services.transcription import TranscriptionResult
from app.workers import tasks
from app.workers.queue import default_queue

WEBHOOK_URL = "http://127.0.0.1:8000/webhooks/telegram"
CHAT_ID = 777_888_999

COMPANY_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


async def seed_db() -> None:
    step("Step 0: seed Company / Project / User")
    async with session_scope() as db:
        existing = await db.scalar(select(User).where(User.telegram_chat_id == CHAT_ID))
        if existing is not None:
            await db.delete(existing)
            await db.flush()

        db.add(Company(id=COMPANY_ID, name="ASB Smoke Test GmbH", address="Berlin"))
        db.add(
            Project(
                id=PROJECT_ID,
                company_id=COMPANY_ID,
                name="FTTH Bayreuth Los 3",
                project_number="2026-FTTH-003",
                auftraggeber="Stadtwerke Bayreuth",
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
    print(f"  user={USER_ID}")


def install_mocks() -> Path:
    step("Step 1: install mocks for external services")

    storage_root = Path("/tmp/polier-test-storage")
    fake_audio = storage_root / "p4-fake.ogg"
    fake_image = storage_root / "p4-fake.jpg"
    storage_root.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"OggS" + b"\x00" * 256)

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

    transcripts = iter(
        [
            ("Danas smo postavili 80 metara FTTH kabla u Birkenstrasse.", "hr",
             "bautagebuch_entry", "Heute wurden 80 m FTTH-Kabel verlegt."),
            ("Ukupno 80 metara kabla, sve dokumentovano.", "hr",
             "aufmass", "80 m Kabel verlegt, dokumentiert."),
            ("Feierabend, gotovi smo, javljam za bautagebuch.", "hr",
             "feierabend", "Polier meldet Feierabend."),
        ]
    )
    next_intent = {"intent": "smalltalk", "summary": ""}

    async def fake_transcribe(audio_path, *, language_hint=None):
        text, lang, intent, summary = next(transcripts)
        next_intent["intent"] = intent
        next_intent["summary"] = summary
        return TranscriptionResult(text=text, language=lang, duration_seconds=10.0, segments=[])

    async def fake_classify(text, *, detected_language=None, client=None):
        return ClassificationResult(
            intent=next_intent["intent"],
            confidence=0.92,
            language=detected_language or "hr",
            summary=next_intent["summary"],
            mentions={
                "location": "Birkenstraße",
                "quantities": [{"wert": 80, "einheit": "m", "was": "FTTH-Kabel"}],
            },
        )

    async def fake_download(file_id, *, expected_content_type=None, client=None):
        if (expected_content_type or "").startswith("image/"):
            return StoredMedia(path=fake_image, content_type="image/jpeg",
                               size_bytes=fake_image.stat().st_size)
        return StoredMedia(path=fake_audio, content_type="audio/ogg",
                           size_bytes=fake_audio.stat().st_size)

    sent: list[dict] = []

    async def fake_send_text(chat_id, body, *, client=None):
        sent.append({"kind": "text", "chat_id": chat_id, "body": body})
        return SentMessage(message_id=1, chat_id=chat_id, text=body)

    async def fake_send_document(chat_id, file_path, *, caption=None,
                                  file_name=None, mime_type="application/pdf",
                                  client=None):
        sent.append({
            "kind": "document",
            "chat_id": chat_id,
            "file_name": file_name or file_path.name,
            "size": file_path.stat().st_size if file_path.exists() else None,
            "caption": caption,
        })
        return SentMessage(message_id=2, chat_id=chat_id, text=caption or "")

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

    async def fake_generate(session_id, *, client=None):
        return BautagebuchData(
            datum=date(2026, 5, 3),  # local_today() in Berlin tz
            baustelle="FTTH Bayreuth Los 3",
            projekt_nr="2026-FTTH-003",
            wetter="Leichter Regen, 8–17 °C, 2.4 mm Niederschlag",
            temperatur_celsius=12.5,
            arbeitszeit={"beginn": "07:00", "ende": "16:30", "pause_minuten": 30},
            anwesende_kolonnen=[{"firma": "ASB Smoke Test GmbH", "personen": 4, "gewerk": "Tiefbau"}],
            ausgefuehrte_arbeiten=[
                {"position": "Aushub Kabelgraben", "menge": 80, "einheit": "m", "ort": "Birkenstraße"},
                {"position": "Verlegung FTTH-Kabel", "menge": 80, "einheit": "m", "ort": "Birkenstraße"},
            ],
            geraete_einsatz=[{"geraet": "Bagger CAT 308", "stunden": 6.5}],
            materiallieferungen=[],
            besondere_vorkommnisse=["Leichter Regen ab 14:00"],
            behinderungen=[],
            fotos_referenz=[],
            polier_name="Mirzo Polier",
            stunden_gesamt=9.0,
            warnungen=[],
        )

    tasks.download_telegram_media = fake_download  # type: ignore[assignment]
    tasks.transcribe = fake_transcribe  # type: ignore[assignment]
    tasks.classify = fake_classify  # type: ignore[assignment]
    tasks.telegram_send_text = fake_send_text  # type: ignore[assignment]
    tasks.telegram_send_document = fake_send_document  # type: ignore[assignment]
    tasks.weather_service.fetch = fake_weather_fetch  # type: ignore[assignment]
    tasks.generate_for_session = fake_generate  # type: ignore[assignment]
    tasks._E2E_SENT = sent  # type: ignore[attr-defined]

    print("  download / transcribe / classify / send / generate / weather -> stubbed")
    return Path("/tmp/polier-test-storage")


async def post_telegram(update_id: int, message_id: int, *, voice=False,
                        photo=False, text=None) -> None:
    secret = (
        settings.telegram_webhook_secret.get_secret_value()
        if settings.telegram_webhook_secret
        else None
    )
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}
    msg = {
        "message_id": message_id,
        "from": {"id": CHAT_ID, "is_bot": False, "first_name": "Mirzo", "language_code": "hr"},
        "chat": {"id": CHAT_ID, "type": "private"},
        "date": 1761600000,
    }
    if voice:
        msg["voice"] = {
            "file_id": f"AwAC-{message_id}",
            "duration": 10,
            "mime_type": "audio/ogg",
            "file_size": 5000,
        }
    if photo:
        msg["photo"] = [
            {"file_id": f"PHOTO-{message_id}-small", "width": 320, "height": 320},
            {"file_id": f"PHOTO-{message_id}-big", "width": 1280, "height": 1280},
        ]
    if text:
        msg["text"] = text

    payload = {"update_id": update_id, "message": msg}
    async with httpx.AsyncClient() as client:
        resp = await client.post(WEBHOOK_URL, json=payload, headers=headers)
    assert resp.status_code == 200, resp.text


async def drain_queue() -> None:
    queue = default_queue()
    while queue.count:
        for job in list(queue.jobs):
            fn = job.func_name
            if fn.endswith("process_telegram_voice_message"):
                await tasks._process_telegram_voice_message(uuid.UUID(job.args[0]))
            elif fn.endswith("process_photo_message"):
                provider = job.args[1] if len(job.args) > 1 else "twilio"
                await tasks._process_photo_message(uuid.UUID(job.args[0]), provider=provider)
            elif fn.endswith("process_text_message"):
                provider = job.args[1] if len(job.args) > 1 else "twilio"
                await tasks._process_text_message(uuid.UUID(job.args[0]), provider=provider)
            elif fn.endswith("generate_bautagebuch"):
                await tasks._generate_bautagebuch(uuid.UUID(job.args[0]))
            else:
                print(f"  unknown job: {fn}")
            job.delete()


async def main() -> None:
    configure_logging()
    await seed_db()
    storage_root = install_mocks()

    step("Step 2: 2 voice + 1 photo + 1 Feierabend")
    await post_telegram(9001, 201, voice=True)
    await post_telegram(9002, 202, voice=True)
    await post_telegram(9003, 203, photo=True)
    await post_telegram(9004, 204, voice=True)  # this one is the Feierabend

    step("Step 3: drain queue (voice + photo + generate_bautagebuch)")
    await drain_queue()

    step("Step 4: verify Report + PDF")
    async with session_scope() as db:
        report = await db.scalar(
            select(Report).join(ReportSession).where(ReportSession.user_id == USER_ID)
        )
        assert report is not None, "no Report row created"
        assert report.pdf_path, "report has no pdf_path"
        pdf_path = Path(report.pdf_path)
        assert pdf_path.exists(), f"PDF missing at {pdf_path}"
        assert pdf_path.read_bytes().startswith(b"%PDF-")
        print(f"  report id={report.id} pdf={pdf_path.name} size={pdf_path.stat().st_size}")

        sent: list[dict] = getattr(tasks, "_E2E_SENT", [])
        docs = [s for s in sent if s["kind"] == "document"]
        texts = [s for s in sent if s["kind"] == "text"]
        print(f"  docs sent: {len(docs)}, text replies: {len(texts)}")
        assert len(docs) == 1
        assert docs[0]["caption"] and "Bautagebuch fertig" in docs[0]["caption"]

    step("Step 5: user replies '✅' (confirmation)")
    await post_telegram(9005, 205, text="✅")
    await drain_queue()

    step("Step 6: verify finalized")
    async with session_scope() as db:
        report = await db.scalar(
            select(Report).join(ReportSession).where(ReportSession.user_id == USER_ID)
        )
        assert report is not None
        assert report.confirmed_at is not None, "report not confirmed"

        sessions = (
            await db.execute(select(ReportSession).where(ReportSession.user_id == USER_ID))
        ).scalars().all()
        assert sessions, "no session"
        assert sessions[0].status == SessionStatus.finalized, sessions[0].status
        assert sessions[0].finalized_at is not None
        print(f"  report.confirmed_at={report.confirmed_at}")
        print(f"  session.status={sessions[0].status.value}")

        sent: list[dict] = getattr(tasks, "_E2E_SENT", [])
        confirm_replies = [s for s in sent if s["kind"] == "text" and "bestätigt" in s["body"]]
        assert confirm_replies, "no confirmation reply sent"
        print(f"  confirmation reply: {confirm_replies[-1]['body']}")

    step("ALL CHECKS PASSED (Phase 4)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        sys.exit(1)
