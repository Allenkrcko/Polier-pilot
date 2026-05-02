"""Intent classifier using Claude Haiku via the Anthropic SDK.

Forces structured output by binding a single tool (`record_classification`)
and using `tool_choice` so Claude is required to invoke it. The tool's
`input_schema` is the contract for our ClassificationResult.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from anthropic import AsyncAnthropic

from app.config import settings
from app.prompts import load as load_prompt

logger = logging.getLogger(__name__)


Intent = Literal[
    "bautagebuch_entry",
    "aufmass",
    "maengel",
    "behinderung",
    "feierabend",
    "question",
    "smalltalk",
]

_VALID_INTENTS: frozenset[str] = frozenset(
    [
        "bautagebuch_entry",
        "aufmass",
        "maengel",
        "behinderung",
        "feierabend",
        "question",
        "smalltalk",
    ]
)

_TOOL_NAME: Final = "record_classification"

_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": list(_VALID_INTENTS),
            "description": "The single best-matching intent.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Self-reported confidence, 0..1.",
        },
        "language": {
            "type": "string",
            "description": "ISO language code of the input (de, hr, bs, sr, pl, ro, tr, ...).",
        },
        "summary": {
            "type": "string",
            "description": "One-sentence formal German summary, max 25 words.",
        },
        "mentions": {
            "type": "object",
            "properties": {
                "project": {"type": ["string", "null"]},
                "location": {"type": ["string", "null"]},
                "persons": {"type": "array", "items": {"type": "string"}},
                "equipment": {"type": "array", "items": {"type": "string"}},
                "materials": {"type": "array", "items": {"type": "string"}},
                "quantities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "wert": {"type": "number"},
                            "einheit": {"type": "string"},
                            "was": {"type": "string"},
                        },
                        "required": ["wert", "einheit", "was"],
                    },
                },
                "problems": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["intent", "confidence", "language", "summary"],
}

_client: AsyncAnthropic | None = None


def _anthropic_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    return _client


@dataclass(frozen=True)
class ClassificationResult:
    intent: Intent
    confidence: float
    language: str
    summary: str
    mentions: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def _coerce_intent(value: Any) -> Intent:
    if isinstance(value, str) and value in _VALID_INTENTS:
        return value  # type: ignore[return-value]
    logger.warning("Unknown intent %r from classifier; falling back to smalltalk", value)
    return "smalltalk"


async def classify(
    text: str,
    *,
    detected_language: str | None = None,
    client: AsyncAnthropic | None = None,
) -> ClassificationResult:
    """Classify a transcript / text snippet via Haiku.

    Empty / very short input short-circuits to smalltalk to save a call.
    Tool invocation is forced via `tool_choice` so we never have to parse
    free-form text - we read the dict directly from the tool_use block.
    """
    text = (text or "").strip()
    if len(text) < 2:
        return ClassificationResult(
            intent="smalltalk",
            confidence=1.0,
            language=detected_language or "und",
            summary="Keine inhaltliche Aussage erkannt.",
            mentions={},
        )

    cli = client or _anthropic_client()
    system_prompt = load_prompt("classifier")
    user_message = (
        f"Sprache (optional): {detected_language or 'auto'}\n\n"
        f"Transkript:\n{text}"
    )

    response = await cli.messages.create(
        model=settings.anthropic_model_classification,
        max_tokens=600,
        system=system_prompt,
        tools=[
            {
                "name": _TOOL_NAME,
                "description": "Strukturierte Aufzeichnung der Klassifikation.",
                "input_schema": _TOOL_INPUT_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": user_message}],
    )

    tool_input: dict[str, Any] | None = None
    for block in getattr(response, "content", []) or []:
        # Anthropic SDK exposes either pydantic blocks or dict-like items.
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type == "tool_use":
            tool_input = (
                getattr(block, "input", None)
                if not isinstance(block, dict)
                else block.get("input")
            )
            break

    if not tool_input:
        logger.warning("Classifier returned no tool_use block; defaulting to smalltalk")
        tool_input = {
            "intent": "smalltalk",
            "confidence": 0.0,
            "language": detected_language or "und",
            "summary": "Klassifikation fehlgeschlagen.",
            "mentions": {},
        }

    return ClassificationResult(
        intent=_coerce_intent(tool_input.get("intent")),
        confidence=float(tool_input.get("confidence") or 0.0),
        language=str(tool_input.get("language") or detected_language or "und"),
        summary=str(tool_input.get("summary") or ""),
        mentions=dict(tool_input.get("mentions") or {}),
        raw=json.loads(json.dumps(tool_input, default=str)),
    )
