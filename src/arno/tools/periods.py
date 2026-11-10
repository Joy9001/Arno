"""Period strings resolved on the host, which has a clock; the sandbox does not."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_period(period: str | None, today: date) -> tuple[date, date]:
    """Return inclusive `(start, end)` dates for a period the model or API named.

    The ledger never holds a date after today, so a period that runs into the future ends
    today: an open month or week means "so far", matching the recaps. Otherwise coverage would
    call every day left in the month a missing statement. Weeks start on Monday.
    """
    if not period or period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    if period == "this_week":
        return today - timedelta(days=today.weekday()), today
    if period == "last_week":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if period == "today":
        return today, today
    if period == "last_7_days":
        return today - timedelta(days=6), today
    if period == "last_30_days":
        return today - timedelta(days=29), today
    if period == "this_year":
        return date(today.year, 1, 1), today
    if _DAY.match(period):
        start = end = date.fromisoformat(period)
        if start > today:
            raise ValueError(f"The period {period!r} has not started yet")
        return start, end
    match = _MONTH.match(period)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"Invalid month in period {period!r}")
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
    else:
        match = _RANGE.match(period)
        if match is None:
            raise ValueError(
                f"Unknown period {period!r}; use this_month, last_month, this_week, last_week, "
                'this_year, today, last_7_days, last_30_days, "YYYY-MM", "YYYY-MM-DD", or '
                '"YYYY-MM-DD..YYYY-MM-DD"'
            )
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
        if end < start:
            raise ValueError("A period range ends before it starts")
    if start > today:
        raise ValueError(f"The period {period!r} has not started yet")
    return start, min(end, today)


__all__ = ["resolve_period"]
