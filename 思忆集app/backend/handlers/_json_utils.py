"""Shared JSON extraction utilities for LLM responses."""

from __future__ import annotations

import json
from typing import Any


def parse_json_from_llm(text: str) -> dict[str, Any] | list[Any] | None:
    """Extract JSON from LLM response (strip markdown fence, find {} or [] boundaries)."""
    c = text.strip()
    if c.startswith("```"):
        nl = c.find("\n")
        if nl >= 0:
            c = c[nl + 1:]
        if c.endswith("```"):
            c = c[:-3]
        c = c.strip()

    # Try object first
    bs = c.find("{")
    be = c.rfind("}")
    if bs >= 0 and be > bs:
        try:
            return json.loads(c[bs:be + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    # Try array
    bs = c.find("[")
    be = c.rfind("]")
    if bs >= 0 and be > bs:
        try:
            return json.loads(c[bs:be + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return None

