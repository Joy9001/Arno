"""The scenario kernel: one integer matrix of future balances, and every statistic from it.

`arno.engine.projection` turns a ledger into a `Spec`: dated cash, card bills, and pools of
historical spend. This module walks every scenario at once with NumPy, in whole paise, in one
unchunked array.

Randomness is counter-based: the draw for a scenario on a day is a SplitMix64 hash of the seed,
the stream, the scenario, and the date. Scenario ``r`` therefore never depends on how many
scenarios run, on any other stream, or on a library's sampling methods, and `draw` below is the
pure-Python oracle the vectorized code is tested against.

Within a day the order is fixed: income, card bills due, obligations (an obligation is tested
here), one-off payments and goal contributions, then spend. Everyday spend keeps the card it was
paid with; only new charges (``other`` and ``obligation``) are held to the card's limit, and what
does not fit leaves cash the same day. Used credit falls only when a bill is paid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

I64 = npt.NDArray[np.int64]
U64 = npt.NDArray[np.uint64]

MASK64 = (1 << 64) - 1
GOLDEN = 0x9E3779B97F4A7C15
MIX1 = 0xBF58476D1CE4E5B9
MIX2 = 0x94D049BB133111EB
WEEKDAY, WEEKEND = 0, 1


def mix(value: int) -> int:
    """The SplitMix64 finalizer, in plain Python: the oracle for `_mix`."""
    z = (value + GOLDEN) & MASK64
    z = ((z ^ (z >> 30)) * MIX1) & MASK64
    z = ((z ^ (z >> 27)) * MIX2) & MASK64
    return z ^ (z >> 31)


def draw(seed: int, stream: int, scenario: int, day: int) -> int:
    """The 64-bit draw for one cell. `_draws` must agree with this bit for bit."""
    return mix(mix(mix(mix(seed & MASK64) ^ stream) ^ scenario) ^ day)


def _mix(values: U64) -> U64:
    z = values + np.uint64(GOLDEN)
    z ^= z >> np.uint64(30)
    z *= np.uint64(MIX1)
    z ^= z >> np.uint64(27)
    z *= np.uint64(MIX2)
    z ^= z >> np.uint64(31)
    return z


def _draws(seed: int, stream: int, scenarios: int, days: npt.NDArray[np.int64]) -> U64:
    """Draws for every scenario (rows) and day key (columns)."""
    base = np.uint64(mix(mix(seed & MASK64) ^ stream))
    rows = _mix(np.arange(scenarios, dtype=np.uint64) ^ base)
    return _mix(rows[:, None] ^ days.astype(np.uint64)[None, :])


def below(values: U64, bound: int) -> U64:
    """Map draws onto 0..bound-1 by the top 32 bits (bound must be under 2**32)."""
    assert 0 < bound < 1 << 32, "bound must be under 2**32"
    return ((values >> np.uint64(32)) * np.uint64(bound)) >> np.uint64(32)


# --- Spec ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pool:
    """Historical days a stream draws from, in date order.

    ``columns`` holds one int64 array per column, each the length of the pool: ``cash``,
    ``cash_cut`` (the reducible part of cash), and ``card{k}`` and ``card{k}_cut`` for card ``k``.
    """

    columns: dict[str, I64]
    classes: npt.NDArray[np.int8]
    weights: I64 | None = None
    stream: int = 0

    @property
    def size(self) -> int:
        return len(self.classes)


@dataclass(frozen=True, slots=True)
class Rare:
    """Rare large spending days, resampled at their long-run rate rather than from the pool.

    Each row is a whole past day split out of the pool on stream ``replaces_stream``, so on a day
    a rare draw hits it replaces that pool's draw instead of adding to it.
    """

    columns: dict[str, I64]
    events: int
    exposure_days: int
    stream: int
    replaces_stream: int


@dataclass(frozen=True, slots=True)
class CardSpec:
    limit: int
    used: int
    # Day index of the bill each day's charges join; -1 when it falls after the horizon.
    due_of_day: I64
    # Bills already owed at the start, by the day index they are due (0 pays on day zero).
    bills: dict[int, int] = field(default_factory=dict)
    obligation: I64 | None = None
    other: I64 | None = None


@dataclass(frozen=True, slots=True)
class Spec:
    start_balance: int
    days: int
    first_day: int  # date ordinal of index 0
    classes: npt.NDArray[np.int8]  # weekday or weekend of each index
    inflow: I64
    obligation: I64  # cash obligations
    later: I64  # one-off payments and goal contributions
    obligation_days: I64
    unconfirmed: I64
    floor: int
    cards: tuple[CardSpec, ...] = ()
    pools: tuple[Pool, ...] = ()
    rare: Rare | None = None
    cut_bps: int = 0
    cut_until: int = -1  # last day index the cut applies to


@dataclass(frozen=True, slots=True)
class Settings:
    scenarios: int = 2_048
    seed: int = 0
    keep_days: int = 0  # keep full paths for the first this many days (for path-level tests)


@dataclass(slots=True)
class Run:
    """What one kernel run leaves behind: the band, every scenario's minimum, and the tests."""

    scenarios: int
    days: int
    curve: I64  # (days + 1, 3): P10, P50, P90 by day
    minima: I64  # each scenario's lowest balance over the horizon
    crossings: I64  # by day, how many scenarios have been below the floor
    obligation_failures: I64  # by obligation day, how many scenarios could not pay
    obligation_margins: I64  # by obligation day, payable order statistic at scenarios // 100
    credit_exceeded: bool
    optimistic_minima: I64 | None
    optimistic_crossings: I64 | None
    paths: I64 | None
    # Every day whose obligation is tested: the spec's own days plus every card bill due day.
    obligation_days: I64


