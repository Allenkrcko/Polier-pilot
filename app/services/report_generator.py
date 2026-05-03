"""Bautagebuch generator.

Aggregates a ReportSession's messages + photos into a context block,
asks Claude Sonnet to fill the `record_bautagebuch` tool, and returns
a structured BautagebuchData ready for the HTML/PDF renderer.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import Message, MessageType, Photo, ReportSession
from app.db.session import session_scope
from app.prompts import load as load_prompt

logger = logging.getLogger(__name__)

_TOOL_NAME: Final = "record_bautagebuch"

_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "datum": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "baustelle": {"type": "string"},
        "projekt_nr": {"type": ["string", "null"]},
        "wetter": {"type": ["string", "null"]},
        "temperatur_celsius": {"type": ["number", "null"]},
        "arbeitszeit": {
            "type": "object",
            "properties": {
                "beginn": {"type": ["string", "null"]},
                "ende": {"type": ["string", "null"]},
                "pause_minuten": {"type": ["number", "null"]},
            },
        },
        "anwesende_kolonnen": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "firma": {"type": "string"},
                    "personen": {"type": "number"},
                    "gewerk": {"type": "string"},
                },
                "required": ["firma"],
            },
        },
        "ausgefuehrte_arbeiten": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "position": {"type": "string"},
                    "menge": {"type": ["number", "null"]},
                    "einheit": {"type": ["string", "null"]},
                    "ort": {"type": ["string", "null"]},
                },
                "required": ["position"],
            },
        },
        "geraete_einsatz": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "geraet": {"type": "string"},
                    "stunden": {"type": ["number", "null"]},
                },
                "required": ["geraet"],
            },
        },
        "materiallieferungen": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "material": {"type": "string"},
                    "menge": {"type": ["number", "null"]},
                    "einheit": {"type": ["string", "null"]},
                    "lieferant": {"type": ["string", "null"]},
                },
                "required": ["material"],
            },
        },
        "besondere_vorkommnisse": {"type": "array", "items": {"type": "string"}},
        "behinderungen": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "art": {"type": "string"},
                    "vob_anzeige": {"type": "boolean"},
                    "beschreibung": {"type": "string"},
                },
                "required": ["art"],
            },
        },
        "fotos_referenz": {"type": "array", "items": {"type": "string"}},
        "polier_name": {"type": ["string", "null"]},
        "stunden_gesamt": {"type": ["number", "null"]},
        "_warnungen": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["datum", "baustelle"],
}


_client: AsyncAnthropic | None = None


def _anthropic_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    return _client


@dataclass(frozen=True)
class BautagebuchData:
    """Structured Bautagebuch as filled by Claude Sonnet."""

    datum: date
    baustelle: str
    projekt_nr: str | None
    wetter: str | None
    temperatur_celsius: float | None
    arbeitszeit: dict[str, Any] = field(default_factory=dict)
    anwesende_kolonnen: list[dict[str, Any]] = field(default_factory=list)
    ausgefuehrte_arbeiten: list[dict[str, Any]] = field(default_factory=list)
    geraete_einsatz: list[dict[str, Any]] = field(default_factory=list)
    materiallieferungen: list[dict[str, Any]] = field(default_factory=list)
    besondere_vorkommnisse: list[str] = field(default_factory=list)
    behinderungen: list[dict[str, Any]] = field(default_factory=list)
    fotos_referenz: list[str] = field(default_factory=list)
    polier_name: str | None = None
    stunden_gesamt: float | None = None
    warnungen: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "datum": self.datum.isoformat(),
            "baustelle": self.baustelle,
            "projekt_nr": self.projekt_nr,
            "wetter": self.wetter,
            "temperatur_celsius": self.temperatur_celsius,
            "arbeitszeit": dict(self.arbeitszeit),
            "anwesende_kolonnen": list(self.anwesende_kolonnen),
            "ausgefuehrte_arbeiten": list(self.ausgefuehrte_arbeiten),
            "geraete_einsatz": list(self.geraete_einsatz),
            "materiallieferungen": list(self.materiallieferungen),
            "besondere_vorkommnisse": list(self.besondere_vorkommnisse),
            "behinderungen": list(self.behinderungen),
            "fotos_referenz": list(self.fotos_referenz),
            "polier_name": self.polier_name,
            "stunden_gesamt": self.stunden_gesamt,
            "warnungen": list(self.warnungen),
        }


async def _load_session_context(session_id: uuid.UUID) -> dict[str, Any]:
    """Load session + project + user + messages + photos into a JSON-friendly dict."""
    async with session_scope() as db:
        session = await db.scalar(
            select(ReportSession)
            .where(ReportSession.id == session_id)
            .options(
                selectinload(ReportSession.project),
                selectinload(ReportSession.user),
                selectinload(ReportSession.messages).selectinload(Message.photos),
            )
        )
        if session is None:
            raise ValueError(f"ReportSession {session_id} not found")

        messages_ctx: list[dict[str, Any]] = []
        photo_refs: list[dict[str, Any]] = []
        weather_block: dict[str, Any] | None = None

        for msg in session.messages:
            payload = msg.payload or {}
            if not weather_block and payload.get("weather"):
                weather_block = payload["weather"]

            entry: dict[str, Any] = {
                "id": str(msg.id),
                "type": msg.type.value,
                "received_at": msg.received_at.isoformat(),
                "transcript": msg.transcript,
                "language": msg.detected_language,
                "intent": msg.classified_intent,
                "classification_summary": (payload.get("classification") or {}).get("summary"),
                "mentions": (payload.get("classification") or {}).get("mentions"),
            }
            if msg.type == MessageType.photo:
                exif = payload.get("exif") or {}
                entry["exif"] = {
                    "gps_lat": exif.get("gps_lat"),
                    "gps_lon": exif.get("gps_lon"),
                    "captured_at": exif.get("captured_at"),
                }
                for ph in msg.photos:
                    photo_refs.append({"id": str(ph.id), "message_id": str(msg.id)})
            messages_ctx.append(entry)

        return {
            "session": {
                "id": str(session.id),
                "work_date": session.work_date.isoformat(),
                "status": session.status.value,
            },
            "project": {
                "id": str(session.project.id),
                "name": session.project.name,
                "auftraggeber": session.project.auftraggeber,
                "project_number": session.project.project_number,
                "address": session.project.address,
            },
            "user": {
                "id": str(session.user.id),
                "name": session.user.name,
                "language_pref": session.user.language_pref,
            },
            "weather": weather_block,
            "photo_ids": [pr["id"] for pr in photo_refs],
            "messages": messages_ctx,
        }


async def generate_for_session(
    session_id: uuid.UUID,
    *,
    client: AsyncAnthropic | None = None,
) -> BautagebuchData:
    """Generate a Bautagebuch for an existing ReportSession."""
    context = await _load_session_context(session_id)
    cli = client or _anthropic_client()

    system_prompt = load_prompt("bautagebuch")
    user_message = (
        "Hier ist der Kontext für den Bautagebuch-Eintrag. Generiere die "
        "strukturierte Aufzeichnung durch Aufruf des Tools `record_bautagebuch`.\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```"
    )

    response = await cli.messages.create(
        model=settings.anthropic_model_generation,
        max_tokens=2000,
        system=system_prompt,
        tools=[
            {
                "name": _TOOL_NAME,
                "description": "Strukturierte Aufzeichnung des Bautagebuchs nach VOB/B §6.",
                "input_schema": _TOOL_INPUT_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": user_message}],
    )

    tool_input: dict[str, Any] | None = None
    for block in getattr(response, "content", []) or []:
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
        raise RuntimeError("Sonnet did not invoke record_bautagebuch")

    return _parse(tool_input, context=context)


def _parse(payload: dict[str, Any], *, context: dict[str, Any]) -> BautagebuchData:
    """Coerce Claude's tool input dict to BautagebuchData with sane fallbacks."""
    raw_date = payload.get("datum") or context["session"]["work_date"]
    parsed_date = date.fromisoformat(raw_date)

    return BautagebuchData(
        datum=parsed_date,
        baustelle=str(payload.get("baustelle") or context["project"]["name"]),
        projekt_nr=payload.get("projekt_nr") or context["project"]["project_number"],
        wetter=payload.get("wetter")
        or (context.get("weather") or {}).get("summary_de"),
        temperatur_celsius=_coerce_float(payload.get("temperatur_celsius")),
        arbeitszeit=dict(payload.get("arbeitszeit") or {}),
        anwesende_kolonnen=list(payload.get("anwesende_kolonnen") or []),
        ausgefuehrte_arbeiten=list(payload.get("ausgefuehrte_arbeiten") or []),
        geraete_einsatz=list(payload.get("geraete_einsatz") or []),
        materiallieferungen=list(payload.get("materiallieferungen") or []),
        besondere_vorkommnisse=list(payload.get("besondere_vorkommnisse") or []),
        behinderungen=list(payload.get("behinderungen") or []),
        fotos_referenz=list(payload.get("fotos_referenz") or context.get("photo_ids", [])),
        polier_name=payload.get("polier_name") or context["user"]["name"],
        stunden_gesamt=_coerce_float(payload.get("stunden_gesamt")),
        warnungen=list(payload.get("_warnungen") or []),
        raw=json.loads(json.dumps(payload, default=str)),
    )


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
