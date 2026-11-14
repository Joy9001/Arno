"""The cash projection: thousands of seeded futures, and every statistic from the same ones.

This module is pure: it never touches the database, the clock, or the network. The database
adapter in `arno.tools.projection` turns the ledger into these inputs, and the affordability
verdict in `arno.engine.affordability` wraps them. Every affordability number the product
states comes through `project`.

Money is whole paise. A day's balance is the sum of liquid accounts. Card spend is a liability:
it is charged up to the card's limit (the rest leaves cash that day) and leaves cash when its
bill falls due. `Model` says how everyday spend is drawn from history; each of its switches is
admitted only if it beats the one before it on the backtest. `arno.engine.kernel` does the
arithmetic for all scenarios at once.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import lru_cache
from typing import NotRequired, Required, TypedDict

import numpy as np

from arno.engine import kernel

DEFAULT_RUNS = 16_384
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
    """How the scenarios ran, so a reader can tell sampling from a flat estimate."""

    runs: int
    seed: int
    days: int
    categories_sampled: list[str]
    categories_flat: list[str]
    credit_limit_exceeded: bool
    obligation_misses: int
    include_unconfirmed: bool
    resampling: str
    floor_interval: NotRequired[list[float]]
    floor_shortfall_paise: NotRequired[int]


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
    # Spend since the last statement closed, due with the next bill. None: the split is
    # unknown and the whole outstanding is due on the first due date.
    unbilled_paise: int | None = None

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


@lru_cache(maxsize=8192)
def _due_date(statement_day: int, due_day: int, spend_date: date) -> date:
    # A walk asks for the same few dozen answers hundreds of thousands of times.
    close = _next_on_or_after(spend_date, statement_day)
    due = _clamped_day(close.year, close.month, due_day)
    if due > close:
        return due
    next_month = _add_months(date(close.year, close.month, 1), 1)
    return _clamped_day(next_month.year, next_month.month, due_day)


def card_due_date(card: CardAccount, spend_date: date) -> date:
    """The date the bill containing ``spend_date`` is paid in full.

    Spend closes on the first statement day on or after it. The bill is then due on the next due
    day after that statement date, which may fall in the following month when the statement day is
    later than the due day, or when a short month clamps the due day onto the close itself.
    """
    return _due_date(card.statement_day, card.due_day, spend_date)


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


@dataclass(frozen=True, slots=True)
class Model:
    """How everyday spend is drawn. Each field is one switch the backtest admits or rejects.

    The defaults are the walk shipped before the scenario kernel: one independent draw per
    category per day from that category's 90-day pool, weekday and weekend apart, and a flat daily
    rate for a category with too little history.
    """

    pooling: str = "category"  # "category": one draw per category; "day": whole historical days
    pool_resampling: bool = False
    block_length: int = 1
    recency_half_life_days: int | None = None
    rare_mad_multiple: int | None = None
    prior_quarters: tuple[int, ...] | None = None


LEGACY_MODEL = Model()
MODEL = LEGACY_MODEL
OBLIGATION_THRESHOLD = 0.99
CROSSING_SHARE = 0.10
_OBLIGATION_KINDS = frozenset({"obligation", "instalment", "owed_payment"})
_LATER_KINDS = frozenset({"hypothetical", "hypothetical_emi", "goal_contribution"})


def _day_index(as_of: date, day: date, days: int) -> int:
    offset = (day - as_of).days
    return offset if 0 <= offset <= days else -1


def last_statement_close(statement_day: int, as_of: date) -> date:
    """The last statement close on or before ``as_of``: spend after it joins the next bill."""
    close = _next_on_or_after(as_of, statement_day)
    if close == as_of:
        return close
    previous = _add_months(date(close.year, close.month, 1), -1)
    return _clamped_day(previous.year, previous.month, statement_day)


def _card_spec(card: CardAccount, as_of: date, days: int, total: int) -> kernel.CardSpec:
    due_of_day = np.fromiter(
        (
            _day_index(as_of, card_due_date(card, as_of + timedelta(days=t)), days)
            for t in range(total)
        ),
        dtype=np.int64,
        count=total,
    )
    bills: dict[int, int] = {}
    outstanding = card.outstanding_paise
    if outstanding > 0:
        if card.unbilled_paise is None:
            # With no split known, the whole balance is due on the first due date.
            due = _day_index(as_of, _first_due_on_or_after(card, as_of), days)
            if due >= 0:
                bills[due] = outstanding
        else:
            unbilled = max(0, min(card.unbilled_paise, outstanding))
            billed = outstanding - unbilled
            if billed > 0:
                # An unpaid closed bill is due on its due date, or today if that has passed.
                due_day = card_due_date(card, last_statement_close(card.statement_day, as_of))
                due = 0 if due_day < as_of else _day_index(as_of, due_day, days)
                if due >= 0:
                    bills[due] = bills.get(due, 0) + billed
            if unbilled > 0 and int(due_of_day[0]) >= 0:
                first = int(due_of_day[0])
                bills[first] = bills.get(first, 0) + unbilled
    return kernel.CardSpec(
        limit=card.credit_limit_paise,
        used=outstanding - card.credit_paise,
        due_of_day=due_of_day,
        bills=bills,
        obligation=np.zeros(total, dtype=np.int64),
        other=np.zeros(total, dtype=np.int64),
    )


def _category_pool(
    category: SpendCategory, card_index: int | None, stream: int
) -> kernel.Pool | None:
    weekday, weekend, all_days, real = _pools(category)
    if real:
        amounts = all_days
        classes = [
            kernel.WEEKEND if day.weekday() >= 5 else kernel.WEEKDAY
            for day, _amount in _dense_history(category)
        ]
    else:
        amounts = [category.baseline_per_day_paise] * 2
        classes = [kernel.WEEKDAY, kernel.WEEKEND]
    del weekday, weekend
    if not any(amount > 0 for amount in amounts):
        return None
    values = np.maximum(np.array(amounts, dtype=np.int64), 0)
    if card_index is None:
        card = np.zeros_like(values)
    else:
        card = np.array(
            [
                max(0, min(int(round(int(value) * category.card_share)), int(value)))
                for value in values
            ],
            dtype=np.int64,
        )
    cash = values - card
    columns: dict[str, kernel.I64] = {"cash": cash}
    if card_index is not None:
        columns[f"card{card_index}"] = card
    if category.reducible:
        columns["cash_cut"] = cash
        if card_index is not None:
            columns[f"card{card_index}_cut"] = card
    return kernel.Pool(columns=columns, classes=np.array(classes, dtype=np.int8), stream=stream)


@dataclass(frozen=True, slots=True)
class _Prepared:
    spec: kernel.Spec
    sampled: list[str]
    flat: list[str]


def _prepare(inputs: ProjectionInput, model: Model) -> _Prepared:
    days = horizon_days(inputs)
    total = days + 1
    as_of = inputs.as_of
    first_day = as_of.toordinal()
    classes = np.array(
        [
            kernel.WEEKEND if (as_of + timedelta(days=t)).weekday() >= 5 else kernel.WEEKDAY
            for t in range(total)
        ],
        dtype=np.int8,
    )
    inflow = np.zeros(total, dtype=np.int64)
    obligation = np.zeros(total, dtype=np.int64)
    later = np.zeros(total, dtype=np.int64)
    unconfirmed = np.zeros(total, dtype=np.int64)
    obligation_days: set[int] = set()
    card_index = {card.id: index for index, card in enumerate(inputs.cards)}
    cards = [_card_spec(card, as_of, days, total) for card in inputs.cards]

    for event in inputs.events:
        start = event.at_start or event.date <= as_of
        index = 0 if start else _day_index(as_of, event.date, days)
        if index < 0:
            continue
        amount = event.amount_paise
        charged = event.card_id if amount < 0 and event.card_id in card_index else None
        if not event.confirmed:
            if amount > 0 and event.kind.startswith("income"):
                unconfirmed[index] += amount
            continue
        if start:
            # Today's events all apply, whatever their kind; a card charge joins its bill.
            if charged is not None:
                other = cards[card_index[charged]].other
                assert other is not None
                other[0] += -amount
            elif amount > 0:
                inflow[0] += amount
            else:
                later[0] += -amount
            continue
        kind = event.kind
        if kind.startswith("income") or kind == "owed_receipt":
            if amount > 0:
                inflow[index] += amount
        elif kind in _OBLIGATION_KINDS:
            obligation_days.add(index)
            if charged is not None:
                owed = cards[card_index[charged]].obligation
                assert owed is not None
                owed[index] += -amount
            elif amount > 0:
                inflow[index] += amount
            else:
                obligation[index] += -amount
        elif kind in _LATER_KINDS:
            if charged is not None:
                other = cards[card_index[charged]].other
                assert other is not None
                other[index] += -amount
            elif amount > 0:
                inflow[index] += amount
            else:
                later[index] += -amount

    pools: list[kernel.Pool] = []
    sampled: list[str] = []
    flat: list[str] = []
    for stream, category in enumerate(inputs.categories, start=1):
        real = _pools(category)[3]
        (sampled if real else flat).append(category.name)
        # The card a category's share goes to, as the walk always chose it: its own card, else
        # the first card; a share with no card at all, or an unknown card, is cash.
        if category.card_share <= 0 or len(inputs.cards) == 0:
            index: int | None = None
        elif category.card_id is None:
            index = 0
        else:
            index = card_index.get(category.card_id)
        pool = _category_pool(category, index, stream)
        if pool is not None:
            pools.append(pool)

    cut_bps = 0
    cut_until = -1
    for category in inputs.categories:
        if category.reduce_ratio > 0:
            cut_bps = int(round(category.reduce_ratio * 10_000))
            cut_until = (
                days
                if category.reduce_until is None
                else min(days, (category.reduce_until - as_of).days)
            )
            break

    spec = kernel.Spec(
        start_balance=inputs.starting_balance_paise,
        days=days,
        first_day=first_day,
        classes=classes,
        inflow=inflow,
        obligation=obligation,
        later=later,
        obligation_days=np.array(sorted(obligation_days), dtype=np.int64),
        unconfirmed=unconfirmed,
        floor=inputs.floor_paise,
        cards=tuple(cards),
        pools=tuple(pools),
        cut_bps=cut_bps,
        cut_until=cut_until,
    )
    return _Prepared(spec=spec, sampled=sampled, flat=flat)


def _rank(count: int, share: float) -> int:
    """The 0-based index of the order statistic at rank ceil(share * count)."""
    return max(1, math.ceil(share * count)) - 1


def _first_crossing(as_of: date, crossings: kernel.I64, scenarios: int) -> str | None:
    # More than 10% of scenarios below the floor: absent exactly when the floor probability passes.
    over = np.flatnonzero(crossings * 10 > scenarios)
    return (as_of + timedelta(days=int(over[0]))).isoformat() if len(over) > 0 else None


def _median_low(as_of: date, medians: kernel.I64) -> MedianLow:
    index = int(np.argmin(medians))
    return {"date": (as_of + timedelta(days=index)).isoformat(), "paise": int(medians[index])}


def _summary(
    inputs: ProjectionInput, prepared: _Prepared, run: kernel.Run, seed: int
) -> Projection:
    days = run.days
    floor = inputs.floor_paise
    scenarios = run.scenarios
    as_of = inputs.as_of
    curve: list[CurvePoint] = [
        {
            "date": (as_of + timedelta(days=index)).isoformat(),
            "median_paise": int(run.curve[index, 1]),
            "p10_paise": int(run.curve[index, 0]),
            "p90_paise": int(run.curve[index, 2]),
        }
        for index in range(days + 1)
    ]
    held = int(np.count_nonzero(run.minima >= floor))
    low_rank = _rank(scenarios, 0.1)
    floor_probability = held / scenarios
    low, high = _wilson(held, scenarios)
    breaching = run.minima[run.minima < floor]
    misses = int(np.count_nonzero(run.obligation_failures * 100 > scenarios))
    simulation: Simulation = {
        "runs": scenarios,
        "seed": seed,
        "days": days,
        "categories_sampled": prepared.sampled,
        "categories_flat": prepared.flat,
        "credit_limit_exceeded": run.credit_exceeded,
        "obligation_misses": misses,
        "include_unconfirmed": False,
        "resampling": _describe(MODEL),
        "floor_interval": [round(low, 4), round(high, 4)],
        "floor_shortfall_paise": int(floor - breaching.mean()) if len(breaching) > 0 else 0,
    }
    data_quality: DataQuality = {**inputs.data_quality}
    recorded = data_quality.get("gaps")
    gaps = list(recorded) if recorded is not None else []
    if run.credit_exceeded:
        gaps.append(
            "projected card spend exceeds the card limit; the excess is treated as cash spend"
        )
    if low < FLOOR_THRESHOLD <= high:
        gaps.append(
            f"borderline: the floor probability's 95% interval ({low:.1%} to {high:.1%}) "
            f"contains the {FLOOR_THRESHOLD:.0%} threshold"
        )
    data_quality["gaps"] = gaps
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
    result: Projection = {
        "as_of": as_of.isoformat(),
        "horizon_days": days,
        "end": (as_of + timedelta(days=days)).isoformat(),
        "floor_paise": floor,
        "floor_set": floor > 0,
        "floor_threshold": FLOOR_THRESHOLD,
        "floor_probability": round(floor_probability, 4),
        "median_low": _median_low(as_of, run.curve[:, 1]),
        "p10_low_paise": int(np.partition(run.minima, low_rank)[low_rank]),
        "first_floor_crossing": _first_crossing(as_of, run.crossings, scenarios),
        "curve": curve,
        "goals": _goal_rows(inputs),
        "obligations_met": misses == 0,
        "data_quality": data_quality,
        "sources": list(inputs.sources),
        "source_ids": source_ids,
        "runs": scenarios,
        "seed": seed,
        "optimistic": None,
    }
    unconfirmed = [
        event
        for event in inputs.events
        if not event.confirmed and event.amount_paise > 0 and event.kind.startswith("income")
    ]
    optimistic_minima, optimistic_crossings = run.optimistic_minima, run.optimistic_crossings
    if optimistic_minima is not None and optimistic_crossings is not None and len(unconfirmed) > 0:
        shifted = run.curve[:, 1] + np.cumsum(prepared.spec.unconfirmed)
        result["optimistic"] = {
            "floor_probability": round(
                int(np.count_nonzero(optimistic_minima >= floor)) / scenarios, 4
            ),
            "median_low": _median_low(as_of, shifted),
            "first_floor_crossing": _first_crossing(as_of, optimistic_crossings, scenarios),
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
        gaps.append(
            "unconfirmed variable income is shown only as an optimistic case and is not counted "
            "toward the verdict"
        )
    return result


def _wilson(successes: int, n: int) -> tuple[float, float]:
    z = 1.959963984540054
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _describe(model: Model) -> str:
    parts = ["whole-day vectors" if model.pooling == "day" else "one draw per category"]
    parts.append("weekday/weekend matched")
    if model.block_length > 1:
        parts.append(f"blocks of about {model.block_length} days")
    if model.pool_resampling:
        parts.append("per-scenario pool resampling")
    if model.recency_half_life_days is not None:
        parts.append(f"recency half-life {model.recency_half_life_days} days")
    if model.rare_mad_multiple is not None:
        parts.append("rare large days as their own stream")
    return ", ".join(parts)


def _run(
    inputs: ProjectionInput,
    *,
    runs: int,
    seed: int,
    keep_days: int = 0,
    checkpoints: tuple[int, ...] = (),
    model: Model | None = None,
) -> tuple[_Prepared, kernel.Run]:
    if runs < 1:
        raise ValueError("runs must be positive")
    chosen = model if model is not None else MODEL
    prepared = _prepare(inputs, chosen)
    settings = kernel.Settings(
        scenarios=runs,
        seed=seed,
        block_length=chosen.block_length,
        keep_days=keep_days,
        checkpoints=checkpoints,
    )
    return prepared, kernel.run(prepared.spec, settings)


def project(
    inputs: ProjectionInput,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    model: Model | None = None,
) -> Projection:
    """Run the scenario kernel and return the curve, floor probability, and data quality."""
    prepared, run = _run(inputs, runs=runs, seed=seed, model=model)
    return _summary(inputs, prepared, run, seed)


def simulate_paths(
    inputs: ProjectionInput,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    days: int | None = None,
    model: Model | None = None,
) -> kernel.I64:
    """Every scenario's daily balance for the first ``days`` days (the whole horizon by default)."""
    keep = (horizon_days(inputs) + 1) if days is None else days + 1
    _prepared, run = _run(inputs, runs=runs, seed=seed, keep_days=keep, model=model)
    assert run.paths is not None
    return run.paths