# --- Drawing days ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Picker:
    """Weighted choice of a pool position, restricted to one class.

    Each class gets a table in which every position appears as many times as its weight, so a
    draw maps straight to a position: the same answer as bisecting the cumulative weights, with
    one gather instead of a search. Both classes' tables are stored end to end.
    """

    table: I64
    offsets: tuple[int, int]
    sizes: tuple[int, int]

    @classmethod
    def build(cls, classes: npt.NDArray[np.int8], weights: I64 | None) -> _Picker:
        all_positions = np.arange(len(classes), dtype=np.int64)
        tables: list[I64] = []
        for label in (WEEKDAY, WEEKEND):
            chosen = all_positions[classes == label]
            # A class with no days falls back to every day, as the walk always has.
            if len(chosen) == 0:
                chosen = all_positions
            tables.append(chosen if weights is None else np.repeat(chosen, weights[chosen]))
        return cls(
            table=np.concatenate(tables),
            offsets=(0, len(tables[0])),
            sizes=(len(tables[0]), len(tables[1])),
        )

    def pick_days(self, labels: npt.NDArray[np.int8], values: U64) -> I64:
        """One pick per scenario and day, each day restricted to its own class."""
        sizes = np.where(labels == WEEKEND, self.sizes[1], self.sizes[0]).astype(np.uint64)
        offsets = np.where(labels == WEEKEND, self.offsets[1], self.offsets[0])
        target = ((values >> np.uint64(32)) * sizes[None, :]) >> np.uint64(32)
        return self.table[target.astype(np.int64) + offsets[None, :]]


def _poisson_table(mean: float, top: int) -> npt.NDArray[np.float64]:
    # shortcut: zero MAD skips rare-day split and Poisson(k) puts 37% zero-rate mass on k=1,
    # revisit if real-ledger rare spikes breach P10
    terms = [math.exp(-mean)]
    for k in range(1, top + 1):
        terms.append(terms[-1] * mean / k)
    return np.cumsum(np.array(terms, dtype=np.float64))


class _RareState:
    """Rare large days: each scenario's own rate, then one Bernoulli draw per day."""

    def __init__(self, rare: Rare, settings: Settings) -> None:
        self.rare = rare
        self.settings = settings
        top = rare.events + 10 * int(math.isqrt(rare.events) + 1) + 10
        table = _poisson_table(float(rare.events), top)
        values = _draws(
            settings.seed, rare.stream * 8 + 4, settings.scenarios, np.zeros(1, np.int64)
        )
        uniform = (values[:, 0] >> np.uint64(11)).astype(np.float64) / float(1 << 53)
        counts = np.searchsorted(table, uniform, side="right").astype(np.int64)
        # The scenario's chance of a rare day, in units of 2**-32.
        self.threshold = np.minimum(
            (counts << np.int64(32)) // max(rare.exposure_days, 1), (1 << 32) - 1
        ).astype(np.uint64)
        self.size = len(next(iter(rare.columns.values())))

    def rows(self, day_keys: I64) -> tuple[npt.NDArray[np.bool_], I64]:
        settings = self.settings
        hits = _draws(settings.seed, self.rare.stream * 8 + 5, settings.scenarios, day_keys)
        which = _draws(settings.seed, self.rare.stream * 8 + 6, settings.scenarios, day_keys)
        happened = (hits >> np.uint64(32)) < self.threshold[:, None]
        return happened, below(which, self.size).astype(np.int64)


# --- The walk ----------------------------------------------------------------------------------


def _scaled(cumulative: I64, bps: int) -> I64:
    """round_half_up(cumulative * bps / 10_000) for non-negative values, in integers."""
    return (cumulative * bps + 5_000) // 10_000


