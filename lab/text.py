"""Small helpers for pulling code and JSON out of model responses."""

from __future__ import annotations

import json
import re


def extract_code(text: str) -> str:
    """Return the first fenced code block, or the whole text if none is fenced."""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from a model response."""
    m = re.search(r"```(?:json)?\s*\n(\{.*?\})\s*```", text, re.DOTALL)
    candidate = m.group(1) if m else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        candidate = text[start:end + 1] if start != -1 and end > start else ""
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return {}
