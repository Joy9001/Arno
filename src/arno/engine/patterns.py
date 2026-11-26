"""Pure recurring-pattern coverage rules and cadence helpers.

Detection itself lives in ``arno.engine.series``; database loading, storage of
``series_detections``, and missed-occurrence queries live in ``arno.tools.patterns``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

AMOUNT_TOLERANCE = 0.10
MISSED_AFTER_DAYS = 3
HISTORY_DAYS = 800

FAMILY_INTERVAL_DAYS: dict[str, int] = {
    "weekly": 7,
    "fortnightly": 14,
    "four_weekly": 28,
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}


@dataclass(frozen=True, slots=True)
class KnownSchedule:
    id: str
    name: str
    normalized: str
    amount_paise: int | None
    category_id: str | None = None
    interval_days: int | None = None
    confirmed: bool = True


def family_interval_days(family: str) -> int | None:
    """Map a ``series.Rule.family`` name to an approximate interval in days."""
    return FAMILY_INTERVAL_DAYS.get(family)


def rule_interval_days(rule: str | None) -> int | None:
    """Derive an approximate cadence in days from an RRULE string."""
    upper = (rule or "").upper()
    freq_match = re.search(r"FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)", upper)
    if not freq_match:
        return None
    freq = freq_match.group(1)
    interval_match = re.search(r"INTERVAL=(\d+)", upper)
    step = int(interval_match.group(1)) if interval_match else 1
    if freq == "DAILY":
        return step
    if freq == "WEEKLY":
        return 7 * step
    if freq == "MONTHLY":
        if step == 3:
            return 90
        if step == 6:
            return 180
        if step == 1:
            bymonthday = re.search(r"BYMONTHDAY=([0-9,-]+)", upper)
            positions = re.search(r"BYSETPOS=([0-9,-]+)", upper)
            # A clamped date list can still select two occurrences per month.
            if (
                bymonthday
                and "," in bymonthday.group(1)
                and (positions is None or "," in positions.group(1))
            ):
                return 15
            return 30
        return 30 * step
    return 365 * step


# shortcut: one detected series per payee and raw token overlap in same_merchant, revisit when a
# real payee holds two plans or false-matches generic words
def same_merchant(schedule: KnownSchedule, normalized: str) -> bool:
    """Whether a schedule name and normalized merchant share identity."""
    if not schedule.normalized or not normalized:
        return False
    if schedule.normalized == normalized:
        return True
    left = set(schedule.normalized.split())
    right = set(normalized.split())
    return bool(left & right)


def same_charge(
    schedule: KnownSchedule,
    categories: set[str],
    amount_paise: int,
    interval_days: int,
) -> bool:
    """A schedule with the same category, cadence, and amount covers this charge.

    A schedule's name is often a label ("Rent") while the payee is a person ("Ramesh
    (landlord)"), so name matching alone calls a covered charge unscheduled.
    """
    if not schedule.category_id or schedule.category_id not in categories:
        return False
    if schedule.interval_days != interval_days:
        return False
    if not schedule.amount_paise:
        return False
    return abs(amount_paise - schedule.amount_paise) <= schedule.amount_paise * AMOUNT_TOLERANCE


def is_covered(
    *,
    normalized: str,
    categories: set[str],
    amount_paise: int,
    interval_days: int | None,
    schedules: Sequence[KnownSchedule],
) -> bool:
    """Whether any confirmed schedule covers this series."""
    for schedule in schedules:
        if not schedule.confirmed:
            continue
        if same_merchant(schedule, normalized):
            return True
        if interval_days is not None and same_charge(
            schedule, categories, amount_paise, interval_days
        ):
            return True
    return False


__all__ = [
    "AMOUNT_TOLERANCE",
    "FAMILY_INTERVAL_DAYS",
    "HISTORY_DAYS",
    "MISSED_AFTER_DAYS",
    "KnownSchedule",
    "family_interval_days",
    "is_covered",
    "rule_interval_days",
    "same_charge",
    "same_merchant",
]
