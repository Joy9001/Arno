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
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import lru_cache
from typing import NotRequired, Required, TypedDict

import numpy as np
import numpy.typing as npt

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


class RareDay(TypedDict):
    """One past day spent far above the usual, resampled as a rare event."""

    date: str
    amount_paise: int


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
    # Unusually large past days resampled as their own rare stream; a one-off expense here
    # can be marked so it does not recur.
    rare_days: NotRequired[list[RareDay]]
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
    emi_rate_bps: int
    emi_cost_paise: int


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
    levers: list[str]
    cost_paise: int
    floor_probability: float


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
    # The card's own EMI terms, when recorded: annual rate in basis points, and fee.
    emi_rate_bps: int | None = None
    emi_fee_paise: int | None = None

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
class SpendRow:
    """Spend on one day in one category from one account; ``card_id`` is None for cash."""

    date: date
    category_id: str | None
    card_id: str | None
    amount_paise: int


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
    # Positive spend over the long window, for whole-day pools and rare days.
    spend: tuple[SpendRow, ...] = ()
    # The first day the ledger holds data; days before it are not zero-spend days.
    history_start: date | None = None

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


def goal_rows(inputs: ProjectionInput) -> list[GoalRow]:
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
# Admitted on the tuning seeds, one switch at a time, each lowering the mean CRPS by at
# least 1% with no cell significantly worse: whole days (3.6%), the short-history prior
# (3.4%), and rare days (1.0%). Blocks, recency weights, and pool resampling did not pass.
MODEL = Model(pooling="day", rare_mad_multiple=3, prior_quarters=(0, 2, 4, 6, 8))
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


RARE_WINDOW_DAYS = 365
RECENCY_UNIT = 1_024
RARE_MIN_DAYS = 10
DAY_STREAM = 0
RARE_STREAM = 999


def _weekend(day: date) -> int:
    return kernel.WEEKEND if day.weekday() >= 5 else kernel.WEEKDAY


def _recency(ages: npt.NDArray[np.int64], half_life: int) -> kernel.I64:
    """Integer weights that halve every ``half_life`` days of age, never below one."""
    weights = np.rint(RECENCY_UNIT * np.exp2(-ages / half_life)).astype(np.int64)
    return np.maximum(weights, 1)


def _card_for(
    category: SpendCategory, cards: tuple[CardAccount, ...], index: dict[str, int]
) -> int | None:
    # The card a category's share goes to, as the walk always chose it: its own card, else the
    # first card; a share with no card at all, or an unknown card, is cash.
    if category.card_share <= 0 or len(cards) == 0:
        return None
    if category.card_id is None:
        return 0
    return index.get(category.card_id)


def _cuttable(category: SpendCategory) -> bool:
    """A category a cut may reduce: reducible with a positive estimate, the set
    `reduce_categories` reports."""
    return category.reducible and category.baseline_per_day_paise > 0


def _split_columns(
    values: kernel.I64, category: SpendCategory, card: int | None
) -> dict[str, kernel.I64]:
    if card is None:
        card_part = np.zeros_like(values)
    else:
        card_part = np.array(
            [
                max(0, min(int(round(int(value) * category.card_share)), int(value)))
                for value in values
            ],
            dtype=np.int64,
        )
    cash = values - card_part
    columns: dict[str, kernel.I64] = {"cash": cash}
    if card is not None:
        columns[f"card{card}"] = card_part
    if _cuttable(category):
        columns["cash_cut"] = cash
        if card is not None:
            columns[f"card{card}_cut"] = card_part
    return columns


def _coverage_start(inputs: ProjectionInput, window: int) -> date:
    start = inputs.as_of - timedelta(days=window)
    return start if inputs.history_start is None else max(start, inputs.history_start)


