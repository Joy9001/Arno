"""iCalendar RRULE expansion with India's holiday adjustments.

`python-dateutil` owns the recurrence arithmetic. We only add the two things it does not know:
an India working-day calendar, and a policy for moving an occurrence off a holiday ("next" for
bills, "previous" for salary paid on the last working day).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta

from dateutil.rrule import rrule, rrulestr

from fin_agent.india.workdays import (
    HolidayAdjust,
    IndiaCalendar,
    adjust_to_working_day,
    calendar_for,
)


def normalize_rule(rule: str) -> str:
    text = rule.strip()
    if not text:
        raise ValueError("A schedule needs an RRULE")
    if not text.upper().startswith("RRULE:"):
        text = f"RRULE:{text}"
    return text


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
    return first.date() if first else None


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
    effective_end = min(end, ends) if ends is not None else end
    if effective_end < start:
        return []

    calendar = calendar or calendar_for(
        start - timedelta(days=7), effective_end + timedelta(days=7)
    )
    buffer_start = start - timedelta(days=7)
    buffer_end = effective_end + timedelta(days=7)
    dates: set[date] = set()
    for raw in _between(parsed, buffer_start, buffer_end):
        if raw < starts:
            continue
        if ends is not None and raw > ends:
            continue
        adjusted = adjust_to_working_day(raw, holiday_adjust, calendar)
        if adjusted < start or adjusted > effective_end:
            continue
        dates.add(adjusted)
    return sorted(dates)
