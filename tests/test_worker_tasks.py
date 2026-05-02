"""Tests for app.workers.tasks confirmation text helper."""
from __future__ import annotations

from app.workers import tasks


def test_confirmation_text_short_message_de() -> None:
    out = tasks._confirmation_text("Heute 80 m Graben fertig.", "de")
    assert out == "✅ Verstanden (Deutsch): Heute 80 m Graben fertig."


def test_confirmation_text_truncates_at_80() -> None:
    long = "A" * 200
    out = tasks._confirmation_text(long, "hr")
    # "...) " prefix + up to 80 + "..."
    payload = out.split(": ", 1)[1]
    assert payload.endswith("...")
    assert len(payload) <= 80


def test_confirmation_text_unknown_language_falls_back_to_uppercase() -> None:
    out = tasks._confirmation_text("hi", "xx")
    assert "(XX)" in out


def test_confirmation_text_strips_newlines() -> None:
    out = tasks._confirmation_text("line1\nline2", "de")
    assert "\n" not in out
    assert "line1 line2" in out
