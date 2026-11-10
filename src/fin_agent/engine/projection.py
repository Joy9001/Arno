"""The seeded, day-by-day cash projection.

This module is pure: it never touches the database, the clock, or the network. The database
adapter in `fin_agent.tools.projection` turns the ledger into these inputs, and the affordability
verdict in `fin_agent.engine.affordability` wraps them. Every affordability number the product
states comes through this walk.

Money is whole paise. A day's balance is the sum of liquid accounts; card spend is a liability
until its due date, when the card's outstanding plus everything charged since the last bill is
paid in full. Variable spend is resampled from real history, one day at a time, with weekday and
weekend pools kept apart so weekend clustering survives without resampling whole weeks (the L5
decision recorded in plan.md §6).
"""

from __future__ import annotations

import calendar
import random
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import NotRequired, Required, TypedDict

DEFAULT_RUNS = 1_000
DEFAULT_SEED = 20261103
FLOOR_THRESHOLD = 0.90
HISTORY_DAYS = 90
MIN_REAL_DAYS = 14
MAX_HORIZON_DAYS = 3_650
BASE_HORIZON_DAYS = 90
MAX_CUT_RATIO = 0.50

# Set-up gaps that leave a projection with nothing to project from. A backtest over a month with
# one of these measures its own missing setup, not the engine, so it may never be published.
NO_LIQUID_ACCOUNT = "no liquid account"
NO_CONFIRMED_INCOME = "no confirmed income schedule"
UNUSABLE_SETUP_GAPS = (NO_LIQUID_ACCOUNT, NO_CONFIRMED_INCOME)


class BalanceAge(TypedDict):
    """One account or card balance and how old it is, as the data-quality note reports it."""

    account: str
    id: str
    kind: str
    as_of: str | None
    age_days: int | None
    balance_paise: int
    known: bool


class BaselineSource(TypedDict):
    """The daily spend rate a category was given, and where that rate came from."""

    category: str
    id: str
    source: str
    amount_per_day_paise: int
    real_days: int
    card_share: float


class Simulation(TypedDict):
    """How the seeded walk ran, so a reader can tell sampling from a flat estimate."""

    runs: int
    seed: int
    days: int
    categories_sampled: list[str]
    categories_flat: list[str]
    credit_limit_exceeded: bool
    obligation_misses: int
    include_unconfirmed: bool
    resampling: str


class DataQuality(TypedDict, total=False):
    """What the projection could not verify. The caller supplies what it knows; the walk adds."""

    balance_ages: list[BalanceAge]
    baseline_sources: list[BaselineSource]
    gaps: list[str]
    setup_gaps: list[str]
    outside_text: bool
    simulation: Simulation


class SourceRef(TypedDict):
    """One citable thing the projection read: an account, card, schedule, goal, category, payee."""

    kind: str
    id: str
    name: str
    detail: str
    amount_paise: NotRequired[int | None]


class CurvePoint(TypedDict):
    """One day of the projected balance band."""

    date: str
    median_paise: int
    p10_paise: int
    p90_paise: int


class MedianLow(TypedDict):
    """The lowest median balance of the horizon, and the day it falls on."""

    date: str
    paise: int


class GoalRow(TypedDict):
    """One goal as the projection forecasts it, contributions inside the horizon included."""

    id: str
    name: str
    target_paise: int
    saved_paise: int
    remaining_after_plan_paise: int
    deadline: str | None
    completes_on: str | None
    slips_days: int | None
    reached: bool


class SourceIds(TypedDict):
    """The ids behind every number in the projection, grouped by what they are."""

    accounts: list[str]
    cards: list[str]
    schedules: list[str]
    payees: list[str]
    goals: list[str]
    categories: list[str]


class OptimisticIncome(TypedDict):
    """One unconfirmed income event the optimistic case adds."""

    date: str
    name: str
    amount_paise: int
    source_id: str | None


class Optimistic(TypedDict):
    """The case where unconfirmed variable income does arrive. Never the verdict's case."""

    floor_probability: float
    median_low: MedianLow
    first_floor_crossing: str | None
    income: list[OptimisticIncome]


class Installment(TypedDict):
    """One instalment of a card-EMI purchase."""

    date: str
    amount_paise: int


