"""Plain-Python reference walk and test inputs for the projection kernel."""

from __future__ import annotations

import bisect
import random

import numpy as np

from arno.engine import kernel
from arno.engine.kernel import CardSpec, Pool, SeriesSpec, Settings, Spec


def _ref_pick(cumulative: list[int], positions: list[int], value: int) -> int:
    total = cumulative[-1]
    target = ((value >> 32) * total) >> 32
    return positions[bisect.bisect_right(cumulative, target)]


def _ref_picker(classes: list[int], weights: list[int] | None) -> dict[int, tuple[list, list]]:
    out = {}
    for label in (0, 1):
        chosen = [i for i, c in enumerate(classes) if c == label] or list(range(len(classes)))
        weight = [weights[i] if weights is not None else 1 for i in chosen]
        out[label] = (list(np.cumsum(weight)), chosen)
    return out


def _ref_walk(
    picker, pool_classes, targets, seed, stream_pick, stream_block, scenario, keys, length
) -> list[int]:
    positions: list[int] = []
    current = None
    threshold = (1 << 32) // length
    for label, key in zip(targets, keys, strict=True):
        cumulative, chosen = picker[label]
        fresh = _ref_pick(cumulative, chosen, kernel.draw(seed, stream_pick, scenario, key))
        if current is None or length <= 1:
            current = fresh
        else:
            block = kernel.draw(seed, stream_block, scenario, key) >> 32
            following = current + 1
            if (
                block >= threshold
                and following < len(pool_classes)
                and pool_classes[following] == label
            ):
                current = following
            else:
                current = fresh
        positions.append(current)
    return positions


def _ref_series(spec: Spec, settings: Settings, scenario: int) -> dict[int, dict[str, int]]:
    by_day: dict[int, dict[str, int]] = {}
    for series in spec.series:
        if len(series.ordinals) == 0 or len(series.amounts) == 0:
            continue
        alive_draw = kernel.draw(settings.seed, series.stream * 8, scenario, 0)
        if (alive_draw >> 32) >= series.alive_threshold:
            continue
        cum = [int(value) for value in series.jitter_cumulative]
        offsets = [int(value) for value in series.jitter_offsets]
        col = (
            f"card{series.card}"
            if series.card is not None and 0 <= series.card < len(spec.cards)
            else "cash"
        )
        for ordinal in series.ordinals:
            key = int(ordinal)
            skip_draw = kernel.draw(settings.seed, series.stream * 8 + 1, scenario, key)
            if (skip_draw >> 32) < series.skip_threshold:
                continue
            offset_draw = kernel.draw(settings.seed, series.stream * 8 + 2, scenario, key)
            if len(cum) > 0:
                target = ((offset_draw >> 32) * cum[-1]) >> 32
                offset = offsets[bisect.bisect_right(cum, target)]
            else:
                offset = 0
            day = (key - spec.first_day) + offset
            if not (1 <= day <= spec.days):
                continue
            amount_draw = kernel.draw(settings.seed, series.stream * 8 + 3, scenario, key)
            amount_index = ((amount_draw >> 32) * len(series.amounts)) >> 32
            amount = int(series.amounts[amount_index])
            slot = by_day.setdefault(day, {})
            slot[col] = slot.get(col, 0) + amount
            if series.reducible:
                cut_col = f"{col}_cut"
                slot[cut_col] = slot.get(cut_col, 0) + amount
    return by_day


