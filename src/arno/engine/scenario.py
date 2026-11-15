"""Multi-event what-if changes over a projection input, with no database or clock.

Each change is a typed frozen value referring to schedules, detected series, and goals by ID.
Changes are validated (dates inside the horizon, no overlapping windows on the same target) and
applied in a canonical order without mutating the input, using existing `CashEvent` kinds so the
scenario kernel stays untouched.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, fields, replace
from datetime import date, timedelta
from typing import Literal

from arno.engine.money import add_months, money
from arno.engine.projection import (
    BASE_HORIZON_DAYS,
    MAX_HORIZON_DAYS,
    CashEvent,
    ProjectionInput,
    SeriesForecast,
    horizon_days,
)

MAX_CHANGES = 12
HORIZON_LIMIT_OFFSET_DAYS = 30


@dataclass(frozen=True, slots=True)
class StopIncome:
    """Stop all income, or one named income schedule, within an inclusive window."""

    start: date
    until: date | None = None
    source_id: str | None = None
    label_name: str | None = None


@dataclass(frozen=True, slots=True)
class PauseSchedule:
    """Pause a schedule or a projected detected series within an inclusive window."""

    start: date
    until: date | None = None
    source_id: str | None = None
    series_id: str | None = None
    label_name: str = ""


@dataclass(frozen=True, slots=True)
class ChangeAmount:
    """Set a schedule's occurrences in an inclusive window to a new magnitude, keeping sign."""

    source_id: str
    amount_paise: int
    start: date
    until: date | None = None
    label_name: str = ""


@dataclass(frozen=True, slots=True)
class AddRecurring:
    """Add a recurring inflow, obligation, or goal contribution starting on ``start``."""

    name: str
    amount_paise: int
    direction: Literal["out", "in"]
    start: date
    until: date | None = None
    every: Literal["month", "week", "year"] = "month"
    goal_id: str | None = None
    goal_name: str | None = None


@dataclass(frozen=True, slots=True)
class OneTime:
    """Add one dated inflow or outflow (not an affordability purchase)."""

    name: str
    amount_paise: int
    direction: Literal["out", "in"]
    date: date


ScenarioChangeSpec = StopIncome | PauseSchedule | ChangeAmount | AddRecurring | OneTime


@dataclass(frozen=True, slots=True)
class ChangeResult:
    """One applied change in canonical order with its rendered template label and totals."""

    kind: str
    label: str
    occurrences: int
    amount_paise: int
    adds_inflow: bool = False


@dataclass(frozen=True, slots=True)
class ScenarioApplication:
    """The changed projection input alongside what each change moved."""

    inputs: ProjectionInput
    changes: tuple[ChangeResult, ...]
    adds_or_raises_inflow: bool
    has_added_flow: bool


@dataclass(frozen=True, slots=True)
class _Target:
    kind: Literal["all_income", "schedule", "series"]
    target_id: str
    is_income: bool
    start: date
    end: date
    description: str


def _window_text(start: date, until: date | None) -> str:
    return (
        f"from {start.isoformat()}"
        if until is None
        else f"from {start.isoformat()} to {until.isoformat()}"
    )


def _in_window(day: date, start: date, until: date | None, end_date: date) -> bool:
    return start <= day <= (min(until, end_date) if until is not None else end_date)


def _format_label(
    head: str, count: int, detail: str, *, empty: str = "no occurrence fell in that window."
) -> str:
    if count == 0:
        return f"{head}; {empty}"
    occ = "occurrence" if count == 1 else "occurrences"
    return f"{head} ({count} {occ}, {detail})."


def _field_key(value: object) -> tuple[int, str]:
    if value is None:
        return (0, "")
    if isinstance(value, date):
        return (1, value.isoformat())
    if isinstance(value, int) and not isinstance(value, bool):
        return (1, f"{value:020d}")
    return (1, str(value))


def _sort_key(change: ScenarioChangeSpec) -> tuple[str, tuple[tuple[str, tuple[int, str]], ...]]:
    return (
        type(change).__name__,
        tuple((f.name, _field_key(getattr(change, f.name))) for f in fields(change)),
    )


