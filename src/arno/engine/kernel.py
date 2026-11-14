"""The scenario kernel: one integer matrix of future balances, and every statistic from it.

`arno.engine.projection` turns a ledger into a `Spec`: dated cash, card bills, and pools of
historical spend. This module walks every scenario at once with NumPy, in whole paise, and keeps
only what the statistics need, so memory stays flat as the horizon grows.

Randomness is counter-based: the draw for a scenario on a day is a SplitMix64 hash of the seed,
the stream, the scenario, and the date. Scenario ``r`` therefore never depends on how many
scenarios run, on any other stream, or on a library's sampling methods, and `draw` below is the
pure-Python oracle the vectorized code is tested against.

Within a day the order is fixed: income, card bills due, obligations (an obligation is tested
here), one-off payments and goal contributions, then spend. Card spend is charged up to the
card's headroom; the rest leaves cash the same day. Used credit falls only when a bill is paid.
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
CHUNK_DAYS = 127
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
    resample: bool = False
    pseudo: npt.NDArray[np.bool_] | None = None

    @property
    def size(self) -> int:
        return len(self.classes)


@dataclass(frozen=True, slots=True)
class Rare:
    """Rare large spending days, resampled at their long-run rate rather than from the pool."""

    columns: dict[str, I64]
    events: int
    exposure_days: int
    stream: int


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
    scenarios: int = 16_384
    seed: int = 0
    block_length: int = 1
    keep_days: int = 0  # keep full paths for the first this many days (the backtest)
    checkpoints: tuple[int, ...] = ()


@dataclass(slots=True)
class Run:
    """What one kernel run leaves behind: the band, every scenario's minimum, and the tests."""

    scenarios: int
    days: int
    curve: I64  # (days + 1, 3): P10, P50, P90 by day
    minima: I64  # each scenario's lowest balance over the horizon
    crossings: I64  # by day, how many scenarios have been below the floor
    obligation_failures: I64  # by obligation day, how many scenarios could not pay
    credit_exceeded: bool
    optimistic_minima: I64 | None
    optimistic_crossings: I64 | None
    paths: I64 | None
    checkpoints: I64 | None  # (scenarios, len(settings.checkpoints))


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

    def pick(self, label: int, values: U64) -> I64:
        return self.table[self.offsets[label] + below(values, self.sizes[label]).astype(np.int64)]

    def pick_days(self, labels: npt.NDArray[np.int8], values: U64) -> I64:
        """One pick per scenario and day, each day restricted to its own class."""
        sizes = np.where(labels == WEEKEND, self.sizes[1], self.sizes[0]).astype(np.uint64)
        offsets = np.where(labels == WEEKEND, self.offsets[1], self.offsets[0])
        target = ((values >> np.uint64(32)) * sizes[None, :]) >> np.uint64(32)
        return self.table[target.astype(np.int64) + offsets[None, :]]