def _reference_walk(spec: Spec, settings: Settings) -> tuple[list[list[int]], list[list[int]]]:
    """Each scenario's daily balances and pre-discretionary payables, one day at a time."""
    days = spec.days + 1
    keys = [spec.first_day + t for t in range(days)]
    classes = [int(c) for c in spec.classes]
    paths = []
    payables = []
    for scenario in range(settings.scenarios):
        rows_by_pool = []
        for pool in spec.pools:
            pool_classes = [int(c) for c in pool.classes]
            weights = [int(w) for w in pool.weights] if pool.weights is not None else None
            if pool.resample:
                history = list(range(pool.size))
                pseudo = _ref_walk(
                    _ref_picker(pool_classes, weights),
                    pool_classes,
                    pool_classes,
                    settings.seed,
                    pool.stream * 8 + 2,
                    pool.stream * 8 + 3,
                    scenario,
                    history,
                    settings.block_length,
                )
                positions = _ref_walk(
                    _ref_picker(pool_classes, None),
                    pool_classes,
                    classes,
                    settings.seed,
                    pool.stream * 8,
                    pool.stream * 8 + 1,
                    scenario,
                    keys,
                    settings.block_length,
                )
                rows_by_pool.append([pseudo[p] for p in positions])
            else:
                rows_by_pool.append(
                    _ref_walk(
                        _ref_picker(pool_classes, weights),
                        pool_classes,
                        classes,
                        settings.seed,
                        pool.stream * 8,
                        pool.stream * 8 + 1,
                        scenario,
                        keys,
                        settings.block_length,
                    )
                )
        series_by_day = _ref_series(spec, settings, scenario)
        balance = spec.start_balance
        used = [card.used for card in spec.cards]
        bills = [dict(card.bills) for card in spec.cards]
        removed_total = {"cash_cut": 0, **{f"card{k}_cut": 0 for k in range(len(spec.cards))}}
        reducible_total = dict.fromkeys(removed_total, 0)
        path = []
        payable_path = []
        for t in range(days):
            spend = {}
            names = ["cash", *(f"card{k}" for k in range(len(spec.cards))), *removed_total]
            day_series = series_by_day.get(t, {})
            for name in names:
                spend[name] = 0
                if t == 0:
                    continue
                for pool, rows in zip(spec.pools, rows_by_pool, strict=True):
                    column = pool.columns.get(name)
                    if column is not None:
                        spend[name] += int(column[rows[t]])
                spend[name] += day_series.get(name, 0)
            if spec.cut_bps and t <= spec.cut_until:
                for total_name, cut_name in [
                    ("cash", "cash_cut"),
                    *((f"card{k}", f"card{k}_cut") for k in range(len(spec.cards))),
                ]:
                    before = (reducible_total[cut_name] * spec.cut_bps + 5_000) // 10_000
                    reducible_total[cut_name] += spend[cut_name]
                    after = (reducible_total[cut_name] * spec.cut_bps + 5_000) // 10_000
                    spend[total_name] -= after - before
            balance += int(spec.inflow[t])
            for k in range(len(spec.cards)):
                paid = bills[k].pop(t, 0)
                balance -= paid
                used[k] -= paid
            balance -= int(spec.obligation[t])
            payable = balance
            for k, card in enumerate(spec.cards):
                new = [
                    int(card.obligation[t]) if card.obligation is not None else 0,
                    int(card.other[t]) if card.other is not None else 0,
                ]
                loose = 0
                if card.limit_spend:
                    charges = [*new, spend[f"card{k}"]]
                else:
                    # Resampled spend goes on the card first, past the limit; a series is new.
                    series_part = day_series.get(f"card{k}", 0) if t > 0 else 0
                    loose = spend[f"card{k}"] - series_part
                    charges = [*new, series_part]
                for charge_index, amount in enumerate(charges):
                    charged = min(amount, max(card.limit - used[k], 0))
                    overflow = amount - charged
                    balance -= overflow
                    if charge_index == 0:
                        payable -= overflow
                    used[k] += charged
                    due = int(card.due_of_day[t])
                    if due >= 0:
                        bills[k][due] = bills[k].get(due, 0) + charged
                if not card.limit_spend and loose:
                    # Loose spend keeps the card and takes headroom after the day's new charges.
                    used[k] += loose
                    due = int(card.due_of_day[t])
                    if due >= 0:
                        bills[k][due] = bills[k].get(due, 0) + loose
            balance -= int(spec.later[t])
            balance -= spend["cash"]
            path.append(balance)
            payable_path.append(payable)
        paths.append(path)
        payables.append(payable_path)
    return paths, payables