def _target_for(change: ScenarioChangeSpec, income_ids: set[str]) -> _Target | None:
    if isinstance(change, (AddRecurring, OneTime)):
        return None
    if isinstance(change, StopIncome):
        kind: Literal["all_income", "schedule", "series"] = (
            "all_income" if change.source_id is None else "schedule"
        )
        tid = change.source_id or ""
        name = change.label_name or change.source_id or "all income"
        op, is_income = "stop_income", True
    elif isinstance(change, PauseSchedule):
        if change.source_id is not None:
            kind, tid, is_income = "schedule", change.source_id, change.source_id in income_ids
        else:
            assert change.series_id is not None
            kind, tid, is_income = "series", change.series_id, False
        name = change.label_name or tid or "schedule"
        op = "pause_schedule"
    else:
        kind, tid, is_income = "schedule", change.source_id, change.source_id in income_ids
        name = change.label_name or tid
        op = "change_amount"
    desc = f"{op} ({name}, {_window_text(change.start, change.until)})"
    return _Target(kind, tid, is_income, change.start, change.until or date.max, desc)


def _overlaps(first: _Target, second: _Target) -> bool:
    if max(first.start, second.start) > min(first.end, second.end):
        return False
    if first.kind == second.kind and (
        first.kind == "all_income" or first.target_id == second.target_id
    ):
        return True
    return (first.kind == "all_income" and second.is_income) or (
        second.kind == "all_income" and first.is_income
    )


def _check_horizon_date(as_of: date, value: date, *, label: str) -> None:
    if value < as_of:
        raise ValueError(f"change {label} {value.isoformat()} cannot be before {as_of.isoformat()}")
    if (value - as_of).days > MAX_HORIZON_DAYS - HORIZON_LIMIT_OFFSET_DAYS:
        raise ValueError("change date is beyond the projection horizon")


def _validate_changes(
    as_of: date, changes: Sequence[ScenarioChangeSpec], income_ids: set[str]
) -> None:
    if not 1 <= len(changes) <= MAX_CHANGES:
        raise ValueError(f"changes must contain 1 to {MAX_CHANGES} items")
    for change in changes:
        if isinstance(change, OneTime):
            if change.amount_paise <= 0:
                raise ValueError("one_time amount_paise must be positive")
            _check_horizon_date(as_of, change.date, label="date")
            continue
        _check_horizon_date(as_of, change.start, label="start")
        if change.until is not None:
            if change.until < change.start:
                raise ValueError(
                    f"change until {change.until.isoformat()} must be on or after "
                    f"start {change.start.isoformat()}"
                )
            _check_horizon_date(as_of, change.until, label="until")
        if isinstance(change, PauseSchedule) and bool(change.source_id) == bool(change.series_id):
            raise ValueError("pause_schedule needs exactly one of source_id or series_id")
        if isinstance(change, (ChangeAmount, AddRecurring)) and change.amount_paise <= 0:
            raise ValueError("change amount_paise must be positive")
        if (
            isinstance(change, AddRecurring)
            and change.goal_id is not None
            and change.direction != "out"
        ):
            raise ValueError("goal contributions must have direction 'out'")

    targets = [t for change in changes if (t := _target_for(change, income_ids)) is not None]
    for index, first in enumerate(targets):
        for second in targets[index + 1 :]:
            if _overlaps(first, second):
                raise ValueError(
                    "overlapping changes on the same target: "
                    f"{first.description} and {second.description}"
                )


def scenario_horizon_days(
    as_of: date, changes: Sequence[ScenarioChangeSpec], *, ask_horizon_days: int | None = None
) -> int:
    """Horizon reaching at least 90 days past the latest start/date and through every ``until``."""
    latest_anchor = as_of
    latest_until = as_of
    for change in changes:
        if isinstance(change, OneTime):
            latest_anchor = max(latest_anchor, change.date)
        else:
            latest_anchor = max(latest_anchor, change.start)
            if change.until is not None:
                latest_until = max(latest_until, change.until)
    needed = max(
        BASE_HORIZON_DAYS,
        ask_horizon_days or 0,
        (latest_anchor - as_of).days + BASE_HORIZON_DAYS,
        (latest_until - as_of).days,
    )
    return max(1, min(MAX_HORIZON_DAYS, needed))


