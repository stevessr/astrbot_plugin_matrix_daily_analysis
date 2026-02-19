"""
Time parsing helpers.
"""

import math
from datetime import datetime
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a value into a local datetime, returning None on invalid input."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(timestamp):
        return None

    try:
        return datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return None


def get_hour_from_timestamp(value: Any, default: int = 0) -> int:
    """Get hour value from timestamp-like input."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return default
    return parsed.hour


def format_timestamp_hm(value: Any, default: str = "00:00") -> str:
    """Format timestamp-like input to HH:MM."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return default
    return parsed.strftime("%H:%M")