def _spec(
    rng: random.Random,
    *,
    cards: int,
    days: int,
    cut: bool = False,
    series: bool = False,
    limit_spend: bool = True,
) -> Spec:
    total = days + 1
    first_day = 739_000
    classes = np.array([1 if (first_day + t) % 7 in (5, 6) else 0 for t in range(total)], np.int8)
    inflow = np.zeros(total, np.int64)
    obligation = np.zeros(total, np.int64)
    later = np.zeros(total, np.int64)
    for t in range(1, total):
        if rng.random() < 0.05:
            inflow[t] = rng.randint(5_000, 40_000)
        if rng.random() < 0.08:
            obligation[t] = rng.randint(1_000, 9_000)
        if rng.random() < 0.05:
            later[t] = rng.randint(500, 3_000)
    obligation_days = np.array(np.nonzero(obligation)[0], np.int64)
    card_specs = []
    for k in range(cards):
        # A day's charges join the first bill due after it.
        dues = np.array([((t + 5 - k) // 30 + 1) * 30 - 5 + k for t in range(total)], np.int64)
        dues[dues > days] = -1
        card_specs.append(
            CardSpec(
                limit=rng.randint(3_000, 12_000),
                used=rng.randint(0, 2_000),
                due_of_day=dues,
                bills={rng.randint(0, 9): rng.randint(0, 2_000)},
                obligation=np.array(
                    [rng.randint(500, 3_000) if rng.random() < 0.04 else 0 for _ in range(total)],
                    np.int64,
                ),
                other=np.array(
                    [rng.randint(1_000, 5_000) if rng.random() < 0.03 else 0 for _ in range(total)],
                    np.int64,
                ),
                limit_spend=limit_spend,
            )
        )
    pools = []
    for index in range(2):
        size = rng.randint(8, 20)
        cash = np.array([rng.randint(0, 600) for _ in range(size)], np.int64)
        columns: dict[str, kernel.I64] = {"cash": cash, "cash_cut": cash // 2}
        for k in range(cards):
            columns[f"card{k}"] = np.array([rng.randint(0, 400) for _ in range(size)], np.int64)
            columns[f"card{k}_cut"] = columns[f"card{k}"] // 3
        pools.append(
            Pool(
                columns=columns,
                classes=np.array([rng.randint(0, 1) for _ in range(size)], np.int8),
                weights=np.array([rng.randint(1, 5) for _ in range(size)], np.int64),
                stream=index + 1,
                resample=index == 1,
            )
        )
    series_specs: list[SeriesSpec] = []
    if series:
        jitter_offsets = np.array([-1, 0, 1, 2], dtype=np.int64)
        jitter_cumulative = np.array(
            [1 << 30, (1 << 30) + (1 << 31), (1 << 30) + (1 << 31) + (1 << 29), 1 << 32],
            dtype=np.uint64,
        )
        for index in range(3):
            occ_days = sorted({0, days, *(rng.randint(1, days) for _ in range(rng.randint(4, 8)))})
            series_specs.append(
                SeriesSpec(
                    ordinals=np.array([first_day + d for d in occ_days], dtype=np.int64),
                    alive_threshold=int(round(rng.uniform(0.55, 0.95) * (1 << 32))),
                    skip_threshold=int(round(rng.uniform(0.10, 0.35) * (1 << 32))),
                    jitter_offsets=jitter_offsets,
                    jitter_cumulative=jitter_cumulative,
                    amounts=np.array(
                        [rng.randint(200, 900) for _ in range(rng.randint(1, 3))],
                        dtype=np.int64,
                    ),
                    card=rng.randrange(cards) if cards > 0 and index == 1 else None,
                    reducible=index != 2,
                    stream=100 + index,
                )
            )
    return Spec(
        start_balance=rng.randint(5_000, 30_000),
        days=days,
        first_day=first_day,
        classes=classes,
        inflow=inflow,
        obligation=obligation,
        later=later,
        obligation_days=obligation_days,
        unconfirmed=np.zeros(total, np.int64),
        floor=2_000,
        cards=tuple(card_specs),
        pools=tuple(pools),
        series=tuple(series_specs),
        cut_bps=1_500 if cut else 0,
        cut_until=days // 2 if cut else -1,
    )