class HypotheticalMeta(TypedDict, total=False):
    """The purchase an affordability verdict was tested against."""

    amount_paise: int
    date: str
    method: str
    emi_months: int | None
    account: str | None
    last_cash_date: str
    card_id: str
    card_name: str
    due_date: str
    installments: list[Installment]
    total_paise: int
    available_credit_paise: int
    feasible: bool


class GoalSlip(TypedDict):
    """One goal the purchase pushes later, or off the horizon."""

    id: str
    name: str
    slips_days: int | None
    completes_on: str | None


class CategoryCutRow(TypedDict):
    """One reducible category and the daily rate a cut leaves it at."""

    category: str
    id: str
    percent: float
    baseline_per_day_paise: int
    new_baseline_per_day_paise: int
    until: str | None


class Tradeoff(TypedDict, total=False):
    """One change that keeps the purchase affordable, and what it costs."""

    kind: Required[str]
    detail: Required[str]
    date: str
    days: int
    method: str
    account: str | None
    due_date: str | None
    installments: list[Installment] | None
    goal: str
    goal_id: str
    slips_days: int | None
    completes_on: str | None
    until: str
    ratio: float
    categories: list[CategoryCutRow]


class Baseline(TypedDict):
    """The same run without the purchase, so a verdict can be read against it."""

    floor_probability: float
    median_low: MedianLow
    first_floor_crossing: str | None


class PaymentDate(TypedDict):
    """The earliest day a one-off payment clears the floor threshold."""

    date: str
    days: int
    amount_paise: int


class Projection(TypedDict):
    """One projection run: the balance band, the floor odds, and every citation behind them."""

    as_of: str
    horizon_days: int
    end: str
    floor_paise: int
    floor_set: bool
    floor_threshold: float
    floor_probability: float
    median_low: MedianLow
    p10_low_paise: int
    first_floor_crossing: str | None
    curve: list[CurvePoint]
    goals: list[GoalRow]
    obligations_met: bool
    data_quality: DataQuality
    sources: list[SourceRef]
    source_ids: SourceIds
    runs: int
    seed: int
    optimistic: Optimistic | None
    # The affordability verdict: present only when a hypothetical purchase was tested.
    verdict: NotRequired[str]
    verdict_reason: NotRequired[str]
    verdict_threshold: NotRequired[float]
    tradeoffs: NotRequired[list[Tradeoff]]
    goal_slips: NotRequired[list[GoalSlip]]
    hypothetical: NotRequired[HypotheticalMeta]
    baseline: NotRequired[Baseline]


@dataclass(frozen=True, slots=True)
class LiquidAccount:
    id: str
    name: str
    balance_paise: int
    as_of: date | None = None
    # Days since the later of the last movement and the stated balance, the age the context
    # reports; None when the balance is not known.
    balance_age_days: int | None = None
    kind: str = "bank"
    known: bool = True


@dataclass(frozen=True, slots=True)
class CardAccount:
    id: str
    name: str
    statement_day: int
    due_day: int
    outstanding_paise: int
    credit_limit_paise: int
    known: bool = True
    # A positive ledger balance is an overpayment: usable credit beyond the limit.
    credit_paise: int = 0

    @property
    def available_credit_paise(self) -> int:
        return max(self.credit_limit_paise - self.outstanding_paise + self.credit_paise, 0)


@dataclass(frozen=True, slots=True)
class CashEvent:
    """One dated cash movement. Positive is inflow; negative is outflow.

    A negative event with ``card_id`` is charged to that card and paid on its next bill; it does
    not leave the liquid balance on the event date. A negative event with ``goal_id`` is a goal
    contribution: it leaves liquid cash and adds to that goal's fenced balance.
    """

    date: date
    amount_paise: int
    name: str
    kind: str
    source_id: str | None = None
    confirmed: bool = True
    goal_id: str | None = None
    card_id: str | None = None
    at_start: bool = False


@dataclass(frozen=True, slots=True)
class GoalPlan:
    id: str
    name: str
    target_paise: int
    saved_paise: int
    deadline: date | None = None
    contribution_schedule_id: str | None = None
    priority: int = 3


