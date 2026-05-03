"""Tests for app.services.report_generator (Sonnet mocked)."""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.services import report_generator


class _Block:
    def __init__(self, type: str, input: dict[str, Any] | None = None) -> None:
        self.type = type
        self.input = input or {}


class _Resp:
    def __init__(self, blocks: list[_Block]) -> None:
        self.content = blocks


class _Messages:
    def __init__(self, blocks: list[_Block]) -> None:
        self._blocks = blocks
        self.last_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> _Resp:
        self.last_kwargs = kwargs
        return _Resp(self._blocks)


class _FakeAnthropic:
    def __init__(self, blocks: list[_Block]) -> None:
        self.messages = _Messages(blocks)


def _sample_context() -> dict[str, Any]:
    return {
        "session": {
            "id": "ssn-1",
            "work_date": "2026-05-02",
            "status": "pending_review",
        },
        "project": {
            "id": "p-1",
            "name": "FTTH Bayreuth Los 3",
            "auftraggeber": "Stadtwerke Bayreuth",
            "project_number": "2026-FTTH-003",
            "address": "Bayreuth",
        },
        "user": {"id": "u-1", "name": "Mirzo Polier", "language_pref": "bs"},
        "weather": {
            "summary_de": "Leichter Regen, 8–17 °C, 2.4 mm Niederschlag",
            "temperature_max_c": 17.2,
            "temperature_min_c": 8.4,
        },
        "photo_ids": ["ph1", "ph2"],
        "messages": [],
    }


def test_parse_fills_defaults_from_context() -> None:
    ctx = _sample_context()
    bd = report_generator._parse(
        {
            "datum": "2026-05-02",
            "baustelle": "FTTH Bayreuth Los 3",
            "ausgefuehrte_arbeiten": [
                {"position": "FTTH-Kabel verlegen", "menge": 80, "einheit": "m"}
            ],
        },
        context=ctx,
    )
    assert bd.datum == date(2026, 5, 2)
    assert bd.baustelle == "FTTH Bayreuth Los 3"
    assert bd.projekt_nr == "2026-FTTH-003"  # from context
    assert bd.wetter == "Leichter Regen, 8–17 °C, 2.4 mm Niederschlag"
    assert bd.polier_name == "Mirzo Polier"
    assert bd.fotos_referenz == ["ph1", "ph2"]
    assert bd.ausgefuehrte_arbeiten[0]["position"] == "FTTH-Kabel verlegen"


def test_parse_warns_when_explicit_warnungen_present() -> None:
    ctx = _sample_context()
    bd = report_generator._parse(
        {
            "datum": "2026-05-02",
            "baustelle": "FTTH Bayreuth Los 3",
            "_warnungen": ["Arbeitszeit nicht erfasst", "Wetter unbekannt"],
        },
        context=ctx,
    )
    assert "Arbeitszeit nicht erfasst" in bd.warnungen
    assert "Wetter unbekannt" in bd.warnungen


def test_as_dict_round_trips() -> None:
    ctx = _sample_context()
    bd = report_generator._parse(
        {"datum": "2026-05-02", "baustelle": "X", "stunden_gesamt": 8.5},
        context=ctx,
    )
    d = bd.as_dict()
    assert d["datum"] == "2026-05-02"
    assert d["stunden_gesamt"] == 8.5
    assert isinstance(d["arbeitszeit"], dict)
    assert isinstance(d["ausgefuehrte_arbeiten"], list)


@pytest.mark.asyncio
async def test_generate_for_session_calls_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: mocked context loader + mocked Anthropic returns BautagebuchData."""
    ctx = _sample_context()

    async def fake_loader(session_id):  # type: ignore[no-untyped-def]
        return ctx

    monkeypatch.setattr(report_generator, "_load_session_context", fake_loader)

    fake = _FakeAnthropic(
        [
            _Block(
                "tool_use",
                input={
                    "datum": "2026-05-02",
                    "baustelle": "FTTH Bayreuth Los 3",
                    "wetter": "Leichter Regen",
                    "temperatur_celsius": 12.5,
                    "ausgefuehrte_arbeiten": [
                        {"position": "Aushub", "menge": 80, "einheit": "m"}
                    ],
                    "fotos_referenz": ["ph1"],
                    "stunden_gesamt": 9.0,
                },
            )
        ]
    )

    import uuid as _uuid

    bd = await report_generator.generate_for_session(_uuid.uuid4(), client=fake)  # type: ignore[arg-type]
    assert bd.baustelle == "FTTH Bayreuth Los 3"
    assert bd.temperatur_celsius == pytest.approx(12.5)
    assert bd.ausgefuehrte_arbeiten[0]["menge"] == 80
    # Forced tool use
    assert fake.messages.last_kwargs["tool_choice"]["name"] == "record_bautagebuch"
