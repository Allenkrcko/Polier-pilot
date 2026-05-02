"""Tests for app.services.transcription with the OpenAI client mocked."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services import transcription


class _FakeTranscriptions:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def create(self, **kwargs: Any) -> Any:
        class Resp:
            def __init__(self, data: dict[str, Any]) -> None:
                self._data = data

            def model_dump(self) -> dict[str, Any]:
                return dict(self._data)

        return Resp(self._payload)


class _FakeAudio:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.transcriptions = _FakeTranscriptions(payload)


class _FakeOpenAIClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.audio = _FakeAudio(payload)


@pytest.mark.asyncio
async def test_transcribe_small_file_returns_parsed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"OggS" + b"\x00" * 32)

    fake_payload = {
        "text": "Heute haben wir 80 Meter Graben ausgehoben.",
        "language": "de",
        "duration": 5.4,
        "segments": [{"start": 0.0, "end": 5.4, "text": "..."}],
    }

    monkeypatch.setattr(
        transcription, "_openai_client", lambda: _FakeOpenAIClient(fake_payload)
    )

    result = await transcription.transcribe(audio)

    assert result.text == "Heute haben wir 80 Meter Graben ausgehoben."
    assert result.language == "de"
    assert result.duration_seconds == pytest.approx(5.4)
    assert len(result.segments) == 1


@pytest.mark.asyncio
async def test_transcribe_handles_missing_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "empty.ogg"
    audio.write_bytes(b"\x00" * 16)

    monkeypatch.setattr(
        transcription, "_openai_client", lambda: _FakeOpenAIClient({})
    )

    result = await transcription.transcribe(audio, language_hint="hr")
    assert result.text == ""
    assert result.language == "hr"
    assert result.duration_seconds == 0.0
    assert result.segments == []