@dataclass(frozen=True, slots=True)
class SpendCategory:
    id: str
    name: str
    baseline_per_day_paise: int
    baseline_source: str = "estimated"
    reducible: bool = False
    card_share: float = 0.0
    card_id: str | None = None
    history: tuple[tuple[date, int], ...] = ()
    covered_by_obligation: bool = False
    # A tradeoff cut: spend (1 - reduce_ratio) of the baseline until reduce_until, then the full
    # baseline. A cut "until payday" must not keep cutting for the rest of the horizon.
    reduce_ratio: float = 0.0
    reduce_until: date | None = None


@dataclass(frozen=True, slots=True)
class ProjectionInput:
    as_of: date
    floor_paise: int
    liquid: tuple[LiquidAccount, ...] = ()
    events: tuple[CashEvent, ...] = ()
    cards: tuple[CardAccount, ...] = ()
    goals: tuple[GoalPlan, ...] = ()
    categories: tuple[SpendCategory, ...] = ()
    ask_horizon_days: int | None = None
    data_quality: DataQuality = field(default_factory=DataQuality)
    sources: tuple[SourceRef, ...] = ()

    @property
    def starting_balance_paise(self) -> int:
        return sum(account.balance_paise for account in self.liquid)


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _clamped_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_on_or_after(day: date, wanted_day: int) -> date:
    candidate = _clamped_day(day.year, day.month, wanted_day)
    if candidate >= day:
        return candidate
    if day.month == 12:
        return _clamped_day(day.year + 1, 1, wanted_day)
    return _clamped_day(day.year, day.month + 1, wanted_day)


def statement_close(card: CardAccount, spend_date: date) -> date:
    """The date the statement holding ``spend_date`` closes.

    A card's bill closes on its statement day; spend after that day closes on the next one. The
    due date in `card_due_date` follows from this date, and the write and read tools quote both
    from here, so no card bill date can drift from the projection.
    """
    return _next_on_or_after(spend_date, card.statement_day)


def card_due_date(card: CardAccount, spend_date: date) -> date:
    """The date the bill containing ``spend_date`` is paid in full.

    Spend closes on the first statement day on or after it. The bill is then due on the next due
    day after that statement date, which may fall in the following month when the statement day is
    later than the due day, or when a short month clamps the due day onto the close itself.
    """
    close = statement_close(card, spend_date)
    due = _clamped_day(close.year, close.month, card.due_day)
    if due > close:
        return due
    next_month = _add_months(date(close.year, close.month, 1), 1)
    return _clamped_day(next_month.year, next_month.month, card.due_day)


def _first_due_on_or_after(card: CardAccount, day: date) -> date:
    due = _clamped_day(day.year, day.month, card.due_day)
    if due >= day:
        return due
    next_month = _add_months(date(day.year, day.month, 1), 1)
    return _clamped_day(next_month.year, next_month.month, card.due_day)


def horizon_days(inputs: ProjectionInput) -> int:
    """max(90 days, the furthest goal deadline, the asked horizon), capped for sanity."""
    asked = inputs.ask_horizon_days if inputs.ask_horizon_days is not None else 0
    furthest = max(
        ((goal.deadline - inputs.as_of).days for goal in inputs.goals if goal.deadline is not None),
        default=0,
    )
    return max(1, min(MAX_HORIZON_DAYS, max(BASE_HORIZON_DAYS, asked, furthest)))


def percentile(values: list[int], q: float) -> int:
    """Linear-interpolated percentile, so 1,000 runs give a stable P10 and P90."""
    if len(values) == 0:
        return 0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return int(round(ordered[low] + (ordered[high] - ordered[low]) * fraction))


def _dense_history(category: SpendCategory) -> list[tuple[date, int]]:
    if len(category.history) == 0:
        return []
    by_day = {day: int(amount) for day, amount in category.history}
    start = min(by_day)
    end = max(by_day)
    days: list[tuple[date, int]] = []
    cursor = start
    while cursor <= end:
        days.append((cursor, max(0, by_day.get(cursor, 0))))
        cursor += timedelta(days=1)
    return days


def _pools(category: SpendCategory) -> tuple[list[int], list[int], list[int], bool]:
    """Weekday, weekend, and all-day pools. The fourth item says whether real history is used."""
    history = _dense_history(category)
    real_days = sum(1 for _day, amount in history if amount > 0)
    if real_days < MIN_REAL_DAYS:
        return [], [], [], False
    weekday: list[int] = []
    weekend: list[int] = []
    for day, amount in history:
        (weekend if day.weekday() >= 5 else weekday).append(amount)
    return weekday, weekend, [amount for _day, amount in history], True


