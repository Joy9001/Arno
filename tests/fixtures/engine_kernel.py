"""Plain-Python reference walk and test inputs for the projection kernel."""

from __future__ import annotations

import bisect
import random

import numpy as np

from arno.engine import kernel
from arno.engine.kernel import CardSpec, Pool, Settings, Spec


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


def _ref_walk(picker, targets, seed, stream, scenario, keys) -> list[int]:
    positions = []
    for label, key in zip(targets, keys, strict=True):
        cumulative, chosen = picker[label]
        positions.append(_ref_pick(cumulative, chosen, kernel.draw(seed, stream, scenario, key)))
    return positions


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
            rows_by_pool.append(
                _ref_walk(
                    _ref_picker(pool_classes, weights),
                    classes,
                    settings.seed,
                    pool.stream * 8,
                    scenario,
                    keys,
                )
            )
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
            for name in names:
                spend[name] = 0
                if t == 0:
                    continue
                for pool, rows in zip(spec.pools, rows_by_pool, strict=True):
                    column = pool.columns.get(name)
                    if column is not None:
                        spend[name] += int(column[rows[t]])
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
                # Everyday spend goes on the card first, past the limit; the rest is new.
                loose = spend[f"card{k}"]
                charges = new
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
                if loose:
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
        cut_bps=1_500 if cut else 0,
        cut_until=days // 2 if cut else -1,
    )
