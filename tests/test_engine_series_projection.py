"""Tests for detected recurring payment series in the scenario kernel and projection."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta

import numpy as np

from arno.engine import kernel
from arno.engine.affordability import Hypothetical, afford
from arno.engine.projection import (
    LEGACY_MODEL,
    MODEL,
    CardAccount,
    CashEvent,
    LiquidAccount,
    ProjectionInput,
    SeriesForecast,
    SpendCategory,
    _series_streams,
    checkpoints_for,
    prepare,
    project,
    simulate_paths,
)

RUNS = 2_048  # fixed sample size: enough for the binomial bounds below, ~8x cheaper than production
AS_OF = date(2026, 1, 1)


def _inputs(
    *series: SeriesForecast,
    start: int = 1_000_000,
    cards: tuple[CardAccount, ...] = (),
    categories: tuple[SpendCategory, ...] = (),
    horizon: int = 90,
) -> ProjectionInput:
    return ProjectionInput(
        as_of=AS_OF,
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=start, as_of=AS_OF),),
        cards=cards,
        categories=categories,
        series=series,
        ask_horizon_days=horizon,
    )


def test_alive_probability_controls_whether_series_runs():
    series = SeriesForecast(
        id="s1",
        name="Gym",
        expected=(AS_OF + timedelta(days=10),),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=0.5,
        amounts=(1_000,),
    )
    paths = simulate_paths(_inputs(series, start=10_000), runs=RUNS, seed=7)
    paid = np.count_nonzero(paths[:, -1] == 9_000) / RUNS
    se = math.sqrt(0.5 * 0.5 / RUNS)
    assert abs(paid - 0.5) < 4 * se


def test_skip_probability_skips_expected_occurrences():
    live_series = SeriesForecast(
        id="s1",
        name="Club",
        expected=(AS_OF + timedelta(days=10),),
        jitter=((0, 1.0),),
        skip=0.3,
        alive=1.0,
        amounts=(1_000,),
    )
    paths = simulate_paths(_inputs(live_series, start=10_000), runs=RUNS, seed=11)
    paid = np.count_nonzero(paths[:, -1] == 9_000) / RUNS
    se = math.sqrt(0.7 * 0.3 / RUNS)
    assert abs(paid - 0.7) < 4 * se

    partial_series = SeriesForecast(
        id="s2",
        name="Magazine",
        expected=(AS_OF + timedelta(days=10),),
        jitter=((0, 1.0),),
        skip=0.3,
        alive=0.6,
        amounts=(1_000,),
    )
    partial_paths = simulate_paths(_inputs(partial_series, start=10_000), runs=RUNS, seed=13)
    partial_paid = np.count_nonzero(partial_paths[:, -1] == 9_000) / RUNS
    partial_expected = 0.6 * 0.7
    partial_se = math.sqrt(partial_expected * (1.0 - partial_expected) / RUNS)
    assert abs(partial_paid - partial_expected) < 4 * partial_se


def test_jitter_shifts_payment_day_by_configured_shares():
    series = SeriesForecast(
        id="s1",
        name="Water",
        expected=(AS_OF + timedelta(days=10),),
        jitter=((-1, 0.25), (0, 0.5), (1, 0.25)),
        skip=0.0,
        alive=1.0,
        amounts=(1_000,),
    )
    paths = simulate_paths(_inputs(series, start=10_000), runs=RUNS, seed=19)
    assert bool((paths[:, -1] == 9_000).all())
    drops = paths[:, :-1] - paths[:, 1:]
    for offset, expected_share in ((-1, 0.25), (0, 0.5), (1, 0.25)):
        day_index = 10 + offset
        share = np.count_nonzero(drops[:, day_index - 1] == 1_000) / RUNS
        se = math.sqrt(expected_share * (1.0 - expected_share) / RUNS)
        assert abs(share - expected_share) < 4 * se


def test_amounts_are_drawn_uniformly():
    series = SeriesForecast(
        id="s1",
        name="Topup",
        expected=(AS_OF + timedelta(days=10),),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(100, 300),
    )
    paths = simulate_paths(_inputs(series, start=10_000), runs=RUNS, seed=23)
    paid = 10_000 - paths[:, -1]
    assert set(paid.tolist()) == {100, 300}
    se = 100.0 / math.sqrt(RUNS)
    assert abs(float(paid.mean()) - 200.0) < 4 * se


def test_fixed_monthly_series_lowers_median_end_balance_and_labels_quality():
    netflix = SeriesForecast(
        id="ser_netflix",
        name="Netflix",
        expected=(
            date(2026, 1, 1),  # day 0: never lands
            date(2026, 1, 15),
            date(2026, 2, 15),
            date(2026, 3, 15),
            date(2026, 4, 15),  # past 90-day horizon (ends 2026-04-01): never lands
        ),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(64_900,),
    )
    base = project(_inputs(start=1_000_000), runs=200, seed=1)
    with_series = project(_inputs(netflix, start=1_000_000), runs=200, seed=1)
    assert base["curve"][-1]["median_paise"] == 1_000_000
    assert with_series["curve"][-1]["median_paise"] == 1_000_000 - 3 * 64_900
    assert with_series["curve"][-1]["p10_paise"] == with_series["curve"][-1]["median_paise"]
    assert with_series["curve"][-1]["p90_paise"] == with_series["curve"][-1]["median_paise"]
    assert (
        "1 detected recurring payment you have not confirmed is counted as an outflow: Netflix"
        in with_series["data_quality"]["gaps"]
    )
    assert {
        "kind": "series",
        "id": "ser_netflix",
        "name": "Netflix",
        "detail": "detected, not confirmed",
    } in with_series["sources"]


def test_multiple_series_label_plural_gap_and_sources():
    series = (
        SeriesForecast(
            id="s1",
            name="Netflix",
            expected=(date(2026, 1, 15),),
            jitter=((0, 1.0),),
            skip=0.0,
            alive=1.0,
            amounts=(64_900,),
        ),
        SeriesForecast(
            id="s2",
            name="Airtel",
            expected=(date(2026, 1, 20),),
            jitter=((0, 1.0),),
            skip=0.0,
            alive=1.0,
            amounts=(99_900,),
        ),
        SeriesForecast(
            id="s3",
            name="Gym",
            expected=(date(2026, 1, 25),),
            jitter=((0, 1.0),),
            skip=0.0,
            alive=1.0,
            amounts=(150_000,),
        ),
    )
    result = project(_inputs(*series, start=1_000_000), runs=100, seed=1)
    assert (
        "3 detected recurring payments you have not confirmed are counted as outflows: "
        "Netflix, Airtel, Gym"
    ) in result["data_quality"]["gaps"]
    for item in series:
        assert {
            "kind": "series",
            "id": item.id,
            "name": item.name,
            "detail": "detected, not confirmed",
        } in result["sources"]


def test_boundary_occurrences_respect_day_1_through_horizon():
    end_day = AS_OF + timedelta(days=90)
    never_lands = SeriesForecast(
        id="s_out",
        name="Outside",
        expected=(AS_OF, end_day + timedelta(days=1)),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(50_000,),
    )
    jittered_in = SeriesForecast(
        id="s_in",
        name="JitteredIn",
        expected=(end_day + timedelta(days=1),),
        jitter=((-1, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(25_000,),
    )
    out_result = project(_inputs(never_lands, start=500_000), runs=64, seed=1)
    assert out_result["curve"][-1]["median_paise"] == 500_000

    in_result = project(_inputs(jittered_in, start=500_000), runs=64, seed=1)
    by_date = {point["date"]: point["median_paise"] for point in in_result["curve"]}
    assert by_date[(end_day - timedelta(days=1)).isoformat()] == 500_000
    assert by_date[end_day.isoformat()] == 475_000


def test_card_series_charges_card_up_to_limit_and_bills_on_due_date():
    card = CardAccount(
        id="c1",
        name="Card",
        statement_day=20,
        due_day=25,
        outstanding_paise=70_000,
        credit_limit_paise=100_000,
        unbilled_paise=70_000,
    )
    series = SeriesForecast(
        id="s1",
        name="Phone",
        expected=(date(2026, 1, 11),),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(50_000,),
        card_id="c1",
    )
    result = project(_inputs(series, start=500_000, cards=(card,)), runs=64, seed=1)
    by_date = {point["date"]: point["median_paise"] for point in result["curve"]}
    # Headroom is 30,000, so 20,000 overflows immediately to cash on Jan 11.
    assert by_date["2026-01-10"] == 500_000
    assert by_date["2026-01-11"] == 480_000
    assert by_date["2026-01-24"] == 480_000
    # Unbilled 70,000 + charged 30,000 = 100,000 bill paid on Jan 25.
    assert by_date["2026-01-25"] == 380_000
    assert result["data_quality"]["simulation"]["credit_limit_exceeded"] is True

    # Unknown card_id falls back to cash immediately.
    unknown_card_series = SeriesForecast(
        id="s2",
        name="Other",
        expected=(date(2026, 1, 11),),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(10_000,),
        card_id="missing_card",
    )
    cash_result = project(
        _inputs(unknown_card_series, start=500_000, cards=(card,)), runs=64, seed=1
    )
    cash_by_date = {point["date"]: point["median_paise"] for point in cash_result["curve"]}
    assert cash_by_date["2026-01-11"] == 490_000


def test_a_reducible_series_never_joins_the_cut_columns():
    # A cut is a spending decision over categories; a detected charge is already committed, so
    # the loader's reducible flag is ignored and the cut leaves the series whole.
    series = SeriesForecast(
        id="s1",
        name="Streaming",
        expected=(date(2026, 1, 10),),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(20_000,),
        reducible=True,
    )
    prepared = prepare(_inputs(series, start=500_000), MODEL)
    assert prepared.spec.series[0].reducible is False

    cut_spec = replace(prepared.spec, cut_bps=5_000, cut_until=29)
    run = kernel.run(
        cut_spec,
        kernel.Settings(
            scenarios=16, seed=1, keep_days=31, checkpoints=(15, 30), reducible_until=20
        ),
    )
    assert run.paths is not None and run.reducible is not None
    assert (run.paths[:, 9] == 480_000).all()
    assert not run.reducible.any()


def test_a_cut_that_only_a_series_would_pay_for_is_not_reported():
    # The purchase leaves 50,000 and the series takes 60,000 on day 20: a 16.8% cut of that
    # charge would clear the floor, but the only cuttable category spends 1 paisa a day, so no
    # cut is offered.
    dummy = SpendCategory(
        id="cat1",
        name="Dining",
        baseline_per_day_paise=1,
        reducible=True,
        history=tuple((AS_OF - timedelta(days=index + 1), 0) for index in range(60)),
    )
    series = SeriesForecast(
        id="s1",
        name="Gym",
        expected=(date(2026, 1, 21),),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(60_000,),
        reducible=True,
    )
    inputs = ProjectionInput(
        as_of=AS_OF,
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=150_000),),
        categories=(dummy,),
        series=(series,),
        ask_horizon_days=90,
    )
    result = afford(inputs, Hypothetical(amount_paise=100_000, date=AS_OF), runs=200, seed=1)
    assert result["verdict"] == "no"
    assert all("ratio" not in trade for trade in result["tradeoffs"])


def test_checkpoints_still_capture_series_minima():
    series = SeriesForecast(
        id="s1",
        name="Utility",
        expected=(date(2026, 1, 10), date(2026, 1, 20), date(2026, 2, 10)),
        jitter=((-1, 0.3), (0, 0.4), (1, 0.3)),
        skip=0.2,
        alive=0.8,
        amounts=(15_000, 25_000),
    )
    inputs = ProjectionInput(
        as_of=AS_OF,
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=200_000, as_of=AS_OF),),
        events=(
            CashEvent(
                date=date(2026, 1, 31),
                amount_paise=100_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
        series=(series,),
        ask_horizon_days=90,
    )
    prepared = prepare(inputs, MODEL)
    marks = checkpoints_for(prepared.spec)
    run_full = kernel.run(prepared.spec, kernel.Settings(scenarios=128, seed=3, checkpoints=marks))
    assert run_full.checkpoints is not None
    assert run_full.checkpoints.min(axis=1).tolist() == run_full.minima.tolist()


def test_only_series_that_can_pay_inside_the_horizon_are_gaps_and_citations():
    # Expected today, with no jitter to move it: `prepare` drops the only date, so nothing pays.
    dead = SeriesForecast(
        id="s_dead",
        name="Gone",
        expected=(AS_OF,),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(1_000,),
    )
    result = project(_inputs(dead, start=500_000), runs=32, seed=1)
    assert not any("detected recurring payment" in gap for gap in result["data_quality"]["gaps"])
    assert all(source["id"] != "s_dead" for source in result["sources"])
    assert result["curve"][-1]["median_paise"] == 500_000

    # A repeated id cites one source, not one per forecast.
    live = replace(dead, id="s_live", name="Live", expected=(date(2026, 1, 15),))
    repeated = project(
        _inputs(live, replace(live, name="Live again"), start=500_000), runs=32, seed=1
    )
    assert [source["id"] for source in repeated["sources"]].count("s_live") == 1


def test_series_streams_never_collide_with_category_pool_streams():
    # Category pools take streams 1..n, so 105 categories pass the old fixed series base of 100.
    categories = tuple(
        SpendCategory(id=f"cat{index}", name=f"Cat {index}", baseline_per_day_paise=100)
        for index in range(105)
    )
    series = SeriesForecast(
        id="s1",
        name="Gym",
        expected=(date(2026, 1, 15),),
        jitter=((0, 1.0),),
        skip=0.0,
        alive=1.0,
        amounts=(1_000,),
    )
    prepared = prepare(_inputs(series, categories=categories), LEGACY_MODEL)
    streams = [pool.stream for pool in prepared.spec.pools]
    streams += [item.stream for item in prepared.spec.series]
    assert len(streams) == len(set(streams))
    # The rare-day stream is 999, so the series step over it.
    assert _series_streams(998, 2) == [1000, 1001]