def _category_pool(
    inputs: ProjectionInput,
    category: SpendCategory,
    card: int | None,
    stream: int,
    model: Model,
) -> kernel.Pool | None:
    """One category's own pool: its real days, its flat estimate, or the short-history prior."""
    history = _dense_history(category)
    real = sum(1 for _day, amount in history if amount > 0) >= MIN_REAL_DAYS
    weights: kernel.I64 | None = None
    if real:
        values = np.array([max(0, amount) for _day, amount in history], dtype=np.int64)
        classes = np.array([_weekend(day) for day, _amount in history], dtype=np.int8)
        if model.recency_half_life_days is not None:
            ages = np.array([(inputs.as_of - day).days for day, _amount in history], np.int64)
            weights = _recency(ages, model.recency_half_life_days)
    elif model.prior_quarters is not None and category.baseline_per_day_paise > 0:
        values, classes, weights = _prior(inputs, category, model.prior_quarters)
    else:
        values = np.array([category.baseline_per_day_paise] * 2, dtype=np.int64)
        classes = np.array([kernel.WEEKDAY, kernel.WEEKEND], dtype=np.int8)
    if not bool((values > 0).any()):
        return None
    return kernel.Pool(
        columns=_split_columns(np.maximum(values, 0), category, card),
        classes=classes,
        weights=weights,
        stream=stream,
        resample=model.pool_resampling and real,
    )


