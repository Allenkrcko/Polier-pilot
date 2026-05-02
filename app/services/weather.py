"""Historical weather lookup via Open-Meteo.

Open-Meteo's archive API is free, key-less, and Germany-friendly. We fetch
a single day's daily aggregates plus a German `weather_code_text` so the
Bautagebuch can include the conditions even when the Polier did not
mention them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Final

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT: Final = httpx.Timeout(10.0, connect=5.0)

# WMO weather codes -> short German label. Source: open-meteo.com/en/docs.
_WMO_CODES_DE: Final[dict[int, str]] = {
    0: "Klar",
    1: "Überwiegend klar",
    2: "Teilweise bewölkt",
    3: "Bedeckt",
    45: "Nebel",
    48: "Reifnebel",
    51: "Leichter Nieselregen",
    53: "Mäßiger Nieselregen",
    55: "Starker Nieselregen",
    56: "Leichter gefrierender Nieselregen",
    57: "Starker gefrierender Nieselregen",
    61: "Leichter Regen",
    63: "Mäßiger Regen",
    65: "Starker Regen",
    66: "Leichter gefrierender Regen",
    67: "Starker gefrierender Regen",
    71: "Leichter Schneefall",
    73: "Mäßiger Schneefall",
    75: "Starker Schneefall",
    77: "Schneegriesel",
    80: "Leichte Regenschauer",
    81: "Mäßige Regenschauer",
    82: "Heftige Regenschauer",
    85: "Leichte Schneeschauer",
    86: "Starke Schneeschauer",
    95: "Gewitter",
    96: "Gewitter mit leichtem Hagel",
    99: "Gewitter mit starkem Hagel",
}


@dataclass(frozen=True)
class WeatherSnapshot:
    """Daily aggregate for a single date + GPS point."""

    date: date
    temperature_min_c: float | None
    temperature_max_c: float | None
    temperature_mean_c: float | None
    precipitation_mm: float | None
    weather_code: int | None
    weather_code_text: str

    @property
    def summary_de(self) -> str:
        """Human-friendly German one-liner for the Bautagebuch."""
        parts: list[str] = [self.weather_code_text or "Wetter unbekannt"]
        if self.temperature_min_c is not None and self.temperature_max_c is not None:
            parts.append(
                f"{self.temperature_min_c:.0f}–{self.temperature_max_c:.0f} °C"
            )
        elif self.temperature_mean_c is not None:
            parts.append(f"{self.temperature_mean_c:.0f} °C")
        if self.precipitation_mm is not None and self.precipitation_mm > 0:
            parts.append(f"{self.precipitation_mm:.1f} mm Niederschlag")
        return ", ".join(parts)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
async def fetch(
    *,
    lat: float,
    lon: float,
    work_date: date,
    client: httpx.AsyncClient | None = None,
) -> WeatherSnapshot:
    """Fetch the daily aggregate for (lat, lon, work_date).

    Always uses the configured OPEN_METEO_BASE_URL (archive endpoint).
    """
    params = {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "start_date": work_date.isoformat(),
        "end_date": work_date.isoformat(),
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "temperature_2m_mean",
                "precipitation_sum",
                "weather_code",
            ]
        ),
        "timezone": settings.timezone,
    }

    own = client is None
    client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    try:
        resp = await client.get(settings.open_meteo_base_url, params=params)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own:
            await client.aclose()

    daily = data.get("daily") or {}
    code = (daily.get("weather_code") or [None])[0]
    return WeatherSnapshot(
        date=work_date,
        temperature_min_c=_first_or_none(daily.get("temperature_2m_min")),
        temperature_max_c=_first_or_none(daily.get("temperature_2m_max")),
        temperature_mean_c=_first_or_none(daily.get("temperature_2m_mean")),
        precipitation_mm=_first_or_none(daily.get("precipitation_sum")),
        weather_code=int(code) if isinstance(code, (int, float)) else None,
        weather_code_text=_WMO_CODES_DE.get(int(code), "Wetter unbekannt")
        if isinstance(code, (int, float))
        else "Wetter unbekannt",
    )


def _first_or_none(values: list | None) -> float | None:
    if not values:
        return None
    head = values[0]
    if head is None:
        return None
    try:
        return float(head)
    except (TypeError, ValueError):
        return None
