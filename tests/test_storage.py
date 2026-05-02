"""Tests for app.services.storage."""
from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from app.services import storage


def test_extension_for_known_audio_types() -> None:
    assert storage._extension_for("audio/ogg") == ".ogg"
    assert storage._extension_for("audio/mpeg") == ".mp3"
    assert storage._extension_for("audio/m4a") == ".m4a"


def test_extension_for_image_and_unknown() -> None:
    assert storage._extension_for("image/jpeg") == ".jpg"
    assert storage._extension_for(None) == ".bin"


@pytest.mark.asyncio
async def test_download_twilio_media_writes_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """The downloader writes the response body to STORAGE_LOCAL_PATH."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(storage.settings, "storage_local_path", Path(tmpdir))

        fake_audio = b"OggS" + b"\x00" * 64

        def transport_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.startswith("/Accounts/")
            assert "Authorization" in request.headers
            return httpx.Response(
                200, content=fake_audio, headers={"content-type": "audio/ogg"}
            )

        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            stored = await storage.download_twilio_media(
                "https://api.twilio.com/Accounts/AC/Messages/MM/Media/ME-test",
                twilio_sid="MMtest123",
                client=client,
            )

        assert stored.path.exists()
        assert stored.path.read_bytes() == fake_audio
        assert stored.size_bytes == len(fake_audio)
        assert stored.path.suffix == ".ogg"