def _simulate(
    inputs: ProjectionInput,
    *,
    runs: int,
    seed: int,
    include_unconfirmed: bool = False,
) -> tuple[list[list[int]], Simulation]:
    """Walk every run and return balances by day (index 0 is as_of)."""
    days = horizon_days(inputs)
    start = inputs.as_of
    end = start + timedelta(days=days)

    events_by_day: dict[date, list[CashEvent]] = {}
    start_events: list[CashEvent] = []
    for event in inputs.events:
        if not event.confirmed and not include_unconfirmed:
            continue
        if event.at_start or event.date <= start:
            start_events.append(event)
        elif event.date <= end:
            events_by_day.setdefault(event.date, []).append(event)

    cards = {card.id: card for card in inputs.cards}
    card_pending: dict[tuple[str, date], int] = {}
    card_initial_due: dict[str, date] = {}
    for card in inputs.cards:
        if card.outstanding_paise > 0:
            due = _first_due_on_or_after(card, start)
            if due <= end:
                card_initial_due[card.id] = due

    category_pools = {category.id: _pools(category) for category in inputs.categories}
    sampled: list[str] = []
    flat: list[str] = []
    for category in inputs.categories:
        weekday, weekend, _all, real = category_pools[category.id]
        (sampled if real else flat).append(category.name)

    paths: list[list[int]] = []
    missed_obligation_runs = 0
    credit_exceeded = False

    for run in range(runs):
        rng = random.Random(f"{seed}:{run}")
        balance = inputs.starting_balance_paise
        goal_saved = {goal.id: goal.saved_paise for goal in inputs.goals}
        paid_initial: set[str] = set()

        def charge_card(
            card_id: str,
            amount: int,
            when: date,
            paid_initial: set[str] = paid_initial,
        ) -> int:
            """Add a charge to the card's bill, returning any part that must be paid in cash."""
            nonlocal credit_exceeded
            card = cards[card_id]
            pending = sum(
                value
                for (pending_card, _due), value in card_pending.items()
                if pending_card == card_id
            )
            outstanding = 0 if card_id in paid_initial else card.outstanding_paise
            used = outstanding - card.credit_paise + pending
            if used + amount > card.credit_limit_paise:
                excess = used + amount - card.credit_limit_paise
                credit_exceeded = True
                amount -= excess
                due = card_due_date(card, when)
                if due <= end:
                    card_pending[(card_id, due)] = card_pending.get((card_id, due), 0) + amount
                return excess
            due = card_due_date(card, when)
            if due <= end:
                card_pending[(card_id, due)] = card_pending.get((card_id, due), 0) + amount
            return 0

        def apply_event(event: CashEvent, goal_saved: dict[str, int] = goal_saved) -> None:
            nonlocal balance
            amount = event.amount_paise
            if event.card_id is not None and amount < 0:
                balance -= charge_card(event.card_id, -amount, max(event.date, start))
                return
            if event.goal_id is not None and amount < 0:
                spent = -amount
                balance -= spent
                if event.goal_id in goal_saved:
                    goal_saved[event.goal_id] += spent
                return
            balance += amount

        # A bill already due today is paid today; the first curve point is after it.
        for card in inputs.cards:
            if card_initial_due.get(card.id) == start and card.id not in paid_initial:
                balance -= card.outstanding_paise
                paid_initial.add(card.id)
        for event in start_events:
            apply_event(event)
        path: list[int] = [balance]

        for offset in range(1, days + 1):
            day = start + timedelta(days=offset)
            day_events = events_by_day.get(day, [])

            # 1. Income, including money owed to the user that is expected today.
            for event in day_events:
                if event.kind.startswith("income") or event.kind == "owed_receipt":
                    if event.amount_paise > 0:
                        apply_event(event)

            # 2. Obligations and installments. A card-charged bill is added to the card.
            obligation_today = False
            for event in day_events:
                if event.kind in {"obligation", "instalment", "owed_payment"}:
                    obligation_today = True
                    apply_event(event)

            # 3. Card dues: the initial outstanding on the first due date, plus every charge
            #    belonging to this bill.
            for card in inputs.cards:
                if card_initial_due.get(card.id) == day and card.id not in paid_initial:
                    balance -= card.outstanding_paise
                    paid_initial.add(card.id)
                due_amount = card_pending.pop((card.id, day), 0)
                if due_amount != 0:
                    balance -= due_amount

            if obligation_today and balance < 0:
                missed_obligation_runs += 1

            # 3b. A one-off modelled purchase (the affordability hypothetical) lands on its date.
            for event in day_events:
                if event.kind in {"hypothetical", "hypothetical_emi"}:
                    apply_event(event)

            # 4. Goal contributions: liquid cash moves into a non-liquid goal account.
            for event in day_events:
                if event.kind == "goal_contribution":
                    apply_event(event)

            # 5. Variable spend, resampled one day at a time.
            for category in inputs.categories:
                weekday, weekend, all_days, real = category_pools[category.id]
                if real:
                    pool = weekend if day.weekday() >= 5 else weekday
                    if len(pool) == 0:
                        pool = all_days
                    amount = rng.choice(pool)
                else:
                    amount = category.baseline_per_day_paise
                if amount <= 0:
                    continue
                if category.reduce_ratio > 0 and (
                    category.reduce_until is None or day <= category.reduce_until
                ):
                    amount = int(round(amount * (1 - category.reduce_ratio)))
                    if amount <= 0:
                        continue
                card_part = int(round(amount * category.card_share))
                card_part = max(0, min(card_part, amount))
                cash_part = amount - card_part
                balance -= cash_part
                if card_part != 0:
                    card = (
                        cards.get(category.card_id)
                        if category.card_id is not None
                        else next(iter(inputs.cards), None)
                    )
                    if card is None:
                        balance -= card_part
                    else:
                        balance -= charge_card(card.id, card_part, day)

            path.append(balance)
        paths.append(path)

    return paths, {
        "runs": runs,
        "seed": seed,
        "days": days,
        "categories_sampled": sampled,
        "categories_flat": flat,
        "credit_limit_exceeded": credit_exceeded,
        "obligation_misses": missed_obligation_runs,
        "include_unconfirmed": include_unconfirmed,
        "resampling": "single-day, stratified weekday/weekend",
    }


