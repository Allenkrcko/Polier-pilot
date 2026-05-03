"""End-to-end smoke test for the Telegram path (Phase 2T).

Drives the full inbound voice flow without contacting Telegram or OpenAI.

  1. POST a fake Telegram Update to /webhooks/telegram
  2. Verify the Message row landed
  3. Verify the worker job is enqueued in Redis
  4. Monkey-patch download_telegram_media / transcribe / telegram.send_text
  5. Run the worker task body inline
  6. Verify the transcript landed in the row + the reply was sent

Run with the FastAPI app already running on :8000:
    .venv/bin/python -m scripts.e2e_smoke_telegram
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import settings
from app.db.models import Message
from app.db.session import session_scope
from app.logging_config import configure_logging
from app.services.telegram import SentMessage, StoredMedia
from app.services.transcription import TranscriptionResult
from app.workers import tasks
from app.workers.queue import default_queue

WEBHOOK_URL = "http://127.0.0.1:8000/webhooks/telegram"
UPDATE_ID = 9_000_000 + uuid.uuid4().int % 1_000
TG_MESSAGE_ID = 42 + uuid.uuid4().int % 100
CHAT_ID = 555_111_222
SAMPLE_FILE_ID = "AwACAgEAAxkBAAIBfakeID9876543210"


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


async def post_update() -> None:
    step("Step 1: POST Telegram voice update")
    secret = (
        settings.telegram_webhook_secret.get_secret_value()
        if settings.telegram_webhook_secret
        else None
    )
    headers = {}
    if secret:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret

    payload = {
        "update_id": UPDATE_ID,
        "message": {
            "message_id": TG_MESSAGE_ID,
            "from": {
                "id": CHAT_ID,
                "is_bot": False,
                "first_name": "Mirzo",
                "username": "mirzopolier",
                "language_code": "hr",
            },
            "chat": {"id": CHAT_ID, "type": "private", "first_name": "Mirzo"},
            "date": 1761600000,
            "voice": {
                "file_id": SAMPLE_FILE_ID,
                "file_unique_id": "AgADfakeUnique",
                "duration": 13,
                "mime_type": "audio/ogg",
                "file_size": 21456,
            },
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(WEBHOOK_URL, json=payload, headers=headers)
    print(f"webhook returned {resp.status_code}, body={resp.text!r}")
    assert resp.status_code == 200, resp.text


async def verify_persisted() -> uuid.UUID:
    step("Step 2: verify Message row")
    expected_sid = f"tg-{UPDATE_ID}-{TG_MESSAGE_ID}"
    async with session_scope() as db:
        msg = await db.scalar(select(Message).where(Message.twilio_sid == expected_sid))
        assert msg is not None, "Message row not found"
        assert msg.type.value == "voice"
        assert msg.media_url == SAMPLE_FILE_ID
        assert msg.media_content_type == "audio/ogg"
        assert msg.payload["provider"] == "telegram"
        print(f"id={msg.id} type={msg.type.value} provider=telegram file_id={msg.media_url}")
        return msg.id


def verify_enqueued() -> None:
    step("Step 3: verify worker job enqueued")
    queue = default_queue()
    print(f"queue '{queue.name}' depth={queue.count}")
    assert queue.count >= 1
    job = queue.jobs[0]
    assert job.func_name.endswith("process_telegram_voice_message")
    print(f"job func={job.func_name} args={job.args}")


def install_mocks() -> None:
    step("Step 4: install mocks for download / transcribe / send_text")

    fake_audio = Path("/tmp/polier-test-storage/telegram-fake.ogg")
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"OggS" + b"\x00" * 256)

    async def fake_download(file_id, *, expected_content_type=None, client=None):
        return StoredMedia(path=fake_audio, content_type="audio/ogg", size_bytes=fake_audio.stat().st_size)

    async def fake_transcribe(audio_path, *, language_hint=None):
        return TranscriptionResult(
            text="Danas smo postavili 120 metara FTTH kabla u Birkenstrasse i pokrili rov.",
            language="hr",
            duration_seconds=14.2,
            segments=[],
        )

    sent_log: list[dict] = []

    async def fake_send_text(chat_id, body, *, client=None):
        sent_log.append({"chat_id": chat_id, "body": body})
        return SentMessage(message_id=999, chat_id=chat_id, text=body)

    async def fake_classify(text, *, detected_language=None, client=None):
        from app.services.classifier import ClassificationResult
        return ClassificationResult(
            intent="bautagebuch_entry",
            confidence=0.9,
            language=detected_language or "hr",
            summary="Heute wurden 120 m FTTH-Kabel in der Birkenstraße verlegt.",
            mentions={},
        )

    tasks.download_telegram_media = fake_download  # type: ignore[assignment]
    tasks.transcribe = fake_transcribe  # type: ignore[assignment]
    tasks.telegram_send_text = fake_send_text  # type: ignore[assignment]
    tasks.classify = fake_classify  # type: ignore[assignment]
    tasks._E2E_TG_SENT = sent_log  # type: ignore[attr-defined]
    print("download_telegram_media / transcribe / telegram_send_text / classify -> stubbed")


async def run_worker(message_id: uuid.UUID) -> None:
    step("Step 5: run worker task body inline")
    await tasks._process_telegram_voice_message(message_id)
    sent: list[dict] = getattr(tasks, "_E2E_TG_SENT", [])
    print(f"telegram_send_text invocations: {len(sent)}")
    for item in sent:
        print(f"  -> {item}")
    assert len(sent) == 1
    assert sent[0]["chat_id"] == CHAT_ID
    assert "Verstanden" in sent[0]["body"]
    assert "Hrvatski" in sent[0]["body"]


async def verify_final(message_id: uuid.UUID) -> None:
    step("Step 6: verify DB after processing")
    async with session_scope() as db:
        msg = await db.get(Message, message_id)
        assert msg is not None
        print(f"processed={msg.processed} lang={msg.detected_language}")
        print(f"transcript={msg.transcript[:80]!r}")
        print(f"transcription metadata={json.dumps(msg.payload.get('transcription'), indent=2)}")
        assert msg.processed is True
        assert msg.detected_language == "hr"
        assert "FTTH" in (msg.transcript or "")


async def main() -> None:
    configure_logging()
    await post_update()
    message_id = await verify_persisted()
    verify_enqueued()
    install_mocks()
    await run_worker(message_id)
    await verify_final(message_id)
    step("ALL CHECKS PASSED (Telegram path)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        sys.exit(1)
