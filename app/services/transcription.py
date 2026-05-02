"""OpenAI Whisper transcription wrapper.

Sends audio to `whisper-1` with `response_format="verbose_json"` so we get
the detected language plus segment timings. Audio above the 25 MB Whisper
limit is split into roughly equal-duration chunks with pydub (requires
ffmpeg) and the per-chunk transcripts are stitched back together.
"""
from __future__ import annotations

import logging
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydub import AudioSegment

from app.config import settings

logger = logging.getLogger(__name__)

# Whisper rejects uploads >25 MB. We aim a hair under to leave headroom for re-encoding.
_MAX_UPLOAD_BYTES: int = 24 * 1024 * 1024
_CHUNK_EXPORT_FORMAT: str = "mp3"  # universally accepted, smaller than ogg for speech
_CHUNK_EXPORT_BITRATE: str = "64k"


@dataclass(frozen=True)
class TranscriptionResult:
    """Output of a transcription run."""

    text: str
    language: str
    duration_seconds: float
    segments: list[dict[str, Any]] = field(default_factory=list)


_client: AsyncOpenAI | None = None


def _openai_client() -> AsyncOpenAI:
    """Lazy singleton; constructed once per process."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    return _client


async def _whisper_call(
    audio_path: Path,
    *,
    language_hint: str | None,
) -> dict[str, Any]:
    client = _openai_client()
    with audio_path.open("rb") as fh:
        response = await client.audio.transcriptions.create(
            model=settings.openai_whisper_model,
            file=fh,
            response_format="verbose_json",
            language=language_hint,  # None lets Whisper auto-detect
        )
    # The SDK returns a pydantic-like object; use model_dump where available.
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return dict(response)  # type: ignore[arg-type]


def _split_audio(audio: AudioSegment, max_chunk_ms: int) -> list[AudioSegment]:
    """Slice audio into <= max_chunk_ms chunks, preserving total duration."""
    if len(audio) <= max_chunk_ms:
        return [audio]
    chunk_count = math.ceil(len(audio) / max_chunk_ms)
    actual_chunk_ms = math.ceil(len(audio) / chunk_count)
    return [audio[i : i + actual_chunk_ms] for i in range(0, len(audio), actual_chunk_ms)]


async def transcribe(
    audio_path: Path,
    *,
    language_hint: str | None = None,
) -> TranscriptionResult:
    """Transcribe an audio file. Chunks transparently if it is over 25 MB."""
    audio_path = Path(audio_path)
    size = audio_path.stat().st_size

    if size <= _MAX_UPLOAD_BYTES:
        raw = await _whisper_call(audio_path, language_hint=language_hint)
        return TranscriptionResult(
            text=(raw.get("text") or "").strip(),
            language=raw.get("language") or (language_hint or "und"),
            duration_seconds=float(raw.get("duration") or 0.0),
            segments=list(raw.get("segments") or []),
        )

    logger.info("Audio %s is %d bytes; chunking for Whisper", audio_path, size)
    audio = AudioSegment.from_file(audio_path)
    # Estimate max ms per chunk: scale by the file's actual byte/ms ratio.
    bytes_per_ms = max(size / max(len(audio), 1), 1.0)
    max_chunk_ms = int(_MAX_UPLOAD_BYTES / bytes_per_ms * 0.9)  # 10% safety margin
    chunks = _split_audio(audio, max_chunk_ms)

    parts: list[str] = []
    languages: list[str] = []
    total_duration: float = 0.0
    all_segments: list[dict[str, Any]] = []
    offset_seconds: float = 0.0

    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, chunk in enumerate(chunks):
            chunk_path = Path(tmpdir) / f"chunk_{idx:03d}.{_CHUNK_EXPORT_FORMAT}"
            chunk.export(chunk_path, format=_CHUNK_EXPORT_FORMAT, bitrate=_CHUNK_EXPORT_BITRATE)
            raw = await _whisper_call(chunk_path, language_hint=language_hint)
            parts.append((raw.get("text") or "").strip())
            languages.append(raw.get("language") or "und")
            chunk_duration = float(raw.get("duration") or 0.0)
            for seg in raw.get("segments") or []:
                seg = dict(seg)
                seg["start"] = float(seg.get("start", 0.0)) + offset_seconds
                seg["end"] = float(seg.get("end", 0.0)) + offset_seconds
                all_segments.append(seg)
            offset_seconds += chunk_duration
            total_duration += chunk_duration

    # Pick the most common language across chunks; ties break by first occurrence.
    primary_language = max(set(languages), key=languages.count) if languages else "und"

    return TranscriptionResult(
        text=" ".join(p for p in parts if p),
        language=primary_language,
        duration_seconds=total_duration,
        segments=all_segments,
    )