def _prior(
    inputs: ProjectionInput, category: SpendCategory, quarters: tuple[int, ...]
) -> tuple[kernel.I64, npt.NDArray[np.int8], kernel.I64]:
    """Real days plus pseudo-days around the stated estimate, the prior fading as days arrive.

    With ``r`` real spending days, the real days together carry weight ``r`` and the pseudo-days
    ``14 - r`` within each weekday class, so the pool's mean moves linearly from the estimate to
    the history. The pseudo-days are fixed multiples of the estimate, in quarters.
    """
    start = _coverage_start(inputs, HISTORY_DAYS)
    by_day = dict(category.history)
    days = [start + timedelta(days=offset) for offset in range((inputs.as_of - start).days)]
    real_values = [max(0, int(by_day.get(day, 0))) for day in days]
    spent = sum(1 for value in real_values if value > 0)
    pseudo = [category.baseline_per_day_paise * quarter // 4 for quarter in quarters]
    values: list[int] = []
    classes: list[int] = []
    weights: list[int] = []
    counts = {label: sum(1 for day in days if _weekend(day) == label) for label in (0, 1)}
    if spent > 0:
        for day, value in zip(days, real_values, strict=True):
            values.append(value)
            classes.append(_weekend(day))
            weights.append(spent * len(pseudo))
    for label in (kernel.WEEKDAY, kernel.WEEKEND):
        share = (MIN_REAL_DAYS - spent) * max(counts[label], 1)
        for value in pseudo:
            values.append(value)
            classes.append(label)
            weights.append(share)
    return (
        np.array(values, dtype=np.int64),
        np.array(classes, dtype=np.int8),
        np.array(weights, dtype=np.int64),
    )


def _day_rows(
    inputs: ProjectionInput, card_index: dict[str, int]
) -> tuple[Sequence[SpendRow], date | None]:
    """The rows whole-day pools read, and where their history starts when it is not the ledger's.

    Inputs built without spend rows (tests, the HackerRank adapter) carry only each category's
    history: it is split into cash and card the way the category's card share always split it.
    """
    if len(inputs.spend) > 0:
        return inputs.spend, None
    rows: list[SpendRow] = []
    earliest: date | None = None
    for category in inputs.categories:
        history = _dense_history(category)
        if len(history) == 0:
            continue
        earliest = history[0][0] if earliest is None else min(earliest, history[0][0])
        card = _card_for(category, inputs.cards, card_index)
        card_id = inputs.cards[card].id if card is not None else None
        for day, amount in history:
            if amount <= 0:
                continue
            on_card = 0
            if card_id is not None:
                on_card = max(0, min(int(round(amount * category.card_share)), amount))
                if on_card > 0:
                    rows.append(SpendRow(day, category.id, card_id, on_card))
            if amount - on_card > 0:
                rows.append(SpendRow(day, category.id, None, amount - on_card))
    return rows, earliest


def _day_pool(
    inputs: ProjectionInput,
    sampled: set[str],
    card_index: dict[str, int],
    model: Model,
) -> tuple[kernel.Pool | None, kernel.Rare | None, list[tuple[date, int]]]:
    """Whole historical days of the sampled categories, cash and each card side by side.

    A day the household spent far above its usual (median plus a multiple of the median absolute
    deviation, over days with spend) leaves the everyday pool and becomes a rare day, resampled at
    its rate over the longer window instead.
    """
    reducible = {category.id for category in inputs.categories if _cuttable(category)}
    window = RARE_WINDOW_DAYS if model.rare_mad_multiple is not None else HISTORY_DAYS
    rows, earliest = _day_rows(inputs, card_index)
    first = _coverage_start(inputs, window)
    if earliest is not None:
        first = max(first, earliest)
    count = (inputs.as_of - first).days
    if count <= 0:
        return None, None, []
    names = ["cash", "cash_cut"]
    for k in range(len(inputs.cards)):
        names += [f"card{k}", f"card{k}_cut"]
    columns = {name: np.zeros(count, dtype=np.int64) for name in names}
    for row in rows:
        if row.category_id not in sampled or row.amount_paise <= 0:
            continue
        offset = (row.date - first).days
        if not 0 <= offset < count:
            continue
        card = card_index.get(row.card_id) if row.card_id is not None else None
        base = "cash" if card is None else f"card{card}"
        columns[base][offset] += row.amount_paise
        if row.category_id in reducible:
            columns[f"{base}_cut"][offset] += row.amount_paise
    dates = [first + timedelta(days=offset) for offset in range(count)]
    classes = np.array([_weekend(day) for day in dates], dtype=np.int8)
    ages = np.array([(inputs.as_of - day).days for day in dates], dtype=np.int64)
    totals = columns["cash"] + sum(
        (columns[f"card{k}"] for k in range(len(inputs.cards))), np.zeros(count, np.int64)
    )
    rare_mask = np.zeros(count, dtype=bool)
    if model.rare_mad_multiple is not None:
        spent = totals[totals > 0]
        if len(spent) >= RARE_MIN_DAYS:
            median = float(np.median(spent))
            spread = float(np.median(np.abs(spent - median)))
            # A zero deviation means most spending days look alike; there is nothing to split.
            if spread > 0:
                rare_mask = totals > median + model.rare_mad_multiple * spread
    everyday = (ages <= HISTORY_DAYS) & ~rare_mask
    weights = (
        _recency(ages[everyday], model.recency_half_life_days)
        if model.recency_half_life_days is not None
        else None
    )
    used = [name for name in names if bool(columns[name][everyday].any()) or name == "cash"]
    pool = (
        kernel.Pool(
            columns={name: columns[name][everyday] for name in used},
            classes=classes[everyday],
            weights=weights,
            stream=DAY_STREAM,
            resample=model.pool_resampling,
        )
        if bool(everyday.any())
        else None
    )
    rare = None
    rare_days = [(dates[offset], int(totals[offset])) for offset in np.flatnonzero(rare_mask)]
    if bool(rare_mask.any()):
        rare = kernel.Rare(
            columns={name: columns[name][rare_mask] for name in names},
            events=int(rare_mask.sum()),
            exposure_days=count,
            stream=RARE_STREAM,
        )
    return pool, rare, rare_days


@dataclass(frozen=True, slots=True)
class Prepared:
    spec: kernel.Spec
    sampled: list[str]
    flat: list[str]
    model: Model
    # The days resampled as rare large spending, with each day's total.
    rare_days: list[tuple[date, int]] = field(default_factory=list)


def prepare(inputs: ProjectionInput, model: Model) -> Prepared:
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
    sampled_ids: set[str] = set()
    for category in inputs.categories:
        real = _pools(category)[3]
        (sampled if real else flat).append(category.name)
        if real:
            sampled_ids.add(category.id)
    rare: kernel.Rare | None = None
    rare_days: list[tuple[date, int]] = []
    if model.pooling == "day":
        day_pool, rare, rare_days = _day_pool(inputs, sampled_ids, card_index, model)
        if day_pool is not None:
            pools.append(day_pool)
    for stream, category in enumerate(inputs.categories, start=1):
        if model.pooling == "day" and category.id in sampled_ids:
            continue
        pool = _category_pool(
            inputs, category, _card_for(category, inputs.cards, card_index), stream, model
        )
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
        rare=rare,
        cut_bps=cut_bps,
        cut_until=cut_until,
    )
    return Prepared(spec=spec, sampled=sampled, flat=flat, model=model, rare_days=rare_days)