def _recurring_dates(
    start: date, until: date | None, every: Literal["month", "week", "year"], end_date: date
) -> list[date]:
    limit = min(until, end_date) if until is not None else end_date
    dates: list[date] = []
    step = 0
    while True:
        if every == "month":
            when = add_months(start, step)
        elif every == "week":
            when = start + timedelta(days=7 * step)
        else:
            when = add_months(start, 12 * step)
        if when > limit:
            break
        dates.append(when)
        step += 1
    return dates


def _remove_events(
    events: list[CashEvent], match: Callable[[CashEvent], bool]
) -> tuple[list[CashEvent], int, int]:
    kept: list[CashEvent] = []
    removed = 0
    moved = 0
    for event in events:
        if match(event):
            removed += 1
            moved += abs(event.amount_paise)
        else:
            kept.append(event)
    return kept, removed, moved


def _apply_stop_income(
    change: StopIncome, events: list[CashEvent], end_date: date
) -> tuple[list[CashEvent], ChangeResult]:
    kept, removed, moved = _remove_events(
        events,
        lambda event: (
            _in_window(event.date, change.start, change.until, end_date)
            and event.kind.startswith("income")
            and (change.source_id is None or event.source_id == change.source_id)
        ),
    )
    target = change.label_name or change.source_id or "all income"
    head = f"Stopped {target} {_window_text(change.start, change.until)}"
    return kept, ChangeResult(
        "stop_income",
        _format_label(head, removed, f"{money(moved)['text']} removed"),
        removed,
        moved,
    )


