"""Tests for confirmation token detection in the worker tasks."""
from __future__ import annotations

import pytest

from app.workers import tasks


@pytest.mark.parametrize(
    "text",
    [
        "✅",
        "✔",
        "ok",
        "OK",
        "OK ",
        " ja",
        "Da",
        "potvrđujem",
        "potvrdjujem",
        "Bestätigt",
        "tamam",
        "evet",
        "potwierdzam",
        "tak",
    ],
)
def test_is_confirmation_accepts_known_tokens(text: str) -> None:
    assert tasks._is_confirmation(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "imam pitanje",
        "ne ide tako",
        "okay so we should change the report",
        "ja imam pitanje za bauleiter",  # contains "ja" but not exact-match
    ],
)
def test_is_confirmation_rejects_other(text: str | None) -> None:
    assert tasks._is_confirmation(text) is False
