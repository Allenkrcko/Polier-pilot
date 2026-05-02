"""Prompt templates loaded from sibling .txt files."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


@lru_cache(maxsize=8)
def load(name: str) -> str:
    """Read a prompt file by stem (e.g. load("classifier"))."""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
