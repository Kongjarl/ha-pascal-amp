"""Helpers for safely parsing values returned by the amplifier.

The amplifier is a third-party device; every value coming from it is treated as
untrusted text and parsed defensively so a malformed reply can never raise out
of an entity property and take Home Assistant down.
"""

from __future__ import annotations


def unquote(value: str | None) -> str | None:
    """Strip a single pair of surrounding double quotes from a string value."""
    if value is None:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def quote_if_needed(value: str) -> str:
    """Quote a string value for a SET command when it contains whitespace."""
    if value == "" or any(c.isspace() for c in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def safe_float(value: str | None, default: float | None = None) -> float | None:
    """Parse a float, returning *default* on any problem."""
    if value is None:
        return default
    try:
        return float(unquote(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def safe_int(value: str | None, default: int | None = None) -> int | None:
    """Parse an int, returning *default* on any problem."""
    if value is None:
        return default
    try:
        return int(float(unquote(value)))  # tolerate "1.0"
    except (TypeError, ValueError):
        return default


def safe_bool(value: str | None, default: bool = False) -> bool:
    """Parse a boolean (the amplifier uses 1/0), returning *default* on problem."""
    if value is None:
        return default
    text = unquote(value)
    if text is None:
        return default
    text = text.strip().lower()
    if text in ("1", "true", "on", "yes"):
        return True
    if text in ("0", "false", "off", "no", ""):
        return False
    return default
