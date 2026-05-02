"""EXIF extraction for photos.

We need three things from a Polier's photo: GPS coordinates (decimal),
the EXIF DateTimeOriginal (when the picture was taken, not when uploaded),
and the camera timezone where present. Uses Pillow because it ships with
heic-friendly transform support and is already in our deps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExifData:
    """Subset of EXIF fields we care about. All fields are optional."""

    gps_lat: float | None = None
    gps_lon: float | None = None
    gps_altitude_m: float | None = None
    captured_at: datetime | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    width: int | None = None
    height: int | None = None


def _ratio_to_float(value: Any) -> float | None:
    """Coerce Pillow's IFDRational / Fraction / float / int to a float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dms_to_decimal(values: tuple[Any, Any, Any], ref: str | None) -> float | None:
    """Convert (degrees, minutes, seconds) + N/S/E/W reference to a signed float."""
    if not values or len(values) < 3:
        return None
    deg = _ratio_to_float(values[0])
    minutes = _ratio_to_float(values[1])
    seconds = _ratio_to_float(values[2])
    if deg is None or minutes is None or seconds is None:
        return None
    decimal = deg + minutes / 60.0 + seconds / 3600.0
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    # EXIF DateTimeOriginal format: "YYYY:MM:DD HH:MM:SS"
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=timezone.utc)  # EXIF rarely carries TZ; assume UTC
    return None


def extract(image_path: Path) -> ExifData:
    """Read EXIF from `image_path` and return the parsed subset.

    Missing or unreadable EXIF returns an empty ExifData rather than raising,
    so the worker pipeline can still proceed with a photo that lacks metadata.
    """
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            raw = img.getexif() or {}
    except Exception as exc:  # noqa: BLE001 - tolerate any decoder failure
        logger.warning("EXIF parse failed for %s: %s", image_path, exc)
        return ExifData()

    # Map numeric tags to readable names.
    named: dict[str, Any] = {}
    for tag_id, value in raw.items():
        name = ExifTags.TAGS.get(tag_id, str(tag_id))
        named[name] = value

    gps_info: dict[str, Any] = {}
    raw_gps = named.get("GPSInfo")
    if isinstance(raw_gps, dict):
        for tag_id, value in raw_gps.items():
            name = ExifTags.GPSTAGS.get(tag_id, str(tag_id))
            gps_info[name] = value
    elif raw_gps is not None:
        # Pillow >=10 can also expose GPSInfo as an IFD; try get_ifd.
        try:
            with Image.open(image_path) as img:
                ifd = img.getexif().get_ifd(ExifTags.IFD.GPSInfo)
            for tag_id, value in (ifd or {}).items():
                name = ExifTags.GPSTAGS.get(tag_id, str(tag_id))
                gps_info[name] = value
        except Exception as exc:  # noqa: BLE001
            logger.debug("GPSInfo IFD read failed: %s", exc)

    lat = _dms_to_decimal(
        gps_info.get("GPSLatitude"), gps_info.get("GPSLatitudeRef")
    )
    lon = _dms_to_decimal(
        gps_info.get("GPSLongitude"), gps_info.get("GPSLongitudeRef")
    )
    altitude = _ratio_to_float(gps_info.get("GPSAltitude"))
    if altitude is not None and gps_info.get("GPSAltitudeRef") == 1:
        altitude = -altitude

    captured = _parse_datetime(named.get("DateTimeOriginal") or named.get("DateTime"))

    return ExifData(
        gps_lat=lat,
        gps_lon=lon,
        gps_altitude_m=altitude,
        captured_at=captured,
        camera_make=named.get("Make"),
        camera_model=named.get("Model"),
        width=width,
        height=height,
    )