def _goal_rows(inputs: ProjectionInput) -> list[GoalRow]:
    contributions: dict[str, list[CashEvent]] = {}
    for event in inputs.events:
        if event.kind == "goal_contribution" and event.goal_id is not None:
            contributions.setdefault(event.goal_id, []).append(event)
    rows: list[GoalRow] = []
    for goal in sorted(inputs.goals, key=lambda item: (item.priority, item.name)):
        saved = goal.saved_paise
        completes: date | None = inputs.as_of if saved >= goal.target_paise else None
        for event in sorted(contributions.get(goal.id, []), key=lambda item: item.date):
            if event.date <= inputs.as_of:
                continue
            saved += abs(event.amount_paise)
            if completes is None and saved >= goal.target_paise:
                completes = event.date
        slips: int | None = None
        if completes is not None and goal.deadline is not None:
            slips = max(0, (completes - goal.deadline).days)
        rows.append(
            {
                "id": goal.id,
                "name": goal.name,
                "target_paise": goal.target_paise,
                "saved_paise": goal.saved_paise,
                # Target minus saved minus the contributions planned inside the horizon. Named
                # apart from a goal card's remaining_paise (target minus saved) so the two
                # readings can never be presented as a contradiction.
                "remaining_after_plan_paise": max(goal.target_paise - saved, 0),
                "deadline": goal.deadline.isoformat() if goal.deadline is not None else None,
                "completes_on": completes.isoformat() if completes is not None else None,
                "slips_days": slips,
                "reached": completes is not None,
            }
        )
    return rows