def obligation_days(spec: Spec) -> I64:
    """Every day the walk tests for a cash obligation.

    The spec's own days plus every card bill due date, including the days a day's charges join
    and the days of bills already owed at the start. A bill due today is tested today.
    """
    days = {int(day) for day in spec.obligation_days}
    for card in spec.cards:
        days.update(int(due) for due in card.bills if 0 <= int(due) <= spec.days)
        days.update(int(due) for due in np.unique(card.due_of_day) if 0 <= int(due) <= spec.days)
    return np.array(sorted(days), dtype=np.int64)


def run(spec: Spec, settings: Settings) -> Run:
    """Walk every scenario over the horizon and return the statistics the projection reports."""
    scenarios = settings.scenarios
    width = spec.days + 1
    card_count = len(spec.cards)
    keys = np.arange(spec.first_day, spec.first_day + width, dtype=np.int64)
    rare = _RareState(spec.rare, settings) if spec.rare is not None and spec.rare.events else None

    ranks = [max(1, math.ceil(tau * scenarios)) - 1 for tau in (0.1, 0.5, 0.9)]
    effective_obligations = obligation_days(spec)
    obligation_failures = np.zeros(len(effective_obligations), dtype=np.int64)
    obl_rank = scenarios // 100
    obligation_margins = np.full(len(effective_obligations), np.iinfo(np.int64).max, dtype=np.int64)

    cutting = spec.cut_bps > 0 and spec.cut_until >= 0
    spend = _spend(spec, rare, keys, settings, card_count, cutting)
    if cutting:
        _apply_cut(spec, spend, card_count)

    cash_out = spend["cash"]
    bill_paid = np.zeros((scenarios, width), dtype=np.int64)
    obligation_overflow = np.zeros((scenarios, width), dtype=np.int64)
    credit_exceeded = False
    for k, card in enumerate(spec.cards):
        charges = np.zeros((scenarios, width), dtype=np.int64)
        if card.other is not None:
            charges = charges + card.other[None, :]
        if card.obligation is not None:
            charges = charges + card.obligation[None, :]
        overflow, exceeded = _charge(
            card, charges, spend[f"card{k}"], obligation_overflow, bill_paid
        )
        credit_exceeded |= exceeded
        cash_out = cash_out + overflow

    fixed = spec.inflow - spec.obligation - spec.later
    net = fixed[None, :] - cash_out - bill_paid
    start = np.full(scenarios, spec.start_balance, dtype=np.int64)
    balances = start[:, None] + np.cumsum(net, axis=1)

    for failures, day in enumerate(effective_obligations):
        before = balances[:, day - 1] if day else start
        owed = bill_paid[:, day] + spec.obligation[day] + obligation_overflow[:, day]
        payable = np.where(owed > 0, before + spec.inflow[day] - owed, np.iinfo(np.int64).max)
        obligation_failures[failures] = int(np.count_nonzero(payable < 0))
        obligation_margins[failures] = int(np.partition(payable, obl_rank)[obl_rank])

    lows = np.minimum.accumulate(balances, axis=1)
    optimistic_lows = (
        np.minimum.accumulate(balances + np.cumsum(spec.unconfirmed)[None, :], axis=1)
        if spec.unconfirmed.any()
        else None
    )
    return Run(
        scenarios=scenarios,
        days=spec.days,
        curve=np.partition(balances, ranks, axis=0)[ranks].T,
        minima=lows[:, -1].copy(),
        crossings=np.count_nonzero(lows < spec.floor, axis=0),
        obligation_failures=obligation_failures,
        obligation_margins=obligation_margins,
        credit_exceeded=credit_exceeded,
        optimistic_minima=optimistic_lows[:, -1].copy() if optimistic_lows is not None else None,
        optimistic_crossings=(
            np.count_nonzero(optimistic_lows < spec.floor, axis=0)
            if optimistic_lows is not None
            else None
        ),
        paths=balances[:, : settings.keep_days].copy() if settings.keep_days else None,
        obligation_days=effective_obligations,
    )


def _cut_columns(card_count: int) -> list[str]:
    return ["cash_cut", *(f"card{k}_cut" for k in range(card_count))]


def _spend(
    spec: Spec,
    rare: _RareState | None,
    keys: I64,
    settings: Settings,
    card_count: int,
    with_cut: bool,
) -> dict[str, I64]:
    scenarios, width = settings.scenarios, len(keys)
    names = ["cash", *(f"card{k}" for k in range(card_count))]
    if with_cut:
        names += _cut_columns(card_count)
    spend = {name: np.zeros((scenarios, width), dtype=np.int64) for name in names}
    drawn = rare.rows(keys) if rare is not None and spec.rare is not None else None
    replaces = spec.rare.replaces_stream if spec.rare is not None else None
    for pool in spec.pools:
        picker = _Picker.build(pool.classes, pool.weights)
        draws = _draws(settings.seed, pool.stream * 8, scenarios, keys)
        rows = picker.pick_days(spec.classes, draws)
        # A rare row is a whole day, so it stands in for the draw of the pool it was split from.
        everyday = ~drawn[0] if drawn is not None and pool.stream == replaces else None
        for name in names:
            column = pool.columns.get(name)
            if column is not None:
                values = column[rows]
                spend[name] += values if everyday is None else np.where(everyday, values, 0)
    if drawn is not None and spec.rare is not None:
        happened, which = drawn
        for name in names:
            column = spec.rare.columns.get(name)
            if column is not None:
                spend[name] += np.where(happened, column[which], 0)
    # Day zero is today: its spend is already in the balance.
    for column in spend.values():
        column[:, 0] = 0
    return spend


