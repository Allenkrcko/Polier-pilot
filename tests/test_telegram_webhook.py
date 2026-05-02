"""Tests for the Telegram webhook helpers (no DB)."""
from __future__ import annotations

from app.api import webhooks_telegram
from app.db.models import MessageType


def test_classify_voice() -> None:
    msg = {"voice": {"file_id": "AwACvoice", "mime_type": "audio/ogg"}}
    msg_type, file_id, mime = webhooks_telegram._classify(msg)
    assert msg_type == MessageType.voice
    assert file_id == "AwACvoice"
    assert mime == "audio/ogg"


def test_classify_audio_file_treated_as_voice() -> None:
    msg = {"audio": {"file_id": "audio1", "mime_type": "audio/mpeg"}}
    msg_type, file_id, mime = webhooks_telegram._classify(msg)
    assert msg_type == MessageType.voice
    assert mime == "audio/mpeg"


def test_classify_photo_picks_largest() -> None:
    msg = {
        "photo": [
            {"file_id": "small", "width": 90, "height": 90},
            {"file_id": "medium", "width": 320, "height": 320},
            {"file_id": "large", "width": 1280, "height": 1280},
        ]
    }
    msg_type, file_id, mime = webhooks_telegram._classify(msg)
    assert msg_type == MessageType.photo
    assert file_id == "large"
    assert mime == "image/jpeg"


def test_classify_video() -> None:
    msg = {"video": {"file_id": "vid1"}}
    msg_type, file_id, _ = webhooks_telegram._classify(msg)
    assert msg_type == MessageType.video
    assert file_id == "vid1"


def test_classify_document() -> None:
    msg = {"document": {"file_id": "doc1", "mime_type": "application/pdf"}}
    msg_type, file_id, mime = webhooks_telegram._classify(msg)
    assert msg_type == MessageType.document
    assert file_id == "doc1"
    assert mime == "application/pdf"


def test_classify_text_only() -> None:
    msg = {"text": "hello"}
    msg_type, file_id, mime = webhooks_telegram._classify(msg)
    assert msg_type == MessageType.text
    assert file_id is None
    assert mime is None
