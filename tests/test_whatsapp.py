"""Tests for app.services.whatsapp."""
from __future__ import annotations

import httpx
import pytest

from app.services import whatsapp


def test_normalize_to_adds_prefix() -> None:
    assert whatsapp._normalize_to("+38761123456") == "whatsapp:+38761123456"
    assert whatsapp._normalize_to("whatsapp:+38761123456") == "whatsapp:+38761123456"


@pytest.mark.asyncio
async def test_send_text_posts_to_messages_endpoint() -> None:
    seen_payload: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.twilio.com"
        assert request.url.path.endswith("/Messages.json")
        seen_payload.update(dict(httpx.QueryParams(request.content.decode())))
        body = {"sid": "SMmocked", "status": "queued"}
        return httpx.Response(201, json=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        sent = await whatsapp.send_text(
            "+38761123456", "Hallo Polier", client=client
        )

    assert sent.sid == "SMmocked"
    assert seen_payload["To"] == "whatsapp:+38761123456"
    assert seen_payload["Body"] == "Hallo Polier"
    assert seen_payload["From"].startswith("whatsapp:")


@pytest.mark.asyncio
async def test_send_text_truncates_long_body() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(201, json={"sid": "SMx", "status": "queued"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await whatsapp.send_text("+1234567890", "x" * 5000, client=client)

    assert len(captured["Body"]) == whatsapp._MAX_BODY_LEN


@pytest.mark.asyncio
async def test_send_text_4xx_raises_without_retry() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"code": 21211, "message": "bad number"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(whatsapp.TwilioSendError) as exc_info:
            await whatsapp.send_text("+0", "hi", client=client)
    assert exc_info.value.status_code == 400
    assert call_count == 1  # no retry on 4xx