def _apply_cut(spec: Spec, spend: dict[str, I64], cards: int) -> None:
    """Remove a share of reducible spend until the cut ends, with cumulative rounding.

    Rounding the running total and taking differences keeps the removed amount within half a
    paisa of exact over any horizon, which keeps a cut exactly linear.
    """
    stop = spec.cut_until + 1
    pairs = [("cash", "cash_cut"), *((f"card{k}", f"card{k}_cut") for k in range(cards))]
    for total_name, cut_name in pairs:
        running = np.cumsum(spend[cut_name][:, :stop], axis=1)
        removed = np.diff(_scaled(running, spec.cut_bps), axis=1, prepend=0)
        spend[total_name][:, :stop] -= removed
        spend[cut_name][:, :stop] -= removed


def _charge(
    card: CardSpec,
    charges: I64,
    kept: I64,
    obligation_overflow: I64,
    bill_paid: I64,
) -> tuple[I64, bool]:
    """Charge one card; return the cash part and whether any charge passed the limit.

    Days are split where a bill is due. Within a piece used credit only rises. ``kept`` spend is
    never held to the limit but uses up headroom: each day it goes on the card first, and
    ``charges`` get what is left. A day's new charges are tested against the limit before its
    kept spend takes headroom, so an obligation due today cannot be crowded out by everyday spend.
    """
    # shortcut: unknown unbilled split front-loads 100% balance to first due date and projection
    # assumes cards pay in full, revisit if user revolves a card balance or imports balances
    # without recent spend
    scenarios, width = charges.shape
    used = np.full(scenarios, card.used, dtype=np.int64)
    bills = {due: np.full(scenarios, amount, dtype=np.int64) for due, amount in card.bills.items()}
    overflow = np.zeros_like(charges)
    due_days = {int(day) for day in np.unique(card.due_of_day) if day >= 0} | set(card.bills)
    starts = sorted({0, *(day for day in due_days if day < width)})
    exceeded = False
    for index, start in enumerate(starts):
        stop = starts[index + 1] if index + 1 < len(starts) else width
        paid = bills.pop(start, None)
        if paid is not None:
            bill_paid[:, start] += paid
            used -= paid
        spilled, increments = _charge_piece(
            card, used, charges[:, start:stop], kept[:, start:stop], obligation_overflow, start
        )
        if spilled.any():
            overflow[:, start:stop] = spilled
        exceeded |= bool(spilled.any() or (used > card.limit).any())
        _add_to_bills(bills, increments, card.due_of_day[start:stop])
    return overflow, exceeded


def _charge_piece(
    card: CardSpec,
    used: I64,
    piece: I64,
    kept: I64,
    obligation_overflow: I64,
    start: int,
) -> tuple[I64, I64]:
    """One billing piece: what of ``piece`` spills to cash, and what each day adds to the card."""
    if not piece.any():
        used += kept.sum(axis=1)
        return np.zeros_like(piece), kept
    obligation = card.obligation
    spilled = np.zeros_like(piece)
    taken = np.zeros_like(piece)
    for column in range(piece.shape[1]):
        room = np.maximum(card.limit - used, 0)
        take = np.minimum(piece[:, column], room)
        if obligation is not None and obligation[start + column]:
            owed = obligation[start + column]
            obligation_overflow[:, start + column] += np.clip(used + owed - card.limit, 0, owed)
        used += take
        taken[:, column] = take
        spilled[:, column] = piece[:, column] - take
        used += kept[:, column]
    return spilled, taken + kept


def _add_to_bills(bills: dict[int, I64], increments: I64, dues: I64) -> None:
    if len(dues) == 0:
        return
    edges = np.flatnonzero(np.diff(dues)) + 1
    starts = np.concatenate([[0], edges])
    totals = np.add.reduceat(increments, starts, axis=1)
    for column, start in enumerate(starts):
        due = int(dues[start])
        if due < 0:
            continue
        existing = bills.get(due)
        bills[due] = totals[:, column] if existing is None else existing + totals[:, column]


__all__ = [
    "CardSpec",
    "Pool",
    "Rare",
    "Run",
    "Settings",
    "Spec",
    "U64",
    "below",
    "draw",
    "mix",
    "run",
]
