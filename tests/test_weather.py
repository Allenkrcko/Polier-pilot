"""Tests for app.services.weather with Open-Meteo mocked."""
from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.services import weather


@pytest.mark.asyncio
async def test_fetch_returns_parsed_snapshot() -> None:
    captured_params: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "daily": {
                    "temperature_2m_max": [17.2],
                    "temperature_2m_min": [8.4],
                    "temperature_2m_mean": [12.6],
                    "precipitation_sum": [2.4],
                    "weather_code": [61],
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        snap = await weather.fetch(
            lat=52.524, lon=13.405, work_date=date(2026, 5, 2), client=client
        )

    assert snap.date == date(2026, 5, 2)
    assert snap.temperature_min_c == pytest.approx(8.4)
    assert snap.temperature_max_c == pytest.approx(17.2)
    assert snap.precipitation_mm == pytest.approx(2.4)
    assert snap.weather_code == 61
    assert snap.weather_code_text == "Leichter Regen"
    assert "Leichter Regen" in snap.summary_de
    assert "8" in snap.summary_de  # min temp shows up
    # The request used the right query params
    assert captured_params["start_date"] == "2026-05-02"
    assert captured_params["latitude"] == "52.52400"


@pytest.mark.asyncio
async def test_fetch_handles_missing_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"daily": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        snap = await weather.fetch(
            lat=0, lon=0, work_date=date(2026, 1, 1), client=client
        )
    assert snap.weather_code is None
    assert snap.weather_code_text == "Wetter unbekannt"
    assert snap.summary_de == "Wetter unbekannt"
