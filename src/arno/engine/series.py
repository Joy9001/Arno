"""Recurring payment series, found by clustering one payee's gaps.

A payee with at least three payments is a series when the median gap between consecutive payments
sits within three days of a known cadence. Confidence is the share of gaps that do.
"""

from __future__ import annotations

import calendar
import statistics
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from arno.india.recurrence import expand_rule
from arno.india.workdays import IndiaCalendar

_MIN_PAYMENTS = 3
_CADENCE_TOLERANCE = 3
# (days, family), monthly before four-weekly so a 29-day median reads as monthly.
_CADENCES = (
    (30, "monthly"),
    (28, "four_weekly"),
    (7, "weekly"),
    (14, "fortnightly"),
    (90, "quarterly"),
    (365, "yearly"),
)
# shortcut: variable bills sample the last 12 payments only, revisit if real utility bills show
# seasonal or tail misses
_MAX_SAMPLES = 12
# A price step needs at least this many payments at the old level immediately before it.
_MIN_FIXED_RUN = 3
# Amounts belong to one level when they stay within 2% of its median (50 * diff <= median).
_LEVEL_TOLERANCE = 50
# A series whose last payment is older than two cadences (one missed payment is allowed) has
# stopped; alive is 0 then.
_ALIVE_CADENCES = 2
PROPOSE_CONFIDENCE: float = 0.9
PROPOSE_ALIVE: float = 0.5
# Only a series whose every gap fits its cadence enters the projection.
PROJECT_CONFIDENCE: float = 1.0

_GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "ach",
        "autopay",
        "cr",
        "dr",
        "imps",
        "india",
        "limited",
        "ltd",
        "mandate",
        "nach",
        "neft",
        "payment",
        "pos",
        "pvt",
        "ref",
        "rtgs",
        "txn",
        "upi",
    }
)


@dataclass(frozen=True, slots=True)
class Payment:
    movement_id: str
    date: date
    amount_paise: int


@dataclass(frozen=True, slots=True)
class Rule:
    family: str
    rrule: str
    starts: date
    holiday_adjust: str
    description: str


@dataclass(frozen=True, slots=True)
class Amounts:
    kind: str
    level_paise: int
    low_paise: int
    high_paise: int
    samples: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PriceStep:
    old_paise: int
    new_paise: int
    index: int


@dataclass(frozen=True, slots=True)
class Detection:
    rule: Rule
    confidence: float
    alive: float
    jitter: tuple[tuple[int, float], ...]
    matched: tuple[str, ...]
    amounts: Amounts


def normalize(name: str | None) -> str:
    """Build a stable payee key from statement text while preserving non-Latin letters."""
    if name is None or len(name) == 0:
        return ""
    folded = unicodedata.normalize("NFKC", name).casefold()
    chars: list[str] = []
    for ch in folded:
        cat = unicodedata.category(ch)
        if len(cat) > 0 and cat[0] in ("L", "M", "N"):
            chars.append(ch)
        else:
            chars.append(" ")
    cleaned = "".join(chars)
    tokens: list[str] = []
    for token in cleaned.split():
        digit_count = sum(1 for ch in token if ch.isdigit())
        if digit_count == len(token) or digit_count >= 4:
            continue
        if token in _GENERIC_TOKENS:
            continue
        tokens.append(token)
    return " ".join(tokens)


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{('th', 'st', 'nd', 'rd')[n % 10] if n % 10 < 4 else 'th'}"


