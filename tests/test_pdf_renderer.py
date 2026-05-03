"""Tests for app.services.pdf_renderer (real WeasyPrint, real Jinja)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.services.pdf_renderer import render_bautagebuch
from app.services.report_generator import BautagebuchData


class _Stub:
    def __init__(self, **kw: object) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def _sample_data() -> BautagebuchData:
    return BautagebuchData(
        datum=date(2026, 5, 2),
        baustelle="FTTH Bayreuth Los 3",
        projekt_nr="2026-FTTH-003",
        wetter="Leichter Regen, 8–17 °C",
        temperatur_celsius=12.5,
        arbeitszeit={"beginn": "07:00", "ende": "16:30", "pause_minuten": 30},
        anwesende_kolonnen=[{"firma": "ASB Fiber GmbH", "personen": 4, "gewerk": "Tiefbau"}],
        ausgefuehrte_arbeiten=[
            {"position": "Aushub", "menge": 80, "einheit": "m", "ort": "Birkenstraße"}
        ],
        geraete_einsatz=[{"geraet": "Bagger CAT 308", "stunden": 6.5}],
        materiallieferungen=[],
        besondere_vorkommnisse=["Leichter Regen ab 14:00"],
        behinderungen=[
            {
                "art": "Unbekannte Leitung",
                "vob_anzeige": True,
                "beschreibung": "Aushub gestoppt, Versorger informiert.",
            }
        ],
        fotos_referenz=["ph-uuid-1"],
        polier_name="Mirzo Polier",
        stunden_gesamt=9.0,
        warnungen=[],
    )


def test_render_produces_valid_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pdf_renderer.settings.storage_local_path", tmp_path
    )
    co = _Stub(name="ASB Fiber GmbH")
    proj = _Stub(
        name="FTTH Bayreuth Los 3",
        auftraggeber="Stadtwerke Bayreuth",
        project_number="2026-FTTH-003",
        address="Bayreuth",
    )

    result = render_bautagebuch(
        _sample_data(), company=co, project=proj, session_id="ssn-test-001"
    )

    assert result.pdf_path.exists()
    assert result.pdf_bytes.startswith(b"%PDF-")
    assert len(result.pdf_bytes) > 5000  # non-trivial layout
    # The HTML version must contain the German VOB language
    assert "Bautagebuch" in result.html
    assert "FTTH Bayreuth Los 3" in result.html
    assert "Anzeige hiermit erfolgt" in result.html  # vob_anzeige flag rendered


def test_render_handles_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pdf_renderer.settings.storage_local_path", tmp_path
    )
    bd = _sample_data()
    bd_with_warnings = BautagebuchData(
        **{**{f.name: getattr(bd, f.name) for f in bd.__dataclass_fields__.values()},
           "warnungen": ["Arbeitszeit nicht erfasst", "Wetter unbekannt"]}
    )
    co = _Stub(name="X GmbH")
    proj = _Stub(name="Test", auftraggeber=None, project_number=None, address=None)
    result = render_bautagebuch(
        bd_with_warnings, company=co, project=proj, session_id="ssn-test-002"
    )
    assert "Warnungen" in result.html
    assert "Arbeitszeit nicht erfasst" in result.html