def _apply_pause_schedule(
    change: PauseSchedule,
    events: list[CashEvent],
    series_list: list[SeriesForecast],
    end_date: date,
) -> tuple[list[CashEvent], list[SeriesForecast], ChangeResult]:
    if change.source_id is not None:
        events, removed, moved = _remove_events(
            events,
            lambda event: (
                _in_window(event.date, change.start, change.until, end_date)
                and event.source_id == change.source_id
            ),
        )
        target = change.label_name or change.source_id
    else:
        assert change.series_id is not None
        removed = moved = 0
        series_name = change.label_name
        updated_series: list[SeriesForecast] = []
        for item in series_list:
            if item.id != change.series_id:
                updated_series.append(item)
                continue
            series_name = series_name or item.name
            kept_dates = tuple(
                day
                for day in item.expected
                if not _in_window(day, change.start, change.until, end_date)
            )
            removed = len(item.expected) - len(kept_dates)
            if removed > 0 and item.amounts:
                moved = removed * (sum(item.amounts) // len(item.amounts))
            updated_series.append(replace(item, expected=kept_dates))
        series_list = updated_series
        target = series_name or change.series_id
    head = f"Paused {target} {_window_text(change.start, change.until)}"
    label = _format_label(head, removed, f"{money(moved)['text']} removed")
    return events, series_list, ChangeResult("pause_schedule", label, removed, moved)


def _apply_change_amount(
    change: ChangeAmount, events: list[CashEvent], end_date: date
) -> tuple[list[CashEvent], ChangeResult]:
    changed = moved = delta_mag = 0
    raised_inflow = False
    updated: list[CashEvent] = []
    for event in events:
        if (
            _in_window(event.date, change.start, change.until, end_date)
            and event.source_id == change.source_id
        ):
            new_signed = change.amount_paise if event.amount_paise >= 0 else -change.amount_paise
            if event.kind.startswith("income") and new_signed > event.amount_paise:
                raised_inflow = True
            changed += 1
            moved += abs(new_signed - event.amount_paise)
            delta_mag += abs(new_signed) - abs(event.amount_paise)
            updated.append(replace(event, amount_paise=new_signed))
        else:
            updated.append(event)
    target = change.label_name or change.source_id
    amt = money(change.amount_paise)["text"]
    head = f"Changed {target} to {amt} {_window_text(change.start, change.until)}"
    if moved == 0:
        detail = "no amount change"
    else:
        suffix = "more" if delta_mag > 0 else "less" if delta_mag < 0 else "total difference"
        detail = f"{money(moved)['text']} {suffix}"
    return updated, ChangeResult(
        "change_amount",
        _format_label(head, changed, detail),
        changed,
        moved,
        adds_inflow=raised_inflow,
    )


def _apply_add_recurring(
    change: AddRecurring, as_of: date, end_date: date
) -> tuple[list[CashEvent], ChangeResult]:
    dates = _recurring_dates(change.start, change.until, change.every, end_date)
    if change.direction == "in":
        kind, signed, desc = "income_fixed", change.amount_paise, "inflow"
    elif change.goal_id is not None:
        kind, signed = "goal_contribution", -change.amount_paise
        desc = f"contribution to {change.goal_name or 'goal'}"
    else:
        kind, signed, desc = "obligation", -change.amount_paise, "obligation"
    added = [
        CashEvent(
            date=when,
            amount_paise=signed,
            name=change.name,
            kind=kind,
            confirmed=True,
            goal_id=change.goal_id,
            at_start=when <= as_of,
        )
        for when in dates
    ]
    count = len(dates)
    moved = count * change.amount_paise
    every_word = {"month": "monthly", "week": "weekly", "year": "yearly"}[change.every]
    amt = money(change.amount_paise)["text"]
    win = _window_text(change.start, change.until)
    head = f"Added {every_word} {amt} {desc} ({change.name}) {win}"
    label = _format_label(
        head, count, f"{money(moved)['text']} total", empty="no occurrence fell within the horizon."
    )
    return added, ChangeResult(
        "add_recurring", label, count, moved, adds_inflow=change.direction == "in" and count > 0
    )


def _apply_one_time(
    change: OneTime, as_of: date, end_date: date
) -> tuple[list[CashEvent], ChangeResult]:
    in_horizon = as_of <= change.date <= end_date
    kind, signed, desc = (
        ("income_oneoff", change.amount_paise, "inflow")
        if change.direction == "in"
        else ("obligation", -change.amount_paise, "outflow")
    )
    added = (
        [
            CashEvent(
                date=change.date,
                amount_paise=signed,
                name=change.name,
                kind=kind,
                confirmed=True,
                at_start=change.date <= as_of,
            )
        ]
        if in_horizon
        else []
    )
    amt = money(change.amount_paise)["text"]
    head = f"Added one-time {amt} {desc} ({change.name}) on {change.date.isoformat()}"
    label = f"{head}." if in_horizon else f"{head}; outside the projection horizon."
    count = 1 if in_horizon else 0
    moved = change.amount_paise if in_horizon else 0
    return added, ChangeResult(
        "one_time", label, count, moved, adds_inflow=change.direction == "in" and count > 0
    )


def apply_changes(
    inputs: ProjectionInput,
    changes: Sequence[ScenarioChangeSpec],
    *,
    income_source_ids: Collection[str] = (),
) -> ScenarioApplication:
    """Validate, canonically sort, and apply ``changes`` without mutating ``inputs``."""
    income_ids = set(income_source_ids) | {
        event.source_id
        for event in inputs.events
        if event.source_id is not None and event.kind.startswith("income")
    }
    _validate_changes(inputs.as_of, changes, income_ids)
    end_date = inputs.as_of + timedelta(days=horizon_days(inputs))
    events = list(inputs.events)
    series_list = list(inputs.series)
    added_events: list[CashEvent] = []
    results: list[ChangeResult] = []
    has_added_flow = False

    for change in sorted(changes, key=_sort_key):
        if isinstance(change, StopIncome):
            events, res = _apply_stop_income(change, events, end_date)
        elif isinstance(change, PauseSchedule):
            events, series_list, res = _apply_pause_schedule(change, events, series_list, end_date)
        elif isinstance(change, ChangeAmount):
            events, res = _apply_change_amount(change, events, end_date)
        elif isinstance(change, AddRecurring):
            has_added_flow = True
            new_events, res = _apply_add_recurring(change, inputs.as_of, end_date)
            added_events.extend(new_events)
        else:
            has_added_flow = True
            new_events, res = _apply_one_time(change, inputs.as_of, end_date)
            added_events.extend(new_events)
        results.append(res)

    return ScenarioApplication(
        inputs=replace(inputs, events=(*events, *added_events), series=tuple(series_list)),
        changes=tuple(results),
        adds_or_raises_inflow=any(item.adds_inflow for item in results),
        has_added_flow=has_added_flow,
    )


__all__ = [
    "MAX_CHANGES",
    "AddRecurring",
    "ChangeAmount",
    "ChangeResult",
    "OneTime",
    "PauseSchedule",
    "ScenarioApplication",
    "ScenarioChangeSpec",
    "StopIncome",
    "apply_changes",
    "scenario_horizon_days",
]
