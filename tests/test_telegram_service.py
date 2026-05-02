"""Tests for app.services.telegram with the Bot API mocked."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
import pytest

from app.services import telegram


@pytest.mark.asyncio
async def test_get_file_path_returns_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/getFile" in request.url.path
        assert request.url.params.get("file_id") == "AwACtest"
        return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/file_42.oga"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        path = await telegram.get_file_path("AwACtest", client=client)
    assert path == "voice/file_42.oga"


@pytest.mark.asyncio
async def test_download_telegram_media_writes_file(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(telegram.settings, "storage_local_path", Path(tmpdir))

        fake_audio = b"OggS" + b"\x00" * 128

        def handler(request: httpx.Request) -> httpx.Response:
            if "/getFile" in request.url.path:
                return httpx.Response(
                    200, json={"ok": True, "result": {"file_path": "voice/file_42.oga"}}
                )
            assert "/file/bot" in request.url.path
            assert request.url.path.endswith("/voice/file_42.oga")
            return httpx.Response(
                200, content=fake_audio, headers={"content-type": "audio/ogg"}
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            stored = await telegram.download_telegram_media("AwACtest", client=client)

        assert stored.path.exists()
        assert stored.path.read_bytes() == fake_audio
        assert stored.content_type == "audio/ogg"
        assert stored.path.suffix == ".oga"


@pytest.mark.asyncio
async def test_send_text_posts_send_message() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/sendMessage" in request.url.path
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"message_id": 123, "chat": {"id": 555111222}, "text": captured["text"]},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        sent = await telegram.send_text(555111222, "Hallo Polier", client=client)

    assert sent.message_id == 123
    assert sent.chat_id == 555111222
    assert captured["chat_id"] == 555111222
    assert captured["text"] == "Hallo Polier"


@pytest.mark.asyncio
async def test_send_text_truncates_long_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1, "chat": {"id": 1}, "text": captured["text"]}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await telegram.send_text(1, "x" * 10000, client=client)

    assert len(captured["text"]) == telegram._MAX_BODY_LEN


@pytest.mark.asyncio
async def test_send_text_4xx_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "error_code": 403, "description": "blocked"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(telegram.TelegramError) as exc_info:
            await telegram.send_text(1, "hi", client=client)
    assert exc_info.value.status_code == 403
