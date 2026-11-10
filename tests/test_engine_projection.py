"""Pure projection tests: the walk's order, card cycles, goals, resampling, and determinism."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fin_agent.engine.projection import (
    CardAccount,
    CashEvent,
    CurvePoint,
    GoalPlan,
    LiquidAccount,
    Projection,
    ProjectionInput,
    SpendCategory,
    first_full_payment_date,
    project,
    safe_amount_today,
)


def _inputs(
    *,
    start: int = 1_000_000,
    floor: int = 0,
    events: tuple[CashEvent, ...] = (),
    cards: tuple[CardAccount, ...] = (),
    goals: tuple[GoalPlan, ...] = (),
    categories: tuple[SpendCategory, ...] = (),
    as_of: date = date(2026, 1, 1),
) -> ProjectionInput:
    return ProjectionInput(
        as_of=as_of,
        floor_paise=floor,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=start, as_of=as_of),),
        events=events,
        cards=cards,
        goals=goals,
        categories=categories,
        ask_horizon_days=90,
    )


def _by_date(result: Projection) -> dict[str, CurvePoint]:
    return {point["date"]: point for point in result["curve"]}


def test_horizon_is_at_least_ninety_days():
    result = project(_inputs(), runs=50)
    assert result["horizon_days"] >= 90
    assert len(result["curve"]) == result["horizon_days"] + 1


def test_bands_wrap_the_median_and_floor_probability_is_a_share():
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=5_000,
        history=tuple(
            (date(2025, 12, 1) + timedelta(days=index), 5_000 if index % 2 else 0)
            for index in range(60)
        ),
    )
    result = project(_inputs(categories=(category,)), runs=200, seed=7)
    for point in result["curve"]:
        assert point["p10_paise"] <= point["median_paise"] <= point["p90_paise"]
    assert 0.0 <= result["floor_probability"] <= 1.0
    assert result["runs"] == 200
    assert result["seed"] == 7


def test_same_seed_same_curve_and_a_different_seed_moves_it():
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=5_000,
        history=tuple(
            (date(2025, 12, 1) + timedelta(days=index), (index * 977) % 40_000)
            for index in range(60)
        ),
    )
    one = project(_inputs(categories=(category,)), runs=120, seed=1)
    two = project(_inputs(categories=(category,)), runs=120, seed=1)
    other = project(_inputs(categories=(category,)), runs=120, seed=2)
    assert one["curve"] == two["curve"]
    assert one["curve"] != other["curve"]


def test_income_is_applied_before_an_obligation_on_the_same_day():
    result = project(
        _inputs(
            start=100_000,
            events=(
                CashEvent(
                    date=date(2026, 1, 5),
                    amount_paise=100_000,
                    name="Salary",
                    kind="income_fixed",
                ),
                CashEvent(
                    date=date(2026, 1, 5),
                    amount_paise=-40_000,
                    name="Rent",
                    kind="obligation",
                ),
            ),
        ),
        runs=20,
    )
    assert _by_date(result)["2026-01-05"]["median_paise"] == 160_000


def test_card_outstanding_and_new_card_charges_are_paid_on_the_due_date():
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=5,
        due_day=10,
        outstanding_paise=50_000,
        credit_limit_paise=1_000_000,
    )
    result = project(
        _inputs(
            start=1_000_000,
            cards=(card,),
            events=(
                CashEvent(
                    date=date(2026, 1, 3),
                    amount_paise=-30_000,
                    name="Card bill",
                    kind="obligation",
                    card_id="card",
                ),
            ),
        ),
        runs=20,
    )
    curve = _by_date(result)
    assert curve["2026-01-03"]["median_paise"] == 1_000_000
    assert curve["2026-01-09"]["median_paise"] == 1_000_000
    assert curve["2026-01-10"]["median_paise"] == 920_000


def test_card_due_cycle_rolls_to_next_month_when_due_day_precedes_statement_day():
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=25,
        due_day=5,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    result = project(
        _inputs(
            start=500_000,
            cards=(card,),
            events=(
                CashEvent(
                    date=date(2026, 1, 10),
                    amount_paise=-100_000,
                    name="Card bill",
                    kind="obligation",
                    card_id="card",
                ),
            ),
        ),
        runs=20,
    )
    curve = _by_date(result)
    assert curve["2026-01-10"]["median_paise"] == 500_000
    assert curve["2026-02-05"]["median_paise"] == 400_000


def test_goal_contribution_leaves_liquid_cash_and_completes_the_goal():
    goal = GoalPlan(
        id="goal",
        name="Goa",
        target_paise=60_000,
        saved_paise=40_000,
        deadline=date(2026, 3, 1),
    )
    result = project(
        _inputs(
            start=200_000,
            goals=(goal,),
            events=(
                CashEvent(
                    date=date(2026, 1, 10),
                    amount_paise=-20_000,
                    name="Goa contribution",
                    kind="goal_contribution",
                    goal_id="goal",
                ),
            ),
        ),
        runs=20,
    )
    assert _by_date(result)["2026-01-10"]["median_paise"] == 180_000
    row = result["goals"][0]
    assert row["completes_on"] == "2026-01-10"
    assert row["slips_days"] == 0
    assert row["reached"] is True


def test_unconfirmed_variable_income_is_only_an_optimistic_case():
    result = project(
        _inputs(
            start=20_000,
            floor=10_000,
            events=(
                CashEvent(
                    date=date(2026, 1, 10),
                    amount_paise=50_000,
                    name="Freelance",
                    kind="income_variable",
                    confirmed=False,
                ),
                CashEvent(
                    date=date(2026, 1, 20),
                    amount_paise=-50_000,
                    name="Rent",
                    kind="obligation",
                ),
            ),
        ),
        runs=50,
    )
    assert result["floor_probability"] == 0.0
    optimistic = result["optimistic"]
    assert optimistic is not None
    assert optimistic["floor_probability"] == 1.0
    assert optimistic["income"][0]["name"] == "Freelance"


def test_a_category_with_too_few_real_days_falls_back_to_a_flat_estimate():
    sparse = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=10_000,
        history=(
            (date(2025, 12, 1), 10_000),
            (date(2025, 12, 2), 10_000),
            (date(2025, 12, 3), 10_000),
        ),
    )
    result = project(_inputs(categories=(sparse,)), runs=20)
    assert "Food" in result["data_quality"]["simulation"]["categories_flat"]
    assert result["data_quality"]["simulation"]["categories_sampled"] == []
    assert _by_date(result)["2026-01-02"]["median_paise"] == 990_000


def test_weekend_history_is_resampled_from_weekend_days():
    history = []
    for index in range(90):
        day = date(2025, 10, 1) + timedelta(days=index)
        history.append((day, 100_000 if day.weekday() >= 5 else 100))
    category = SpendCategory(
        id="cat", name="Weekend", baseline_per_day_paise=10_000, history=tuple(history)
    )
    # 2026-01-03 is a Saturday.
    result = project(_inputs(as_of=date(2026, 1, 2), categories=(category,)), runs=200)
    assert _by_date(result)["2026-01-03"]["median_paise"] < 950_000


def test_owed_money_with_an_expected_date_is_a_cash_event():
    result = project(
        _inputs(
            start=0,
            floor=0,
            events=(
                CashEvent(
                    date=date(2026, 1, 7),
                    amount_paise=50_000,
                    name="Ravi repayment",
                    kind="owed_receipt",
                ),
            ),
        ),
        runs=20,
    )
    assert _by_date(result)["2026-01-06"]["median_paise"] == 0
    assert _by_date(result)["2026-01-07"]["median_paise"] == 50_000


def test_safe_amount_and_first_full_payment_date_use_the_tenth_percentile():
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=0,
        history=tuple((date(2025, 12, 1) + timedelta(days=index), 0) for index in range(60)),
    )
    inputs = _inputs(start=100_000, floor=50_000, categories=(category,))
    assert safe_amount_today(inputs, runs=50) == 50_000
    found = first_full_payment_date(inputs, 100_000, runs=50)
    assert found is None
    found = first_full_payment_date(inputs, 50_000, runs=50)
    assert found is not None
    assert found["date"] == "2026-01-01"


def test_first_full_payment_date_returns_none_when_a_past_day_already_breaks_the_floor():
    inputs = _inputs(
        start=100_000,
        floor=0,
        events=(
            CashEvent(
                date=date(2026, 1, 5),
                amount_paise=-150_000,
                name="Rent",
                kind="obligation",
            ),
            CashEvent(
                date=date(2026, 1, 20),
                amount_paise=300_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
    )
    assert first_full_payment_date(inputs, 20_000, start_on=date(2026, 1, 5), runs=20) is None


@pytest.mark.parametrize("runs", [1, 2, 25, 100])
def test_curve_length_is_stable_for_any_run_count(runs: int):
    result = project(_inputs(), runs=runs)
    assert len(result["curve"]) == result["horizon_days"] + 1