def _summary(
    inputs: ProjectionInput,
    paths: list[list[int]],
    *,
    runs: int,
    seed: int,
    include_unconfirmed: bool,
    simulation: Simulation,
) -> Projection:
    days = horizon_days(inputs)
    floor = inputs.floor_paise
    curve: list[CurvePoint] = []
    for index in range(days + 1):
        values = [path[index] for path in paths]
        curve.append(
            {
                "date": (inputs.as_of + timedelta(days=index)).isoformat(),
                "median_paise": percentile(values, 0.50),
                "p10_paise": percentile(values, 0.10),
                "p90_paise": percentile(values, 0.90),
            }
        )
    run_minima = [min(path) for path in paths]
    floor_probability = sum(1 for value in run_minima if value >= floor) / runs if runs > 0 else 0.0
    median_curve = [point["median_paise"] for point in curve]
    lowest = min(median_curve)
    lowest_index = median_curve.index(lowest)
    first_crossing = next(
        (point["date"] for point in curve if point["median_paise"] < floor),
        None,
    )
    data_quality: DataQuality = {**inputs.data_quality}
    recorded_gaps = data_quality.get("gaps")
    existing_gaps = list(recorded_gaps) if recorded_gaps is not None else []
    if simulation["credit_limit_exceeded"]:
        existing_gaps.append(
            "projected card spend exceeds the card limit; the excess is treated as cash spend"
        )
    data_quality["gaps"] = existing_gaps
    data_quality["simulation"] = simulation

    source_ids: SourceIds = {
        "accounts": sorted({account.id for account in inputs.liquid}),
        "cards": sorted({card.id for card in inputs.cards}),
        "schedules": sorted(
            {
                event.source_id
                for event in inputs.events
                if event.source_id is not None
                and event.confirmed
                and not event.kind.startswith("owed_")
            }
        ),
        "payees": sorted(
            {
                event.source_id
                for event in inputs.events
                if event.source_id is not None and event.kind.startswith("owed_")
            }
        ),
        "goals": sorted({goal.id for goal in inputs.goals}),
        "categories": sorted({category.id for category in inputs.categories}),
    }
    return {
        "as_of": inputs.as_of.isoformat(),
        "horizon_days": days,
        "end": (inputs.as_of + timedelta(days=days)).isoformat(),
        "floor_paise": floor,
        "floor_set": floor > 0,
        "floor_threshold": FLOOR_THRESHOLD,
        "floor_probability": round(floor_probability, 4),
        "median_low": {
            "date": curve[lowest_index]["date"],
            "paise": lowest,
        },
        "p10_low_paise": percentile(run_minima, 0.10),
        "first_floor_crossing": first_crossing,
        "curve": curve,
        "goals": _goal_rows(inputs),
        "obligations_met": simulation["obligation_misses"] == 0,
        "data_quality": data_quality,
        "sources": list(inputs.sources),
        "source_ids": source_ids,
        "runs": runs,
        "seed": seed,
        "optimistic": None,
    }


