"""Tests for app.services.exif using Pillow to synthesize EXIF on the fly."""
from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image, TiffImagePlugin

from app.services import exif


def _rational(num: int, den: int = 1) -> TiffImagePlugin.IFDRational:
    return TiffImagePlugin.IFDRational(num, den)


def _build_jpeg_with_exif(path: Path) -> None:
    """Write a tiny JPEG with EXIF: GPS @ Berlin + DateTimeOriginal."""
    img = Image.new("RGB", (16, 16), color="white")
    e = img.getexif()
    e[306] = "2026:05:02 14:23:00"  # DateTime
    e[36867] = "2026:05:02 14:23:00"  # DateTimeOriginal
    e[271] = "TestMake"  # Make
    e[272] = "TestModel"  # Model

    gps = {
        1: "N",  # GPSLatitudeRef
        2: (_rational(52), _rational(31), _rational(34, 10)),  # 52° 31' 3.4" = ~52.5176
        3: "E",  # GPSLongitudeRef
        4: (_rational(13), _rational(24), _rational(20, 10)),  # 13° 24' 2.0"
        5: 0,  # GPSAltitudeRef (above sea level)
        6: _rational(34),  # GPSAltitude
    }
    e[34853] = gps  # GPSInfo tag
    img.save(path, exif=e.tobytes())


def test_extract_returns_decoded_gps_and_timestamp(tmp_path: Path) -> None:
    image_path = tmp_path / "with_gps.jpg"
    _build_jpeg_with_exif(image_path)

    result = exif.extract(image_path)

    assert result.width == 16
    assert result.height == 16
    assert result.gps_lat is not None
    assert 52.50 < result.gps_lat < 52.55
    assert result.gps_lon is not None
    assert 13.39 < result.gps_lon < 13.42
    assert result.gps_altitude_m == pytest.approx(34.0)
    assert result.captured_at == datetime(2026, 5, 2, 14, 23, 0, tzinfo=timezone.utc)
    assert result.camera_make == "TestMake"
    assert result.camera_model == "TestModel"


def test_extract_empty_jpeg_returns_safe_defaults(tmp_path: Path) -> None:
    image_path = tmp_path / "no_exif.jpg"
    Image.new("RGB", (8, 8), color="black").save(image_path)

    result = exif.extract(image_path)

    assert result.gps_lat is None
    assert result.gps_lon is None
    assert result.captured_at is None
    assert result.width == 8
    assert result.height == 8


def test_extract_unreadable_file_returns_empty(tmp_path: Path) -> None:
    bad = tmp_path / "garbage.jpg"
    bad.write_bytes(b"not a jpeg at all")
    result = exif.extract(bad)
    assert result.gps_lat is None
    assert result.captured_at is None
    assert result.width is None


def test_dms_to_decimal_handles_southern_hemisphere() -> None:
    # Sydney 33° 51' 54" S = -33.865
    val = exif._dms_to_decimal((33, 51, 54.0), "S")
    assert val is not None
    assert val == pytest.approx(-33.865, rel=1e-3)
