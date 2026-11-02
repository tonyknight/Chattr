"""Apple Messages dates.

`message.date` counts from 2001-01-01 00:00:00 UTC: in seconds on older databases and in
nanoseconds on newer ones (macOS 10.13 and later write nanoseconds, and older rows keep the
old unit). Telling them apart is safe: a seconds value is below 1e11 until the year 5400, while
a nanoseconds value passes 1e11 within 100 seconds of 2001.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
_NANOSECONDS_FROM = 100_000_000_000


def to_utc(value: Optional[int]) -> Optional[datetime]:
    """The UTC moment for an Apple timestamp in seconds or nanoseconds; None when unset (0)."""
    if not value:
        return None
    seconds = value / 1_000_000_000 if abs(value) >= _NANOSECONDS_FROM else float(value)
    return APPLE_EPOCH + timedelta(seconds=seconds)
