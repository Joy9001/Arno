"""The scenario kernel against two oracles: a plain-Python walk and an exact lattice recursion.

The plain walk follows one scenario at a time, one day at a time, with the same draws; it must
match the vectorized kernel to the paisa. The lattice recursion computes a card-free floor
probability exactly; the kernel must land inside its own sampling interval around it.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

import numpy as np
import pytest

from arno.engine import kernel
from arno.engine.kernel import CardSpec, Pool, Rare, SeriesSpec, Settings, Spec
from tests.fixtures.engine_kernel import _reference_walk, _spec


def test_the_numpy_draws_match_the_python_oracle_bit_for_bit():
    days = np.array([0, 1, 739_000, 2**40], dtype=np.int64)
    values = kernel._draws(20261103, 9, 5, days)
    for scenario in range(5):
        for column, day in enumerate(days):
            assert int(values[scenario, column]) == kernel.draw(20261103, 9, scenario, int(day))


@pytest.mark.parametrize("limit_spend", [True, False])
@pytest.mark.parametrize("with_series", [False, True])
@pytest.mark.parametrize("block_length", [1, 3])
@pytest.mark.parametrize("cards", [0, 2])
def test_the_kernel_matches_a_plain_day_by_day_walk(
    block_length, cards, with_series, limit_spend, monkeypatch
):
    # Small chunks prove that state carries across chunk edges exactly.
    monkeypatch.setattr(kernel, "CHUNK_DAYS", 17)
    rng = random.Random(block_length * 10 + cards + (100 if with_series else 0))
    for trial in range(3):
        spec = _spec(
            rng, cards=cards, days=75, cut=trial == 1, series=with_series, limit_spend=limit_spend
        )
        settings = Settings(scenarios=6, seed=trial, block_length=block_length, keep_days=76)
        run = kernel.run(spec, settings)
        expected, payables = _reference_walk(spec, settings)
        assert run.paths is not None
        assert run.paths.tolist() == expected
        assert run.minima.tolist() == [min(path) for path in expected]
        breached = [
            [min(path[: t + 1]) < spec.floor for path in expected] for t in range(spec.days + 1)
        ]
        assert run.crossings.tolist() == [sum(day) for day in breached]
        rank = settings.scenarios // 100
        obligation_days = kernel.obligation_days(spec)
        expected_failures = [
            sum(1 for row in payables if row[int(day)] < 0) for day in obligation_days
        ]
        expected_margins = [
            sorted(row[int(day)] for row in payables)[rank] for day in obligation_days
        ]
        assert run.obligation_failures.tolist() == expected_failures
        assert run.obligation_margins.tolist() == expected_margins


def test_a_scenario_does_not_depend_on_how_many_run():
    spec = _spec(random.Random(5), cards=1, days=40)
    small = kernel.run(spec, Settings(scenarios=8, seed=3, keep_days=41))
    large = kernel.run(spec, Settings(scenarios=64, seed=3, keep_days=41))
    assert small.paths is not None and large.paths is not None
    assert large.paths[:8].tolist() == small.paths.tolist()


def test_checkpoints_and_obligations_come_from_the_same_walk():
    spec = _spec(random.Random(9), cards=1, days=60)
    settings = Settings(scenarios=250, seed=1, keep_days=61, checkpoints=(0, 10, 60))
    run = kernel.run(spec, settings)
    assert run.paths is not None and run.checkpoints is not None
    assert run.checkpoints.tolist() == run.paths[:, [0, 10, 60]].tolist()
    assert len(run.obligation_failures) == len(kernel.obligation_days(spec))
    assert len(run.obligation_margins) == len(kernel.obligation_days(spec))
    _paths, payables = _reference_walk(spec, settings)
    rank = settings.scenarios // 100
    assert rank == 2
    assert run.obligation_margins.tolist() == [
        sorted(row[int(day)] for row in payables)[rank] for day in kernel.obligation_days(spec)
    ]


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


def test_the_reducible_measure_reads_what_was_there_before_the_cut():
    # Two reducible cash series: 20,000 on day 9 and 10,000 on day 18. A 50% cut halves what
    # leaves cash, but the measure a cut acts on is the uncut spend, so the day-15 checkpoint
    # holds day 9's 20,000 in full and the day-30 checkpoint adds day 18's 10,000.
    first_day = 739_000
    days = 30
    total = days + 1
    jitter = np.array([0], dtype=np.int64)
    cumulative = np.array([1 << 32], dtype=np.uint64)
    series = tuple(
        SeriesSpec(
            ordinals=np.array([first_day + day], dtype=np.int64),
            alive_threshold=1 << 32,
            skip_threshold=0,
            jitter_offsets=jitter,
            jitter_cumulative=cumulative,
            amounts=np.array([amount], dtype=np.int64),
            reducible=True,
            stream=100 + index,
        )
        for index, (day, amount) in enumerate(((9, 20_000), (18, 10_000)))
    )
    spec = Spec(
        start_balance=100_000,
        days=days,
        first_day=first_day,
        classes=np.zeros(total, np.int8),
        inflow=np.zeros(total, np.int64),
        obligation=np.zeros(total, np.int64),
        later=np.zeros(total, np.int64),
        obligation_days=np.zeros(0, np.int64),
        unconfirmed=np.zeros(total, np.int64),
        floor=0,
        series=series,
        cut_bps=5_000,
        cut_until=29,
    )
    run = kernel.run(
        spec,
        Settings(scenarios=8, seed=1, keep_days=total, checkpoints=(15, 30), reducible_until=20),
    )
    assert run.paths is not None and run.reducible is not None
    assert (run.paths[:, 9] == 90_000).all()
    assert (run.paths[:, 18] == 85_000).all()
    assert (run.reducible[:, 0] == 20_000).all()
    assert (run.reducible[:, 1] == 30_000).all()


def test_a_rare_day_replaces_its_day_pool_instead_of_adding_onto_it():
    first_day = 739_000
    days = 20
    total = days + 1
    spec = Spec(
        start_balance=100_000,
        days=days,
        first_day=first_day,
        classes=np.zeros(total, np.int8),
        inflow=np.zeros(total, np.int64),
        obligation=np.zeros(total, np.int64),
        later=np.zeros(total, np.int64),
        obligation_days=np.zeros(0, np.int64),
        unconfirmed=np.zeros(total, np.int64),
        floor=0,
        pools=(
            Pool(
                columns={"cash": np.array([1_000], dtype=np.int64)},
                classes=np.array([0], dtype=np.int8),
                stream=0,
            ),
        ),
        rare=Rare(
            columns={"cash": np.array([5_000], dtype=np.int64)},
            events=1,
            exposure_days=2,
            stream=999,
            replaces_stream=0,
        ),
    )
    run = kernel.run(spec, Settings(scenarios=32, seed=4, keep_days=total))
    assert run.paths is not None
    drops = run.paths[:, :-1] - run.paths[:, 1:]
    unique_drops = set(np.unique(drops).tolist())
    assert unique_drops == {1_000, 5_000}


def test_a_rare_day_adds_when_no_pool_matches_replaces_stream():
    first_day = 739_000
    days = 20
    total = days + 1
    spec = Spec(
        start_balance=200_000,
        days=days,
        first_day=first_day,
        classes=np.zeros(total, np.int8),
        inflow=np.zeros(total, np.int64),
        obligation=np.zeros(total, np.int64),
        later=np.zeros(total, np.int64),
        obligation_days=np.zeros(0, np.int64),
        unconfirmed=np.zeros(total, np.int64),
        floor=0,
        pools=(
            Pool(
                columns={"cash": np.array([1_000], dtype=np.int64)},
                classes=np.array([0], dtype=np.int8),
                stream=1,
            ),
        ),
        rare=Rare(
            columns={"cash": np.array([5_000], dtype=np.int64)},
            events=1,
            exposure_days=2,
            stream=999,
            replaces_stream=0,
        ),
    )
    run = kernel.run(spec, Settings(scenarios=32, seed=4, keep_days=total))
    assert run.paths is not None
    drops = run.paths[:, :-1] - run.paths[:, 1:]
    unique_drops = set(np.unique(drops).tolist())
    assert unique_drops == {1_000, 6_000}


@pytest.mark.parametrize("limit_spend", [True, False])
def test_resampled_spend_keeps_its_card_and_only_new_charges_meet_the_limit(limit_spend):
    days = 6
    total = days + 1
    other = np.zeros(total, np.int64)
    other[3] = 5_000
    card = CardSpec(
        limit=10_000,
        used=5_000,
        due_of_day=np.full(total, -1, np.int64),
        other=other,
        limit_spend=limit_spend,
    )
    spec = Spec(
        start_balance=100_000,
        days=days,
        first_day=739_000,
        classes=np.zeros(total, np.int8),
        inflow=np.zeros(total, np.int64),
        obligation=np.zeros(total, np.int64),
        later=np.zeros(total, np.int64),
        obligation_days=np.zeros(0, np.int64),
        unconfirmed=np.zeros(total, np.int64),
        floor=0,
        cards=(card,),
        pools=(
            Pool(
                columns={"card0": np.array([2_000], dtype=np.int64)},
                classes=np.array([0], dtype=np.int8),
                stream=0,
            ),
        ),
    )
    run = kernel.run(spec, Settings(scenarios=4, seed=1, keep_days=total))
    assert run.paths is not None
    drops = (run.paths[:, :-1] - run.paths[:, 1:])[0].tolist()
    assert run.credit_exceeded
    if limit_spend:
        # Two days of spend fit; on day 3 the spend and the purchase share the last 1,000.
        assert drops == [0, 0, 6_000, 2_000, 2_000, 2_000]
    else:
        # Spend stays on the card. By day 3 it has used 9,000 of the 10,000 limit, so the
        # day-3 purchase gets the last 1,000 and 4,000 leaves cash; then the spend passes the
        # limit on the card.
        assert drops == [0, 0, 4_000, 0, 0, 0]


def test_below_requires_a_bound_under_two_to_the_32():
    draws = np.array([0, 1, 2**63, 2**64 - 1], dtype=np.uint64)
    assert kernel.below(draws, 2**32 - 1).max() < 2**32 - 1
    for bound in (0, -1, 2**32):
        with pytest.raises(AssertionError, match="2\\*\\*32"):
            kernel.below(draws, bound)


def test_a_card_bill_due_day_and_a_day_zero_obligation_are_gated():
    days = 10
    total = days + 1
    card = CardSpec(
        limit=100_000,
        used=50_000,
        due_of_day=np.full(total, -1, np.int64),
        bills={5: 50_000},
    )
    spec = Spec(
        start_balance=10_000,
        days=days,
        first_day=739_000,
        classes=np.zeros(total, np.int8),
        inflow=np.zeros(total, np.int64),
        obligation=np.zeros(total, np.int64),
        later=np.zeros(total, np.int64),
        obligation_days=np.zeros(0, np.int64),
        unconfirmed=np.zeros(total, np.int64),
        floor=-100_000,
        cards=(card,),
    )
    run = kernel.run(spec, Settings(scenarios=8, seed=1))
    assert kernel.obligation_days(spec).tolist() == [5]
    assert run.obligation_failures.tolist() == [8]

    today = replace(
        spec,
        cards=(),
        obligation=np.array([15_000, *([0] * days)], np.int64),
        obligation_days=np.array([0], np.int64),
    )
    run = kernel.run(today, Settings(scenarios=8, seed=1))
    assert kernel.obligation_days(today).tolist() == [0]
    assert run.obligation_failures.tolist() == [8]


def test_card_obligations_are_charged_before_loose_spend():
    # The card has 10,000 of room; day 1's 8,000 obligation gets it before the 5,000 of
    # discretionary spend, which then keeps the card past its limit instead of crowding the
    # obligation out into cash.
    days = 3
    total = days + 1
    obligation = np.zeros(total, np.int64)
    obligation[1] = 8_000
    card = CardSpec(
        limit=10_000,
        used=0,
        due_of_day=np.full(total, -1, np.int64),
        obligation=obligation,
        limit_spend=False,
    )
    spec = Spec(
        start_balance=100_000,
        days=days,
        first_day=739_000,
        classes=np.zeros(total, np.int8),
        inflow=np.zeros(total, np.int64),
        obligation=np.zeros(total, np.int64),
        later=np.zeros(total, np.int64),
        obligation_days=np.array([1], np.int64),
        unconfirmed=np.zeros(total, np.int64),
        floor=0,
        cards=(card,),
        pools=(
            Pool(
                columns={"card0": np.array([5_000], dtype=np.int64)},
                classes=np.array([0], dtype=np.int8),
                stream=0,
            ),
        ),
    )
    run = kernel.run(spec, Settings(scenarios=4, seed=1, keep_days=total))
    assert run.paths is not None
    assert run.obligation_failures.tolist() == [0]
    assert (run.paths == 100_000).all()
    assert run.credit_exceeded