def safe_amount_today(
    inputs: ProjectionInput,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> int:
    """The largest one-off payment today that keeps the floor probability at the threshold."""
    _prepared, run = _run(inputs, runs=runs, seed=seed)
    rank = _rank(run.scenarios, 1 - FLOOR_THRESHOLD)
    headroom = int(np.partition(run.minima, rank)[rank]) - inputs.floor_paise
    return max(0, headroom)


def _checkpoints(spec: kernel.Spec, extra: tuple[int, ...] = ()) -> tuple[int, ...]:
    """The days the horizon's minimum can fall on: before any inflow, and the last day.

    Spend and bills only lower the balance, so it can rise only on a day with an inflow; the
    minimum over any span ending at a checkpoint is the minimum over its checkpoints.
    """
    rising = np.flatnonzero((spec.inflow > 0) | (spec.unconfirmed > 0))
    days = {int(day) - 1 for day in rising if int(day) > 0} | {spec.days, *extra}
    return tuple(sorted(day for day in days if 0 <= day <= spec.days))


def first_full_payment_date(
    inputs: ProjectionInput,
    amount_paise: int,
    *,
    start_on: date | None = None,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> PaymentDate | None:
    """Earliest date a single payment of ``amount_paise`` clears the floor threshold.

    Paying on day ``t`` lowers every balance from ``t`` on. Passing then needs the days before
    ``t`` above the floor and the days from ``t`` on above the floor plus the payment. The second
    part only changes after a checkpoint and the first only gets harder, so the answer is the
    start day or the day after a checkpoint, and only checkpoint balances are needed.
    """
    if amount_paise <= 0:
        return None
    prepared = _prepare(inputs, MODEL)
    days = prepared.spec.days
    start = 0 if start_on is None else max(0, (start_on - inputs.as_of).days)
    if start > days:
        return None
    marks = _checkpoints(prepared.spec, (start - 1,) if start > 0 else ())
    settings = kernel.Settings(
        scenarios=runs, seed=seed, block_length=MODEL.block_length, checkpoints=marks
    )
    run = kernel.run(prepared.spec, settings)
    assert run.checkpoints is not None
    balances = run.checkpoints
    position = {day: column for column, day in enumerate(marks)}
    prefix = np.minimum.accumulate(balances, axis=1)
    suffix = np.minimum.accumulate(balances[:, ::-1], axis=1)[:, ::-1]
    floor = inputs.floor_paise
    needed = runs - _rank(runs, 1 - FLOOR_THRESHOLD)
    candidates = sorted({start, *(day + 1 for day in marks if start < day + 1 <= days)})
    for day in candidates:
        later = [column for column, mark in enumerate(marks) if mark >= day]
        if len(later) == 0:
            break
        after = suffix[:, later[0]] - amount_paise
        if day > 0:
            before = prefix[:, position[day - 1]] if day - 1 in position else None
            assert before is not None
            values = np.minimum(before, after)
        else:
            values = after
        if int(np.count_nonzero(values >= floor)) >= needed:
            found = inputs.as_of + timedelta(days=day)
            return {"date": found.isoformat(), "days": day, "amount_paise": amount_paise}
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
    "last_statement_close",
    "horizon_days",
    "project",
    "reduce_categories",
    "safe_amount_today",
    "simulate_paths",
    "statement_close",
]