def _rule(family: str, dates: Sequence[date]) -> Rule:
    """The calendar rule a family's payments follow, anchored on the latest payment."""
    last = dates[-1]
    weekday = calendar.day_name[last.weekday()]
    code = weekday[:2].upper()
    month_start = last.replace(day=1)
    day = sorted(d.day for d in dates)[len(dates) // 2]
    ordinal = _ordinal(day)
    if family == "weekly":
        return Rule(family, f"FREQ=WEEKLY;BYDAY={code}", last, "none", f"weekly on {weekday}")
    if family == "fortnightly":
        rrule = f"FREQ=WEEKLY;INTERVAL=2;BYDAY={code}"
        return Rule(family, rrule, last, "none", f"fortnightly on {weekday}")
    if family == "four_weekly":
        return Rule(family, "FREQ=DAILY;INTERVAL=28", last, "none", "every 4 weeks")
    if family == "monthly":
        rrule = f"FREQ=MONTHLY;BYMONTHDAY={day}"
        return Rule(family, rrule, month_start, "none", f"monthly on the {ordinal}")
    if family == "quarterly":
        rrule = f"FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY={last.day}"
        return Rule(family, rrule, month_start, "none", f"quarterly on the {_ordinal(last.day)}")
    rrule = f"FREQ=YEARLY;BYMONTH={last.month};BYMONTHDAY={last.day}"
    label = f"yearly on {calendar.month_name[last.month]} {_ordinal(last.day)}"
    return Rule(family, rrule, date(last.year, 1, 1), "none", label)


def _median_paise(values: Sequence[int]) -> int:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def _same_level(value: int, level: int) -> bool:
    """Whether an amount stays within the 2% tolerance of a level."""
    return _LEVEL_TOLERANCE * abs(value - level) <= level


def _is_fixed_run(values: Sequence[int]) -> tuple[bool, int]:
    med = _median_paise(values)
    if med <= 0:
        return False, med
    return all(_same_level(v, med) for v in values), med


def _summarize_amounts(amounts: Sequence[int]) -> Amounts:
    # price_step owns the fixed-run rule, so a stored level is always a level that was charged.
    step = price_step(amounts)
    if step is not None:
        run = amounts[step.index :]
        return Amounts(
            kind="stepped",
            level_paise=step.new_paise,
            low_paise=min(run),
            high_paise=max(run),
            samples=tuple(run[-_MAX_SAMPLES:]),
        )

    samples = tuple(amounts[-_MAX_SAMPLES:])
    fixed_ok, med = _is_fixed_run(amounts)
    if fixed_ok:
        return Amounts(
            kind="fixed",
            level_paise=med,
            low_paise=min(amounts),
            high_paise=max(amounts),
            samples=samples,
        )

    return Amounts(
        kind="variable",
        level_paise=_median_paise(samples),
        low_paise=min(samples),
        high_paise=max(samples),
        samples=samples,
    )


def stepped_level(values: Sequence[int], *, from_paise: int) -> int | None:
    """The common level of amounts that all stepped off ``from_paise``, or None.

    Every amount must differ from the old level by more than the 2% tolerance, in one direction,
    and the amounts must agree with each other.
    """
    if len(values) == 0 or from_paise <= 0:
        return None
    fixed, level = _is_fixed_run(values)
    if not fixed:
        return None
    if not all(
        (value > from_paise) == (values[0] > from_paise) and not _same_level(value, from_paise)
        for value in values
    ):
        return None
    return level


def price_step(amounts: Sequence[int]) -> PriceStep | None:
    """The most recent step to a new amount level in a series, or None.

    The payments just before the step must hold a fixed run of at least 3 at the old level, and
    every payment from the step on must sit at one new level, so a single odd payment followed by
    a return to the old level is not a step. Ordinary variation never yields one.
    """
    for split in range(len(amounts) - 1, _MIN_FIXED_RUN - 1, -1):
        old_paise = None
        for start in range(0, split - _MIN_FIXED_RUN + 1):
            fixed, level = _is_fixed_run(amounts[start:split])
            if fixed:
                old_paise = level
                break
        if old_paise is None:
            continue
        new_paise = stepped_level(amounts[split:], from_paise=old_paise)
        if new_paise is not None:
            return PriceStep(old_paise=old_paise, new_paise=new_paise, index=split)
    return None


def detect(payments: Sequence[Payment], *, as_of: date, window_start: date) -> Detection | None:
    """The payee's recurring series, or None.

    Only payments in ``[window_start, as_of)`` count; a payment dated ``as_of`` is not observed
    yet. The series starts at the first payment followed by a gap that fits the cadence.
    """
    seen = sorted(
        (p for p in payments if window_start <= p.date < as_of),
        key=lambda p: (p.date, p.movement_id),
    )
    if len(seen) < _MIN_PAYMENTS:
        return None
    dates = [p.date for p in seen]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
    median = statistics.median(gaps)
    match = [
        (days, family) for days, family in _CADENCES if abs(median - days) <= _CADENCE_TOLERANCE
    ]
    if not match:
        return None
    days, family = min(match, key=lambda item: abs(median - item[0]))
    # Stray payments before the first regular gap are another habit, not a lapse in this one.
    start = next((i for i, gap in enumerate(gaps) if abs(gap - days) <= _CADENCE_TOLERANCE), None)
    if start is None or len(gaps) - start < _MIN_PAYMENTS - 1:
        return None
    seen, dates, gaps = seen[start:], dates[start:], gaps[start:]
    near = [gap for gap in gaps if abs(gap - days) <= _CADENCE_TOLERANCE]
    offsets = Counter(gap - days for gap in near)
    alive = 1.0 if (as_of - dates[-1]).days <= _ALIVE_CADENCES * days else 0.0
    return Detection(
        rule=_rule(family, dates),
        confidence=len(near) / len(gaps),
        alive=alive,
        jitter=tuple((offset, count / len(near)) for offset, count in sorted(offsets.items())),
        matched=tuple(p.movement_id for p in seen),
        amounts=_summarize_amounts([p.amount_paise for p in seen]),
    )


def upcoming(
    detection: Detection,
    *,
    after: date,
    until: date,
    calendar: IndiaCalendar | None = None,
) -> list[date]:
    """Return the detected rule's expected dates in (after, until]."""
    if until <= after:
        return []
    return expand_rule(
        detection.rule.rrule,
        detection.rule.starts,
        start=after + timedelta(days=1),
        end=until,
        calendar=calendar,
    )
