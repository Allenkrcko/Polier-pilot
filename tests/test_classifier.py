"""Tests for app.services.classifier with the Anthropic SDK mocked."""
from __future__ import annotations

from typing import Any

import pytest

from app.services import classifier


class _ContentBlock:
    """Mimics the pydantic-style tool_use block returned by AsyncAnthropic."""

    def __init__(self, *, type: str, input: dict[str, Any] | None = None) -> None:
        self.type = type
        self.input = input or {}


class _Response:
    def __init__(self, blocks: list[_ContentBlock]) -> None:
        self.content = blocks


class _FakeMessages:
    def __init__(self, blocks: list[_ContentBlock]) -> None:
        self._blocks = blocks
        self.last_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> _Response:
        self.last_kwargs = kwargs
        return _Response(self._blocks)


class _FakeAnthropic:
    def __init__(self, blocks: list[_ContentBlock]) -> None:
        self.messages = _FakeMessages(blocks)


@pytest.mark.asyncio
async def test_classify_short_circuits_empty_input() -> None:
    result = await classifier.classify("")
    assert result.intent == "smalltalk"
    assert result.summary == "Keine inhaltliche Aussage erkannt."


@pytest.mark.asyncio
async def test_classify_extracts_intent_summary_mentions() -> None:
    blocks = [
        _ContentBlock(
            type="tool_use",
            input={
                "intent": "bautagebuch_entry",
                "confidence": 0.92,
                "language": "hr",
                "summary": "Heute wurden 80 m FTTH-Kabel in der Birkenstraße verlegt.",
                "mentions": {
                    "location": "Birkenstraße",
                    "quantities": [{"wert": 80, "einheit": "m", "was": "FTTH-Kabel"}],
                },
            },
        )
    ]
    fake = _FakeAnthropic(blocks)
    result = await classifier.classify(
        "Danas smo postavili 80 metara FTTH kabla u Birkenstrasse.",
        detected_language="hr",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.intent == "bautagebuch_entry"
    assert result.confidence == pytest.approx(0.92)
    assert result.language == "hr"
    assert "Birkenstraße" in result.summary
    assert result.mentions["location"] == "Birkenstraße"
    # Confirm forced tool use was actually requested
    assert fake.messages.last_kwargs["tool_choice"]["name"] == "record_classification"


@pytest.mark.asyncio
async def test_classify_unknown_intent_falls_back_to_smalltalk() -> None:
    blocks = [
        _ContentBlock(
            type="tool_use",
            input={
                "intent": "ALIENS_LANDED",
                "confidence": 0.5,
                "language": "de",
                "summary": "...",
            },
        )
    ]
    fake = _FakeAnthropic(blocks)
    result = await classifier.classify("???", client=fake)  # type: ignore[arg-type]
    assert result.intent == "smalltalk"


@pytest.mark.asyncio
async def test_classify_no_tool_use_block_defaults_to_smalltalk() -> None:
    fake = _FakeAnthropic([_ContentBlock(type="text")])
    result = await classifier.classify("nesto", client=fake)  # type: ignore[arg-type]
    assert result.intent == "smalltalk"
    assert result.confidence == 0.0
