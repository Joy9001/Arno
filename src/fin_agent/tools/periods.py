"""Period strings resolved on the host, which has a clock; the sandbox does not."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def resolve_period(period: str | None, today: date) -> tuple[date, date]:
    """Return inclusive `(start, end)` dates for a period the model or API named."""
    if not period or period == "this_month":
        start = today.replace(day=1)
        return start, today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if period == "last_month":
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    if period == "today":
        return today, today
    if period == "last_7_days":
        return today - timedelta(days=6), today
    if period == "last_30_days":
        return today - timedelta(days=29), today
    if period == "this_year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    match = _MONTH.match(period)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"Invalid month in period {period!r}")
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    match = _RANGE.match(period)
    if match:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
        if end < start:
            raise ValueError("A period range ends before it starts")
        return start, end
    raise ValueError(
        f"Unknown period {period!r}; use this_month, last_month, YYYY-MM, or YYYY-MM-DD..YYYY-MM-DD"
    )


__all__ = ["resolve_period"]