def project(
    inputs: ProjectionInput,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> Projection:
    """Run the seeded projection and return the curve, floor probability, and data quality."""
    if runs < 1:
        raise ValueError("runs must be positive")
    paths, simulation = _simulate(inputs, runs=runs, seed=seed, include_unconfirmed=False)
    result = _summary(
        inputs,
        paths,
        runs=runs,
        seed=seed,
        include_unconfirmed=False,
        simulation=simulation,
    )
    unconfirmed = [
        event
        for event in inputs.events
        if not event.confirmed and event.amount_paise > 0 and event.kind.startswith("income")
    ]
    if len(unconfirmed) > 0:
        optimistic_paths, optimistic_simulation = _simulate(
            inputs, runs=runs, seed=seed, include_unconfirmed=True
        )
        optimistic = _summary(
            inputs,
            optimistic_paths,
            runs=runs,
            seed=seed,
            include_unconfirmed=True,
            simulation=optimistic_simulation,
        )
        result["optimistic"] = {
            "floor_probability": optimistic["floor_probability"],
            "median_low": optimistic["median_low"],
            "first_floor_crossing": optimistic["first_floor_crossing"],
            "income": [
                {
                    "date": event.date.isoformat(),
                    "name": event.name,
                    "amount_paise": event.amount_paise,
                    "source_id": event.source_id,
                }
                for event in sorted(unconfirmed, key=lambda item: item.date)
            ],
        }
        result["data_quality"]["gaps"].append(
            "unconfirmed variable income is shown only as an optimistic case and is not counted "
            "toward the verdict"
        )
    return result


def simulate_paths(
    inputs: ProjectionInput,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> list[list[int]]:
    paths, _simulation = _simulate(inputs, runs=runs, seed=seed, include_unconfirmed=False)
    return paths


def safe_amount_today(
    inputs: ProjectionInput,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> int:
    """The largest one-off payment today that keeps the floor probability at the threshold."""
    paths = simulate_paths(inputs, runs=runs, seed=seed)
    run_minima = [min(path) for path in paths]
    headroom = percentile(run_minima, 1 - FLOOR_THRESHOLD) - inputs.floor_paise
    return max(0, headroom)


def first_full_payment_date(
    inputs: ProjectionInput,
    amount_paise: int,
    *,
    start_on: date | None = None,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> PaymentDate | None:
    """Earliest date a single payment of ``amount_paise`` clears the floor threshold.

    A one-off payment only shifts every later day by the same amount, so prefix and suffix minima
    answer every candidate date in one pass over the simulated paths.
    """
    if amount_paise <= 0:
        return None
    paths = simulate_paths(inputs, runs=runs, seed=seed)
    days = len(paths[0]) - 1
    start_index = 0
    if start_on is not None:
        start_index = max(0, (start_on - inputs.as_of).days)
    prefix_minima: list[list[int]] = []
    suffix_minima: list[list[int]] = []
    for path in paths:
        prefix = [0] * (days + 1)
        running = path[0]
        for index in range(days + 1):
            running = min(running, path[index])
            prefix[index] = running
        prefix_minima.append(prefix)
        minima = [0] * (days + 1)
        running = path[days]
        for index in range(days, -1, -1):
            running = min(running, path[index])
            minima[index] = running
        suffix_minima.append(minima)

    for index in range(start_index, days + 1):
        if index == 0:
            values = [suffix_minima[run][0] - amount_paise for run in range(len(paths))]
        else:
            values = [
                min(
                    prefix_minima[run][index - 1],
                    suffix_minima[run][index] - amount_paise,
                )
                for run in range(len(paths))
            ]
        if percentile(values, 1 - FLOOR_THRESHOLD) >= inputs.floor_paise:
            found = inputs.as_of + timedelta(days=index)
            return {
                "date": found.isoformat(),
                "days": index,
                "amount_paise": amount_paise,
            }
    return None


def reduce_categories(
    inputs: ProjectionInput, ratio: float, *, until: date | None = None
) -> tuple[ProjectionInput, list[CategoryCutRow]]:
    """Spend ``ratio`` less on reducible categories, going back to normal after ``until``.

    The ratio is applied to each simulated day rather than to the history, so a cut that runs
    "until payday" raises the baseline again on the days after it. The rows still report the
    daily rates the user is agreeing to.
    """
    ratio = max(0.0, min(ratio, MAX_CUT_RATIO))
    rows: list[CategoryCutRow] = []
    categories: list[SpendCategory] = []
    for category in inputs.categories:
        if not category.reducible or category.baseline_per_day_paise <= 0:
            categories.append(category)
            continue
        baseline = int(round(category.baseline_per_day_paise * (1 - ratio)))
        categories.append(replace(category, reduce_ratio=ratio, reduce_until=until))
        rows.append(
            {
                "category": category.name,
                "id": category.id,
                "percent": round(ratio * 100, 1),
                "baseline_per_day_paise": category.baseline_per_day_paise,
                "new_baseline_per_day_paise": baseline,
                "until": until.isoformat() if until is not None else None,
            }
        )
    return replace(inputs, categories=tuple(categories)), rows


def _add_months_public(day: date, months: int) -> date:
    return _add_months(day, months)


__all__ = [
    "BASE_HORIZON_DAYS",
    "DEFAULT_RUNS",
    "DEFAULT_SEED",
    "FLOOR_THRESHOLD",
    "HISTORY_DAYS",
    "MAX_CUT_RATIO",
    "MAX_HORIZON_DAYS",
    "MIN_REAL_DAYS",
    "CardAccount",
    "CashEvent",
    "GoalPlan",
    "LiquidAccount",
    "ProjectionInput",
    "SpendCategory",
    "card_due_date",
    "first_full_payment_date",
    "horizon_days",
    "percentile",
    "project",
    "reduce_categories",
    "safe_amount_today",
    "simulate_paths",
    "statement_close",
]
