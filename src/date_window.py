from __future__ import annotations

from datetime import date, timedelta


def previous_tuesday_to_sunday(today: date | None = None) -> tuple[date, date]:
    """Return the most recently completed Tuesday-Sunday window.

    Intended weekly run: Monday. If run on another day, it still resolves to the
    latest completed Sunday and the Tuesday five days earlier.
    """
    today = today or date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    if days_since_sunday == 0:
        # If someone runs this on Sunday, use the previous completed Sunday.
        days_since_sunday = 7
    end = today - timedelta(days=days_since_sunday)
    start = end - timedelta(days=5)
    return start, end
