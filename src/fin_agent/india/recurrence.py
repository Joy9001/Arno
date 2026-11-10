"""iCalendar RRULE expansion with India's holiday adjustments.

`python-dateutil` owns the recurrence arithmetic. We only add the two things it does not know:
an India working-day calendar, and a policy for moving an occurrence off a holiday ("next" for
bills, "previous" for salary paid on the last working day).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule, rrulestr

from fin_agent.india.workdays import (
    HolidayAdjust,
    IndiaCalendar,
    adjust_to_working_day,
    calendar_for,
)

_UNTIL_DATETIME = re.compile(r"UNTIL=(\d{8})T(\d{6})(Z?)", re.IGNORECASE)
INDIA = ZoneInfo("Asia/Kolkata")


def _inclusive_until(match: re.Match[str]) -> str:
    """A date-based schedule means "through" a date-time UNTIL, not up to its midnight."""
    moment = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    if match.group(3) != "":
        moment = moment.replace(tzinfo=UTC).astimezone(INDIA)
    return f"UNTIL={moment:%Y%m%d}T235959"


def _clamp_month_end(text: str) -> str:
    """A month-end day means the last day in a short month, not a skipped month.

    RFC 5545 gives `BYMONTHDAY=31` no February occurrence, so a rent "on the 31st" would silently
    under-reserve one month a year. Every monthly obligation on a day 29-31 falls on min(day, the
    last day of that month): the earliest of that day and the last day, which is
    `BYMONTHDAY=<day>,-1;BYSETPOS=1`. Applied while parsing, so stored rules behave correctly too.
    """
    parts = text.split(";")
    if not any(part.strip().upper() == "FREQ=MONTHLY" for part in parts):
        return text
    if any(part.strip().upper().startswith(("BYSETPOS=", "BYDAY=", "BYMONTH=")) for part in parts):
        return text
    monthdays = [part for part in parts if part.strip().upper().startswith("BYMONTHDAY=")]
    if len(monthdays) != 1:
        return text
    day = monthdays[0].split("=", 1)[1]
    if not day.isdigit() or not 29 <= int(day) <= 31:
        return text
    return ";".join(f"BYMONTHDAY={day},-1" if part is monthdays[0] else part for part in parts) + (
        ";BYSETPOS=1"
    )


def normalize_rule(rule: str) -> str:
    text = rule.strip()
    if len(text) == 0:
        raise ValueError("A schedule needs an RRULE")
    # dateutil rejects a UTC UNTIL against a naive DTSTART, and a date-time UNTIL would drop the
    # final day's noon occurrence.
    text = _UNTIL_DATETIME.sub(_inclusive_until, text)
    if not text.upper().startswith("RRULE:"):
        text = f"RRULE:{text}"
    return text[:6] + _clamp_month_end(text[6:])


def parse_rule(rule: str, starts: date) -> rrule:
    """Parse an RRULE anchored at noon on `starts`.

    Noon keeps a date from drifting across a day when a time zone is applied later, matching the
    ledger's `event_at` convention.
    """
    text = normalize_rule(rule)
    try:
        parsed = rrulestr(text, dtstart=datetime.combine(starts, time(12)))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid RRULE: {exc}") from exc
    if not isinstance(parsed, rrule):
        raise ValueError("The rule must be a single RRULE, not an RRULESET")
    return parsed


def first_occurrence(rule: str, starts: date) -> date | None:
    parsed = parse_rule(rule, starts)
    first = parsed.after(datetime.combine(starts, time(0)), inc=True)
    return first.date() if first is not None else None


def _between(parsed: rrule, start: date, end: date) -> Iterator[date]:
    if end < start:
        return iter(())
    first = datetime.combine(start, time(0, 0, 0))
    last = datetime.combine(end, time(23, 59, 59))
    return (moment.date() for moment in parsed.between(first, last, inc=True))


def expand_rule(
    rule: str,
    starts: date,
    *,
    start: date,
    end: date,
    ends: date | None = None,
    holiday_adjust: HolidayAdjust = "none",
    calendar: IndiaCalendar | None = None,
) -> list[date]:
    """Expand a rule into dates in [start, end], after holiday adjustment.

    The expansion is widened by a week on each side so an occurrence moved by a holiday can still
    land inside the requested window. Duplicate adjusted dates collapse to one.
    """
    parsed = parse_rule(rule, starts)
    if end < start:
        return []

    calendar = (
        calendar
        if calendar is not None
        else calendar_for(start - timedelta(days=7), end + timedelta(days=7))
    )
    buffer_start = start - timedelta(days=7)
    buffer_end = end + timedelta(days=7)
    dates: set[date] = set()
    for raw in _between(parsed, buffer_start, buffer_end):
        if raw < starts:
            continue
        if ends is not None and raw > ends:
            continue
        adjusted = adjust_to_working_day(raw, holiday_adjust, calendar)
        # `ends` bounds the raw occurrence, not its holiday-adjusted date: the last bill can
        # move past the end date and must not disappear.
        if adjusted < start or adjusted > end:
            continue
        dates.add(adjusted)
    return sorted(dates)
