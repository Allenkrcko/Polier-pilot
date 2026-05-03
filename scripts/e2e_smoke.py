"""End-to-end smoke test for Phase 2.

Drives the full path locally without hitting Twilio or OpenAI:

  1. POST a fake voice message to /webhooks/twilio (real FastAPI app)
  2. Verify the Message row landed in Postgres
  3. Verify the worker task was enqueued in Redis
  4. Monkey-patch download / transcribe / send_text and run the
     worker task body directly to simulate the worker
  5. Verify the transcript + language + reply made it back

Run with:
    .venv/bin/python -m scripts.e2e_smoke
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from app.db.models import Message
from app.db.session import session_scope
from app.logging_config import configure_logging
from app.services import storage, transcription, whatsapp
from app.services.storage import StoredMedia
from app.services.transcription import TranscriptionResult
from app.services.whatsapp import SentMessage
from app.workers import tasks
from app.workers.queue import default_queue, redis_conn

WEBHOOK_URL = "http://127.0.0.1:8000/webhooks/twilio"
SAMPLE_SID = f"SMe2e{uuid.uuid4().hex[:12]}"
FROM_NUMBER = "whatsapp:+38761555000"

logger = logging.getLogger("e2e_smoke")


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


async def post_webhook() -> None:
    step("Step 1: POST voice webhook")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            WEBHOOK_URL,
            data={
                "MessageSid": SAMPLE_SID,
                "AccountSid": "ACtest",
                "From": FROM_NUMBER,
                "To": "whatsapp:+14155238886",
                "NumMedia": "1",
                "MediaUrl0": "https://api.twilio.com/2010-04-01/fake/media/voice.ogg",
                "MediaContentType0": "audio/ogg",
                "ProfileName": "Mirzo Polier",
                "WaId": "38761555000",
            },
        )
    assert resp.status_code == 200, resp.text
    print(f"webhook returned {resp.status_code}, body={resp.text!r}")


async def verify_persisted() -> uuid.UUID:
    step("Step 2: verify Message row + initial state")
    async with session_scope() as db:
        msg = await db.scalar(select(Message).where(Message.twilio_sid == SAMPLE_SID))
        assert msg is not None, "Message row not found"
        assert msg.type.value == "voice"
        assert msg.processed is False
        assert msg.transcript is None
        print(f"id={msg.id} type={msg.type.value} processed={msg.processed}")
        return msg.id


def verify_enqueued() -> None:
    step("Step 3: verify worker job enqueued in Redis")
    queue = default_queue()
    depth = queue.count
    print(f"queue '{queue.name}' depth={depth}")
    assert depth >= 1, "Expected at least one job enqueued"
    job = queue.jobs[0]
    print(f"job id={job.id} func={job.func_name} args={job.args}")
    assert job.func_name.endswith("process_voice_message")


def install_mocks() -> None:
    step("Step 4: install service mocks for the worker run")

    fake_audio = Path("/tmp/polier-test-storage/e2e-fake.ogg")
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"OggS" + b"\x00" * 1024)

    async def fake_download(media_url, twilio_sid, *, expected_content_type=None, client=None):
        return StoredMedia(path=fake_audio, content_type="audio/ogg", size_bytes=fake_audio.stat().st_size)

    async def fake_transcribe(audio_path, *, language_hint=None):
        return TranscriptionResult(
            text="Danas smo iskopali 80 metara rova kod broja 23 i postavili kabel.",
            language="hr",
            duration_seconds=12.7,
            segments=[],
        )

    sent_log: list[dict] = []

    async def fake_send_text(to, body, *, media_urls=None, client=None):
        sent_log.append({"to": to, "body": body})
        return SentMessage(sid="SMfake", status="queued", to=to)

    # Phase 3 added a classifier call inside the voice pipeline. Stub it so
    # the smoke runs without a real Anthropic key.
    async def fake_classify(text, *, detected_language=None, client=None):
        from app.services.classifier import ClassificationResult
        return ClassificationResult(
            intent="bautagebuch_entry",
            confidence=0.9,
            language=detected_language or "hr",
            summary="Heute wurden 80 m FTTH-Kabel verlegt.",
            mentions={},
        )

    # Patch the names that worker.tasks resolved at import time.
    tasks.download_twilio_media = fake_download  # type: ignore[assignment]
    tasks.transcribe = fake_transcribe  # type: ignore[assignment]
    tasks.send_text = fake_send_text  # type: ignore[assignment]
    tasks.classify = fake_classify  # type: ignore[assignment]
    tasks._E2E_SENT_LOG = sent_log  # type: ignore[attr-defined]
    print("download / transcribe / send_text / classify -> stubbed")


async def run_worker_task(message_id: uuid.UUID) -> None:
    step("Step 5: run worker task body inline")
    await tasks._process_voice_message(message_id)
    sent: list[dict] = getattr(tasks, "_E2E_SENT_LOG", [])
    print(f"send_text invocations: {len(sent)}")
    for item in sent:
        print(f"  -> {item}")
    assert len(sent) == 1
    assert "Verstanden" in sent[0]["body"]
    assert "Hrvatski" in sent[0]["body"]


async def verify_final_state(message_id: uuid.UUID) -> None:
    step("Step 6: verify DB after processing")
    async with session_scope() as db:
        msg = await db.get(Message, message_id)
        assert msg is not None
        print(f"processed={msg.processed} lang={msg.detected_language}")
        print(f"transcript={msg.transcript[:80]!r}")
        print(f"payload.transcription={json.dumps(msg.payload.get('transcription'), indent=2)}")
        assert msg.processed is True
        assert msg.detected_language == "hr"
        assert "iskopali" in (msg.transcript or "")
        assert msg.processed_at is not None


async def main() -> None:
    configure_logging()
    await post_webhook()
    message_id = await verify_persisted()
    verify_enqueued()
    install_mocks()
    await run_worker_task(message_id)
    await verify_final_state(message_id)
    step("ALL CHECKS PASSED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        sys.exit(1)
