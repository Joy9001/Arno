"""The scenario kernel against two oracles: a plain-Python walk and an exact lattice recursion.

The plain walk follows one scenario at a time, one day at a time, with the same draws; it must
match the vectorized kernel to the paisa. The lattice recursion computes a card-free floor
probability exactly; the kernel must land inside its own sampling interval around it.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import replace

import numpy as np
import pytest

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


def reference(spec: Spec, settings: Settings) -> list[list[int]]:
    """Each scenario's daily balances, walked one day at a time."""
    days = spec.days + 1
    keys = [spec.first_day + t for t in range(days)]
    classes = [int(c) for c in spec.classes]
    paths = []
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
        balance = spec.start_balance
        used = [card.used for card in spec.cards]
        bills = [dict(card.bills) for card in spec.cards]
        removed_total = {"cash_cut": 0, **{f"card{k}_cut": 0 for k in range(len(spec.cards))}}
        reducible_total = dict.fromkeys(removed_total, 0)
        path = []
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
            for k, card in enumerate(spec.cards):
                charges = [
                    int(card.obligation[t]) if card.obligation is not None else 0,
                    int(card.other[t]) if card.other is not None else 0,
                    spend[f"card{k}"],
                ]
                for amount in charges:
                    charged = min(amount, max(card.limit - used[k], 0))
                    balance -= amount - charged
                    used[k] += charged
                    due = int(card.due_of_day[t])
                    if due >= 0:
                        bills[k][due] = bills[k].get(due, 0) + charged
            balance -= int(spec.later[t])
            balance -= spend["cash"]
            path.append(balance)
        paths.append(path)
    return paths


def _spec(rng: random.Random, *, cards: int, days: int, cut: bool = False) -> Spec:
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
                other=np.zeros(total, np.int64),
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


def test_the_numpy_draws_match_the_python_oracle_bit_for_bit():
    days = np.array([0, 1, 739_000, 2**40], dtype=np.int64)
    values = kernel._draws(20261103, 9, 5, days)
    for scenario in range(5):
        for column, day in enumerate(days):
            assert int(values[scenario, column]) == kernel.draw(20261103, 9, scenario, int(day))


@pytest.mark.parametrize("block_length", [1, 3])
@pytest.mark.parametrize("cards", [0, 2])
def test_the_kernel_matches_a_plain_day_by_day_walk(block_length, cards, monkeypatch):
    # Small chunks prove that state carries across chunk edges exactly.
    monkeypatch.setattr(kernel, "CHUNK_DAYS", 17)
    rng = random.Random(block_length * 10 + cards)
    for trial in range(3):
        spec = _spec(rng, cards=cards, days=75, cut=trial == 1)
        settings = Settings(scenarios=6, seed=trial, block_length=block_length, keep_days=76)
        run = kernel.run(spec, settings)
        expected = reference(spec, settings)
        assert run.paths is not None
        assert run.paths.tolist() == expected
        assert run.minima.tolist() == [min(path) for path in expected]
        breached = [
            [min(path[: t + 1]) < spec.floor for path in expected] for t in range(spec.days + 1)
        ]
        assert run.crossings.tolist() == [sum(day) for day in breached]


def test_a_scenario_does_not_depend_on_how_many_run():
    spec = _spec(random.Random(5), cards=1, days=40)
    small = kernel.run(spec, Settings(scenarios=8, seed=3, keep_days=41))
    large = kernel.run(spec, Settings(scenarios=64, seed=3, keep_days=41))
    assert small.paths is not None and large.paths is not None
    assert large.paths[:8].tolist() == small.paths.tolist()


def test_checkpoints_and_obligations_come_from_the_same_walk():
    spec = _spec(random.Random(9), cards=1, days=60)
    settings = Settings(scenarios=32, seed=1, keep_days=61, checkpoints=(0, 10, 60))
    run = kernel.run(spec, settings)
    assert run.paths is not None and run.checkpoints is not None
    assert run.checkpoints.tolist() == run.paths[:, [0, 10, 60]].tolist()
    assert len(run.obligation_failures) == len(spec.obligation_days)


def _lattice_floor_probability(
    start: int, floor: int, fixed: list[int], pools: dict[int, list[int]], classes: list[int]
) -> float:
    """P(every day's balance stays at or above the floor), by an absorbing-barrier recursion."""
    states = {start: 1.0} if start >= floor else {}
    for t in range(1, len(fixed)):
        pool = pools[classes[t]]
        share = 1.0 / len(pool)
        nxt: dict[int, float] = {}
        for balance, probability in states.items():
            for amount in pool:
                value = balance + fixed[t] - amount
                if value >= floor:
                    nxt[value] = nxt.get(value, 0.0) + probability * share
        states = nxt
    return sum(states.values())


def test_a_card_free_floor_probability_matches_the_exact_lattice():
    days = 30
    first_day = 739_000
    classes = [1 if (first_day + t) % 7 in (5, 6) else 0 for t in range(days + 1)]
    weekday = [0, 1, 2, 4, 7]
    weekend = [2, 3, 5, 9]
    pool_values = weekday + weekend
    pool_classes = [0] * len(weekday) + [1] * len(weekend)
    fixed = [0] * (days + 1)
    for t in (10, 20):
        fixed[t] = 25
    start, floor = 60, 0
    exact = _lattice_floor_probability(start, floor, fixed, {0: weekday, 1: weekend}, classes)

    total = days + 1
    spec = Spec(
        start_balance=start,
        days=days,
        first_day=first_day,
        classes=np.array(classes, np.int8),
        inflow=np.array(fixed, np.int64),
        obligation=np.zeros(total, np.int64),
        later=np.zeros(total, np.int64),
        obligation_days=np.zeros(0, np.int64),
        unconfirmed=np.zeros(total, np.int64),
        floor=floor,
        pools=(
            Pool(
                columns={"cash": np.array(pool_values, np.int64)},
                classes=np.array(pool_classes, np.int8),
                stream=1,
            ),
        ),
    )
    run = kernel.run(spec, Settings(scenarios=16_384, seed=11))
    estimate = float(np.count_nonzero(run.minima >= floor)) / run.scenarios
    error = math.sqrt(exact * (1 - exact) / run.scenarios)
    assert 0.2 < exact < 0.8
    assert abs(estimate - exact) < 4 * error


def test_a_cut_removes_a_rounded_share_of_reducible_spend_and_nothing_else():
    spec = _spec(random.Random(2), cards=1, days=50)
    base = kernel.run(spec, Settings(scenarios=4, seed=8, keep_days=51))
    cut = kernel.run(
        replace(spec, cut_bps=2_000, cut_until=50), Settings(scenarios=4, seed=8, keep_days=51)
    )
    assert base.paths is not None and cut.paths is not None
    assert (cut.paths >= base.paths).all()