def walk(
    picker: _Picker,
    pool_classes: npt.NDArray[np.int8],
    target_classes: npt.NDArray[np.int8],
    picks: U64,
    blocks: U64 | None,
    block_length: int,
    start: I64 | None = None,
) -> tuple[I64, I64]:
    """Pool positions for each scenario and target day, by class-matched stationary blocks.

    With probability 1/L a day starts a new block at a random position of its class; otherwise it
    takes the next position if that position's class matches. ``start`` carries the previous
    chunk's last position. Returns the positions and the last column.
    """
    scenarios, length = picks.shape
    out = np.empty((scenarios, length), dtype=np.int64)
    if block_length <= 1 or blocks is None:
        out = picker.pick_days(target_classes, picks)
        return out, out[:, -1].copy()
    threshold = np.uint64((1 << 32) // block_length)
    size = len(pool_classes)
    current = start
    for column in range(length):
        label = int(target_classes[column])
        fresh = picker.pick(label, picks[:, column])
        if current is None:
            current = fresh
        else:
            following = current + 1
            keep = ((blocks[:, column] >> np.uint64(32)) >= threshold) & (following < size)
            safe = np.minimum(following, size - 1)
            keep &= pool_classes[safe] == label
            current = np.where(keep, following, fresh)
        out[:, column] = current
    assert current is not None
    return out, current.copy()


class _PoolState:
    """One pool's walk, carried across chunks."""

    def __init__(self, pool: Pool, settings: Settings, history_days: I64) -> None:
        self.pool = pool
        self.settings = settings
        self.picker = _Picker.build(pool.classes, pool.weights)
        self.last: I64 | None = None
        self.pseudo: I64 | None = None
        if pool.resample:
            # Each scenario's own pseudo-history: the same walk over the real days, so the pool
            # mean varies across scenarios as a sample mean does.
            picks = _draws(settings.seed, pool.stream * 8 + 2, settings.scenarios, history_days)
            blocks = _draws(settings.seed, pool.stream * 8 + 3, settings.scenarios, history_days)
            self.pseudo, _last = walk(
                self.picker, pool.classes, pool.classes, picks, blocks, settings.block_length
            )
            self.picker = _Picker.build(pool.classes, None)

    def rows(self, day_keys: I64, classes: npt.NDArray[np.int8]) -> I64:
        settings = self.settings
        picks = _draws(settings.seed, self.pool.stream * 8, settings.scenarios, day_keys)
        blocks = (
            _draws(settings.seed, self.pool.stream * 8 + 1, settings.scenarios, day_keys)
            if settings.block_length > 1
            else None
        )
        positions, self.last = walk(
            self.picker,
            self.pool.classes,
            classes,
            picks,
            blocks,
            settings.block_length,
            self.last,
        )
        if self.pseudo is None:
            return positions
        return np.take_along_axis(self.pseudo, positions, axis=1)


def _poisson_table(mean: float, top: int) -> npt.NDArray[np.float64]:
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


def run(spec: Spec, settings: Settings) -> Run:
    """Walk every scenario over the horizon and return the statistics the projection reports."""
    scenarios = settings.scenarios
    total_days = spec.days + 1
    card_count = len(spec.cards)
    history_days = np.arange(max((pool.size for pool in spec.pools), default=0), dtype=np.int64)
    states = [_PoolState(pool, settings, history_days[: pool.size]) for pool in spec.pools]
    rare = _RareState(spec.rare, settings) if spec.rare is not None and spec.rare.events else None

    ranks = [max(1, math.ceil(tau * scenarios)) - 1 for tau in (0.1, 0.5, 0.9)]
    curve = np.empty((total_days, 3), dtype=np.int64)
    crossings = np.empty(total_days, dtype=np.int64)
    optimistic = bool(spec.unconfirmed.any())
    unconfirmed_total = np.cumsum(spec.unconfirmed)
    optimistic_crossings = np.empty(total_days, dtype=np.int64) if optimistic else None
    obligation_index = {int(day): index for index, day in enumerate(spec.obligation_days)}
    obligation_failures = np.zeros(len(spec.obligation_days), dtype=np.int64)
    checkpoint_index = {day: index for index, day in enumerate(settings.checkpoints)}
    checkpoints = (
        np.empty((scenarios, len(settings.checkpoints)), dtype=np.int64)
        if settings.checkpoints
        else None
    )
    paths = (
        np.empty((scenarios, min(settings.keep_days, total_days)), dtype=np.int64)
        if settings.keep_days
        else None
    )

    balance = np.full(scenarios, spec.start_balance, dtype=np.int64)
    running_min = np.full(scenarios, np.iinfo(np.int64).max, dtype=np.int64)
    optimistic_min = running_min.copy() if optimistic else None
    used = [np.full(scenarios, card.used, dtype=np.int64) for card in spec.cards]
    bills: list[dict[int, I64]] = [
        {due: np.full(scenarios, amount, dtype=np.int64) for due, amount in card.bills.items()}
        for card in spec.cards
    ]
    cut_carry = {name: np.zeros(scenarios, dtype=np.int64) for name in _cut_columns(card_count)}
    # Every day a bill can fall due: the start's bills, and wherever a day's charges are due.
    due_sets = [
        {int(day) for day in np.unique(card.due_of_day) if day >= 0} | set(card.bills)
        for card in spec.cards
    ]
    credit_exceeded = False

    for begin in range(0, total_days, CHUNK_DAYS):
        end = min(begin + CHUNK_DAYS, total_days)
        width = end - begin
        keys = np.arange(spec.first_day + begin, spec.first_day + end, dtype=np.int64)
        spend = _spend(spec, states, rare, keys, begin, width, scenarios, card_count)
        if spec.cut_bps and begin <= spec.cut_until:
            _apply_cut(spec, spend, cut_carry, begin, width, card_count)

        cash_out = spend["cash"]
        bill_paid = np.zeros((scenarios, width), dtype=np.int64)
        obligation_overflow = np.zeros((scenarios, width), dtype=np.int64)
        for k, card in enumerate(spec.cards):
            charges = spend[f"card{k}"]
            if card.other is not None:
                charges = charges + card.other[None, begin:end]
            obligation = card.obligation[begin:end] if card.obligation is not None else None
            if obligation is not None:
                charges = charges + obligation[None, :]
            overflow, exceeded = _charge(
                card,
                used[k],
                bills[k],
                due_sets[k],
                charges,
                obligation,
                obligation_overflow,
                bill_paid,
                begin,
            )
            credit_exceeded |= exceeded
            cash_out = cash_out + overflow

        fixed = spec.inflow[begin:end] - spec.obligation[begin:end] - spec.later[begin:end]
        net = fixed[None, :] - cash_out - bill_paid
        balances = balance[:, None] + np.cumsum(net, axis=1)

        for column in range(width):
            day = begin + column
            failures = obligation_index.get(day)
            if failures is not None and day > 0:
                before = balances[:, column - 1] if column else balance
                payable = (
                    before
                    + spec.inflow[day]
                    - bill_paid[:, column]
                    - spec.obligation[day]
                    - obligation_overflow[:, column]
                )
                obligation_failures[failures] += int(np.count_nonzero(payable < 0))
            spot = checkpoint_index.get(day)
            if spot is not None and checkpoints is not None:
                checkpoints[:, spot] = balances[:, column]

        curve[begin:end] = np.partition(balances, ranks, axis=0)[ranks].T
        lows = np.minimum.accumulate(balances, axis=1)
        lows = np.minimum(lows, running_min[:, None])
        crossings[begin:end] = np.count_nonzero(lows < spec.floor, axis=0)
        running_min = lows[:, -1].copy()
        if optimistic_min is not None and optimistic_crossings is not None:
            shifted = balances + unconfirmed_total[None, begin:end]
            optimistic_lows = np.minimum(
                np.minimum.accumulate(shifted, axis=1), optimistic_min[:, None]
            )
            optimistic_crossings[begin:end] = np.count_nonzero(optimistic_lows < spec.floor, axis=0)
            optimistic_min = optimistic_lows[:, -1].copy()
        if paths is not None and begin < paths.shape[1]:
            stop = min(end, paths.shape[1])
            paths[:, begin:stop] = balances[:, : stop - begin]
        balance = balances[:, -1].copy()

    return Run(
        scenarios=scenarios,
        days=spec.days,
        curve=curve,
        minima=running_min,
        crossings=crossings,
        obligation_failures=obligation_failures,
        credit_exceeded=credit_exceeded,
        optimistic_minima=optimistic_min,
        optimistic_crossings=optimistic_crossings,
        paths=paths,
        checkpoints=checkpoints,
    )


def _cut_columns(card_count: int) -> list[str]:
    return ["cash_cut", *(f"card{k}_cut" for k in range(card_count))]


def _spend(
    spec: Spec,
    states: list[_PoolState],
    rare: _RareState | None,
    keys: I64,
    begin: int,
    width: int,
    scenarios: int,
    card_count: int,
) -> dict[str, I64]:
    names = ["cash", *(f"card{k}" for k in range(card_count))]
    if spec.cut_bps and begin <= spec.cut_until:
        names += _cut_columns(card_count)
    spend = {name: np.zeros((scenarios, width), dtype=np.int64) for name in names}
    classes = spec.classes[begin : begin + width]
    for state in states:
        rows = state.rows(keys, classes)
        for name in names:
            column = state.pool.columns.get(name)
            if column is not None:
                spend[name] += column[rows]
    if rare is not None and spec.rare is not None:
        happened, which = rare.rows(keys)
        for name in names:
            column = spec.rare.columns.get(name)
            if column is not None:
                spend[name] += np.where(happened, column[which], 0)
    if begin == 0:
        # Day zero is today: its spend is already in the balance.
        for name in names:
            spend[name][:, 0] = 0
    return spend


def _apply_cut(
    spec: Spec, spend: dict[str, I64], carry: dict[str, I64], begin: int, width: int, cards: int
) -> None:
    """Remove a share of reducible spend until the cut ends, with cumulative rounding.

    Rounding the running total and taking differences keeps the removed amount within half a
    paisa of exact over any horizon, which keeps a cut exactly linear.
    """
    stop = min(width, spec.cut_until - begin + 1)
    if stop <= 0:
        return
    pairs = [("cash", "cash_cut"), *((f"card{k}", f"card{k}_cut") for k in range(cards))]
    for total_name, cut_name in pairs:
        reducible = spend[cut_name][:, :stop]
        running = carry[cut_name][:, None] + np.cumsum(reducible, axis=1)
        removed_total = _scaled(running, spec.cut_bps)
        previous = np.concatenate(
            [_scaled(carry[cut_name], spec.cut_bps)[:, None], removed_total[:, :-1]], axis=1
        )
        removed = removed_total - previous
        spend[total_name][:, :stop] -= removed
        spend[cut_name][:, :stop] -= removed
        carry[cut_name] = running[:, -1].copy()


def _charge(
    card: CardSpec,
    used: I64,
    bills: dict[int, I64],
    due_set: set[int],
    charges: I64,
    obligation: I64 | None,
    obligation_overflow: I64,
    bill_paid: I64,
    begin: int,
) -> tuple[I64, bool]:
    """Charge one card for a chunk; return the part that leaves cash and whether it ever did.

    Days are split where a bill is due. Within a piece used credit only rises, so the amount
    charged so far is min(cumulative charges, headroom at the piece start), and the rest is cash.
    ``used`` and ``bills`` are updated in place.
    """
    scenarios, width = charges.shape
    overflow = np.zeros_like(charges)
    due_days = sorted(day for day in due_set if begin <= day < begin + width)
    starts = sorted({0, *(day - begin for day in due_days)})
    exceeded = False
    for index, start in enumerate(starts):
        stop = starts[index + 1] if index + 1 < len(starts) else width
        day = begin + start
        paid = bills.pop(day, None)
        if paid is not None:
            bill_paid[:, start] += paid
            used -= paid
        piece = charges[:, start:stop]
        headroom = np.maximum(card.limit - used, 0)
        running = np.cumsum(piece, axis=1)
        charged = np.minimum(running, headroom[:, None])
        previous = np.concatenate([np.zeros((scenarios, 1), np.int64), charged[:, :-1]], axis=1)
        increments = charged - previous
        spill = piece - increments
        if spill.any():
            exceeded = True
            overflow[:, start:stop] = spill
            if obligation is not None:
                before = used[:, None] + previous
                owed = obligation[None, start:stop]
                obligation_overflow[:, start:stop] += np.clip(before + owed - card.limit, 0, owed)
        used += charged[:, -1]
        dues = card.due_of_day[day : begin + stop]
        _add_to_bills(bills, increments, dues)
    return overflow, exceeded


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
    "CHUNK_DAYS",
    "CardSpec",
    "Pool",
    "Rare",
    "Run",
    "Settings",
    "Spec",
    "below",
    "draw",
    "mix",
    "run",
    "walk",
]
