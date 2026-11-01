"""Indian working days: weekends, national holidays, and the adjustments schedules need.

The `holidays` package supplies the national calendar. State holidays are not included; a
self-hoster in a state with extra holidays can add them to the schedule's RRULE or choose a
different month day. Weekends are Saturday and Sunday.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import holidays as holidays_pkg

HolidayAdjust = Literal["none", "next", "previous"]
HOLIDAY_ADJUSTMENTS: tuple[HolidayAdjust, ...] = ("none", "next", "previous")


@dataclass(frozen=True, slots=True)
class IndiaCalendar:
    """National holidays for a span of years, with weekend awareness."""

    years: tuple[int, ...]
    _holidays: holidays_pkg.India

    @classmethod
    def for_span(cls, start: date, end: date) -> IndiaCalendar:
        first = min(start.year, end.year) - 1
        last = max(start.year, end.year) + 1
        years = tuple(range(first, last + 1))
        return cls(years=years, _holidays=holidays_pkg.India(years=list(years)))

    def is_weekend(self, day: date) -> bool:
        return day.weekday() >= 5

    def holiday_name(self, day: date) -> str | None:
        return self._holidays.get(day)

    def is_holiday(self, day: date) -> bool:
        return day in self._holidays

    def is_working_day(self, day: date) -> bool:
        return not self.is_weekend(day) and not self.is_holiday(day)

    def next_working_day(self, day: date) -> date:
        current = day
        for _ in range(370):
            if self.is_working_day(current):
                return current
            current += timedelta(days=1)
        raise ValueError(f"No working day found after {day.isoformat()}")

    def previous_working_day(self, day: date) -> date:
        current = day
        for _ in range(370):
            if self.is_working_day(current):
                return current
            current -= timedelta(days=1)
        raise ValueError(f"No working day found before {day.isoformat()}")


def calendar_for(start: date, end: date) -> IndiaCalendar:
    return IndiaCalendar.for_span(start, end)


def adjust_to_working_day(
    day: date, policy: HolidayAdjust, calendar: IndiaCalendar | None = None
) -> date:
    """Move a holiday or weekend day according to the schedule's policy."""
    if policy == "none":
        return day
    calendar = calendar or IndiaCalendar.for_span(day, day)
    if calendar.is_working_day(day):
        return day
    if policy == "next":
        return calendar.next_working_day(day)
    return calendar.previous_working_day(day)


def last_working_day(year: int, month: int, calendar: IndiaCalendar | None = None) -> date:
    """The last Monday-Friday that is not a national holiday in the month."""
    last = date(year, month, _calendar.monthrange(year, month)[1])
    calendar = calendar or IndiaCalendar.for_span(last, last)
    return calendar.previous_working_day(last)