def order_rank(count: int, share: float) -> int:
    """The 0-based index of the order statistic at rank ceil(share * count)."""
    return max(1, math.ceil(share * count)) - 1


def _first_crossing(as_of: date, crossings: kernel.I64, scenarios: int) -> str | None:
    # More than 10% of scenarios below the floor: absent exactly when the floor probability passes.
    over = np.flatnonzero(crossings * 10 > scenarios)
    return (as_of + timedelta(days=int(over[0]))).isoformat() if len(over) > 0 else None


def _median_low(as_of: date, medians: kernel.I64) -> MedianLow:
    index = int(np.argmin(medians))
    return {"date": (as_of + timedelta(days=index)).isoformat(), "paise": int(medians[index])}


def _summary(inputs: ProjectionInput, prepared: Prepared, run: kernel.Run, seed: int) -> Projection:
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
    low_rank = order_rank(scenarios, 0.1)
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
        "resampling": _describe(prepared.model),
        "floor_interval": [round(low, 4), round(high, 4)],
        "floor_shortfall_paise": int(floor - breaching.mean()) if len(breaching) > 0 else 0,
        "rare_days": [
            {"date": day.isoformat(), "amount_paise": amount} for day, amount in prepared.rare_days
        ],
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
        "goals": goal_rows(inputs),
        "obligations_met": misses == 0,
        "data_quality": data_quality,
        "sources": list(inputs.sources),
        "source_ids": source_ids,
        "runs": scenarios,
        "seed": seed,
        "optimistic": None,
    }
    end = as_of + timedelta(days=days)
    unconfirmed = [
        event
        for event in inputs.events
        if not event.confirmed
        and event.amount_paise > 0
        and event.kind.startswith("income")
        and event.date <= end
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
) -> tuple[Prepared, kernel.Run]:
    if runs < 1:
        raise ValueError("runs must be positive")
    chosen = model if model is not None else MODEL
    prepared = prepare(inputs, chosen)
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
    rank = order_rank(run.scenarios, 1 - FLOOR_THRESHOLD)
    headroom = int(np.partition(run.minima, rank)[rank]) - inputs.floor_paise
    return max(0, headroom)


def checkpoints_for(spec: kernel.Spec, extra: tuple[int, ...] = ()) -> tuple[int, ...]:
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
    threshold: float = FLOOR_THRESHOLD,
) -> PaymentDate | None:
    """Earliest date a single payment of ``amount_paise`` clears the floor threshold.

    Paying on day ``t`` lowers every balance from ``t`` on. Passing then needs the days before
    ``t`` above the floor and the days from ``t`` on above the floor plus the payment. The second
    part only changes after a checkpoint and the first only gets harder, so the answer is the
    start day or the day after a checkpoint, and only checkpoint balances are needed.
    """
    if amount_paise <= 0:
        return None
    prepared = prepare(inputs, MODEL)
    days = prepared.spec.days
    start = 0 if start_on is None else max(0, (start_on - inputs.as_of).days)
    if start > days:
        return None
    marks = checkpoints_for(prepared.spec, (start - 1,) if start > 0 else ())
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
    needed = runs - order_rank(runs, 1 - threshold)
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
    "SpendRow",
    "card_due_date",
    "checkpoints_for",
    "first_full_payment_date",
    "goal_rows",
    "order_rank",
    "prepare",
    "last_statement_close",
    "horizon_days",
    "project",
    "reduce_categories",
    "safe_amount_today",
    "simulate_paths",
    "statement_close",
]
