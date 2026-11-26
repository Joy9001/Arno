"""Pure projection tests: the walk's order, card cycles, goals, resampling, and determinism."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from arno.engine.affordability import Hypothetical, afford
from arno.engine.inputs import CategoryRow, LedgerSnapshot, build_inputs
from arno.engine.money import MAX_PAISE
from arno.engine.projection import (
    MAX_CUT_RATIO,
    CardAccount,
    CashEvent,
    CurvePoint,
    GoalPlan,
    LiquidAccount,
    Projection,
    ProjectionInput,
    SpendCategory,
    SpendRow,
    card_bills,
    horizon_days,
    prepare,
    project,
    reduce_categories,
)
from arno.tools.projection import load_inputs


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


def test_a_category_with_too_few_real_days_gets_a_spread_around_its_estimate():
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
    # Three real days of 10,000 and pseudo-days at 0 to 2 times the 10,000 estimate: the first
    # day's spend is uncertain, not a flat 10,000, and the band shows it.
    first = _by_date(result)["2026-01-02"]
    assert first["p10_paise"] < first["median_paise"] < first["p90_paise"]
    assert 980_000 <= first["p10_paise"] and first["p90_paise"] <= 1_000_000


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


def test_safe_amount_uses_the_tenth_percentile():
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=0,
        history=tuple((date(2025, 12, 1) + timedelta(days=index), 0) for index in range(60)),
    )
    inputs = _inputs(start=100_000, floor=50_000, categories=(category,))
    assert project(inputs, runs=50)["safe_to_spend"] == {
        "amount_paise": 50_000,
        "binding": "floor",
        "binding_date": "2026-01-01",
    }


@pytest.mark.parametrize("runs", [1, 2, 25, 100])
def test_curve_length_is_stable_for_any_run_count(runs: int):
    result = project(_inputs(), runs=runs)
    assert len(result["curve"]) == result["horizon_days"] + 1


def test_mature_computed_sparse_categories_join_the_day_pool():
    as_of = date(2026, 1, 1)
    start = as_of - timedelta(days=90)
    groceries_history = tuple(
        (start + timedelta(days=i), 50_000 + (i % 5) * 5_000) for i in range(90)
    )
    family_days = {
        start + timedelta(days=5): 5_000,
        start + timedelta(days=25): 6_000,
        start + timedelta(days=45): 5_500,
        start + timedelta(days=65): 6_850_000,
    }
    family_history = tuple(
        (start + timedelta(days=i), family_days.get(start + timedelta(days=i), 0))
        for i in range(90)
    )
    spend_rows = tuple(
        [
            *(
                SpendRow(day, "food", None, amount)
                for day, amount in groceries_history
                if amount > 0
            ),
            *(SpendRow(day, "family", None, amount) for day, amount in family_days.items()),
        ]
    )
    inputs = ProjectionInput(
        as_of=as_of,
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=10_000_000, as_of=as_of),),
        categories=(
            SpendCategory(
                id="food",
                name="Groceries",
                baseline_per_day_paise=60_000,
                baseline_source="computed",
                history=groceries_history,
            ),
            SpendCategory(
                id="family",
                name="Family",
                baseline_per_day_paise=sum(family_days.values()) // 90,
                baseline_source="computed",
                history=family_history,
            ),
        ),
        spend=spend_rows,
        history_start=start,
        ask_horizon_days=90,
    )
    result = project(inputs, runs=64, seed=3)
    sim = result["data_quality"]["simulation"]
    assert "Groceries" in sim["categories_sampled"]
    assert "Family" in sim["categories_sampled"]
    assert "Family" not in sim["categories_flat"]
    assert len(sim["rare_days"]) == 1
    assert sim["rare_days"][0]["date"] == (start + timedelta(days=65)).isoformat()

    short = ProjectionInput(
        as_of=as_of,
        floor_paise=0,
        liquid=inputs.liquid,
        categories=inputs.categories,
        spend=spend_rows,
        history_start=as_of - timedelta(days=10),
        ask_horizon_days=90,
    )
    short_result = project(short, runs=64, seed=3)
    assert "Family" in short_result["data_quality"]["simulation"]["categories_flat"]
    assert "Family" not in short_result["data_quality"]["simulation"]["categories_sampled"]


async def test_safe_to_spend_matches_the_affordability_boundary_on_the_demo_ledger(demo_ledger):
    runs = 100
    positive_cases = 0
    zero_cases = 0
    demo_day = date(2026, 11, 1)
    for days_back in (0, 30, 60, 90):
        inputs = await load_inputs(demo_ledger, as_of=demo_day - timedelta(days=days_back))
        for seed in (1, 2):
            result = project(inputs, runs=runs, seed=seed)
            safe = result["safe_to_spend"]
            assert safe is not None
            amount = safe["amount_paise"]
            assert amount >= 0
            assert safe["binding"] in {"floor", "obligation"}
            if amount > 0:
                positive_cases += 1
                at_limit = afford(
                    inputs,
                    Hypothetical(amount_paise=amount, date=inputs.as_of, method="account"),
                    runs=runs,
                    seed=seed,
                )
                assert at_limit["verdict"] == "yes"
                over_limit = afford(
                    inputs,
                    Hypothetical(amount_paise=amount + 1, date=inputs.as_of, method="account"),
                    runs=runs,
                    seed=seed,
                )
                assert over_limit["verdict"] != "yes"
            else:
                zero_cases += 1
                assert (
                    result["floor_probability"] < result["floor_threshold"]
                    or not result["obligations_met"]
                )
    assert positive_cases >= 1


def _rare_charge_inputs(balance_paise: int) -> ProjectionInput:
    """Rent on day 5, salary on day 7, and a rare 60,000 weekend charge: 2026-01-03 is a Saturday.

    The history spends a paisa a day, except that one weekend day in the pool spent 60,000, so each
    weekend day of the horizon has about a 1 in 26 chance of it.
    """
    as_of = date(2026, 1, 1)
    days = [as_of - timedelta(days=offset) for offset in range(90, 0, -1)]
    big = next(day for day in reversed(days) if day.weekday() == 5)
    category = SpendCategory(
        id="cat",
        name="Misc",
        baseline_per_day_paise=1,
        baseline_source="computed",
        history=tuple((day, 60_000 if day == big else 1) for day in days),
    )
    return ProjectionInput(
        as_of=as_of,
        floor_paise=10_000,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=balance_paise),),
        events=(
            CashEvent(
                date=date(2026, 1, 6),
                amount_paise=-50_000,
                name="Rent",
                kind="obligation",
            ),
            CashEvent(
                date=date(2026, 1, 8),
                amount_paise=200_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
        categories=(category,),
        ask_horizon_days=90,
    )


def test_safe_to_spend_obligation_can_be_the_binding_constraint():
    # Over 90% of scenarios never draw the rare weekend charge, keeping floor headroom near 60,000
    # above the 10,000 floor. The few that draw it (more than the 1% allowed obligation misses)
    # tighten the day-5 obligation margin to 9,997 before salary arrives on day 7; the other
    # 3 paise are the history's everyday spend.
    inputs = _rare_charge_inputs(120_000)
    runs = 200
    result = project(inputs, runs=runs, seed=1)
    safe = result["safe_to_spend"]
    assert safe == {
        "amount_paise": 9_997,
        "binding": "obligation",
        "binding_date": "2026-01-06",
    }
    assert (
        afford(
            inputs,
            Hypothetical(amount_paise=9_997, date=inputs.as_of, method="account"),
            runs=runs,
            seed=1,
        )["verdict"]
        == "yes"
    )
    assert (
        afford(
            inputs,
            Hypothetical(amount_paise=9_998, date=inputs.as_of, method="account"),
            runs=runs,
            seed=1,
        )["verdict"]
        != "yes"
    )


def test_safe_to_spend_returns_zero_with_binding_constraint_when_baseline_fails():
    floor_failing = _inputs(start=50_000, floor=100_000)
    assert project(floor_failing, runs=50)["safe_to_spend"] == {
        "amount_paise": 0,
        "binding": "floor",
        "binding_date": "2026-01-01",
    }

    obligation_failing = _rare_charge_inputs(105_000)
    assert project(obligation_failing, runs=200, seed=1)["safe_to_spend"] == {
        "amount_paise": 0,
        "binding": "obligation",
        "binding_date": "2026-01-06",
    }


def test_safe_to_spend_is_zero_when_a_passing_baseline_has_no_headroom():
    # With this seed four or more of 37 scenarios draw the rare charge and end exactly on the
    # floor, so the floor holds at the baseline but one paisa would put them below it.
    inputs = _rare_charge_inputs(120_005)
    result = project(inputs, runs=37, seed=2)
    assert result["floor_probability"] >= result["floor_threshold"]
    assert result["safe_to_spend"] == {
        "amount_paise": 0,
        "binding": "floor",
        "binding_date": "2026-01-07",
    }
    assert (
        afford(
            inputs,
            Hypothetical(amount_paise=1, date=inputs.as_of, method="account"),
            runs=37,
            seed=2,
        )["verdict"]
        != "yes"
    )


def test_safe_to_spend_is_none_when_no_liquid_balance_is_known():
    inputs = ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=10_000,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=100_000, known=False),),
        ask_horizon_days=90,
    )
    assert project(inputs, runs=20)["safe_to_spend"] is None


def test_existing_projection_outputs_are_unchanged_by_safe_to_spend():
    inputs = _inputs(
        start=100_000,
        floor=50_000,
        events=(
            CashEvent(
                date=date(2026, 1, 10),
                amount_paise=-20_000,
                name="Rent",
                kind="obligation",
            ),
            CashEvent(
                date=date(2026, 1, 20),
                amount_paise=40_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
    )
    result = project(inputs, runs=50, seed=1)
    assert result["floor_probability"] == 1.0
    assert result["median_low"] == {"date": "2026-01-10", "paise": 80_000}
    assert result["p10_low_paise"] == 80_000
    assert result["first_floor_crossing"] is None
    assert result["obligations_met"] is True
    assert result["safe_to_spend"] == {
        "amount_paise": 30_000,
        "binding": "floor",
        "binding_date": "2026-01-10",
    }


def test_amounts_beyond_the_signed_64_bit_balance_are_rejected():
    oversized = MAX_PAISE + 1
    cases = (
        _inputs(start=oversized),
        _inputs(floor=oversized),
        _inputs(
            events=(
                CashEvent(
                    date=date(2026, 1, 10),
                    amount_paise=MAX_PAISE // 2 + 1,
                    name="Large inflow",
                    kind="income_oneoff",
                ),
            )
            * 2
        ),
        _inputs(
            events=(
                CashEvent(
                    date=date(2026, 1, 10),
                    amount_paise=oversized,
                    name="Huge inflow",
                    kind="income_oneoff",
                ),
            )
        ),
    )
    for inputs in cases:
        with pytest.raises(ValueError, match="supported maximum"):
            project(inputs, runs=5)


def test_card_bills_follow_the_statement_cycle_and_clamp_the_overdue_day():
    split = CardAccount(
        id="card",
        name="Card",
        statement_day=5,
        due_day=25,
        outstanding_paise=100_000,
        credit_limit_paise=500_000,
        unbilled_paise=30_000,
    )
    assert card_bills(split, date(2026, 1, 10)) == (
        (date(2026, 1, 25), 70_000),
        (date(2026, 2, 25), 30_000),
    )
    # The closed bill's due day has passed: it is paid today, the unbilled part keeps its cycle.
    assert card_bills(split, date(2026, 1, 30)) == (
        (date(2026, 1, 30), 70_000),
        (date(2026, 2, 25), 30_000),
    )


def test_an_unknown_card_split_uses_the_same_due_date_cycle_at_month_ends():
    unknown = CardAccount(
        id="card",
        name="Card",
        statement_day=31,
        due_day=31,
        outstanding_paise=50_000,
        credit_limit_paise=500_000,
    )
    # A February close clamps to the 28th, so its bill is due on March 31, not February 28.
    assert card_bills(unknown, date(2026, 2, 28)) == ((date(2026, 3, 31), 50_000),)
    # Closed on January 31 and due February 28: still upcoming, so it keeps that day.
    assert card_bills(unknown, date(2026, 2, 27)) == ((date(2026, 2, 28), 50_000),)

    due_today = CardAccount(
        id="card",
        name="Card",
        statement_day=5,
        due_day=28,
        outstanding_paise=50_000,
        credit_limit_paise=500_000,
    )
    assert card_bills(due_today, date(2026, 2, 28)) == ((date(2026, 2, 28), 50_000),)


def test_a_category_split_over_two_cards_uses_each_share_in_the_day_pool():
    cards = (
        CardAccount(
            id="a",
            name="A",
            statement_day=5,
            due_day=25,
            outstanding_paise=0,
            credit_limit_paise=1_000_000,
        ),
        CardAccount(
            id="b",
            name="B",
            statement_day=5,
            due_day=25,
            outstanding_paise=0,
            credit_limit_paise=1_000_000,
        ),
    )
    history = tuple((date(2025, 10, 3) + timedelta(days=index), 10_000) for index in range(90))
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=10_000,
        card_shares=(("a", 0.5), ("b", 0.5)),
        history=history,
    )
    prepared = prepare(_inputs(cards=cards, categories=(category,)))
    columns = prepared.spec.pools[0].columns
    assert set(columns["card0"].tolist()) == {5_000}
    assert set(columns["card1"].tolist()) == {5_000}
    assert not columns["cash"].any()

    direct = replace(category, card_shares=(), card_id="b", card_share=0.25)
    prepared = prepare(_inputs(cards=cards, categories=(direct,)))
    columns = prepared.spec.pools[0].columns
    assert "card0" not in columns
    assert set(columns["card1"].tolist()) == {2_500}
    assert set(columns["cash"].tolist()) == {7_500}


def test_a_whole_day_pool_keeps_the_card_each_payment_actually_used():
    as_of = date(2026, 1, 1)
    start = as_of - timedelta(days=90)
    days = [start + timedelta(days=index) for index in range(90)]
    spend = tuple(
        [SpendRow(day, "cat", "a", 200_000) for day in days[-20:]]
        + [SpendRow(day, "cat", "b", 100_000) for day in days[-20:]]
    )
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=300_000,
        baseline_source="computed",
        card_shares=(("a", 0.5), ("b", 0.5)),
        history=tuple((day, 300_000 if day in days[-20:] else 0) for day in days),
    )
    cards = (
        CardAccount(
            id="a",
            name="A",
            statement_day=5,
            due_day=25,
            outstanding_paise=0,
            credit_limit_paise=100_000_000,
        ),
        CardAccount(
            id="b",
            name="B",
            statement_day=5,
            due_day=25,
            outstanding_paise=0,
            credit_limit_paise=100_000_000,
        ),
    )
    inputs = ProjectionInput(
        as_of=as_of,
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=10_000_000),),
        cards=cards,
        categories=(category,),
        spend=spend,
        history_start=start,
        ask_horizon_days=90,
    )
    prepared = prepare(inputs)
    columns = prepared.spec.pools[0].columns
    assert int(columns["card0"].sum()) == 20 * 200_000
    assert int(columns["card1"].sum()) == 20 * 100_000
    assert not columns["cash"].any()


def test_an_overpaid_card_adds_its_credit_balance_to_headroom():
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=5,
        due_day=25,
        outstanding_paise=1_500_000,
        credit_limit_paise=2_000_000,
        credit_paise=300_000,
    )
    assert card.available_credit_paise == 800_000


def test_horizon_limit_days_caps_the_walk():
    inputs = replace(_inputs(), ask_horizon_days=1_000, horizon_limit_days=120)
    assert horizon_days(inputs) == 120
    assert len(project(inputs, runs=20)["curve"]) == 121


def test_a_zero_run_budget_is_refused():
    with pytest.raises(ValueError, match="runs must be positive"):
        project(_inputs(), runs=0)


def test_reduce_categories_clamps_the_ratio_to_the_engine_range():
    category = SpendCategory(id="cat", name="Food", baseline_per_day_paise=10_000, reducible=True)
    inputs = _inputs(categories=(category,))
    for requested, expected in ((-2.0, 0.0), (5.0, MAX_CUT_RATIO)):
        reduced, rows = reduce_categories(inputs, requested)
        assert reduced.categories[0].reduce_ratio == expected
        assert rows[0]["percent"] == round(expected * 100, 1)


def test_a_derived_baseline_needs_three_rows_on_two_days():
    def snapshot(spend: tuple[SpendRow, ...]) -> LedgerSnapshot:
        return LedgerSnapshot(
            as_of=date(2026, 1, 10),
            floor_paise=0,
            history_start=date(2026, 1, 1),
            categories=(
                CategoryRow(
                    "cat", "Food", True, baseline_per_day_paise=1_000, baseline_source="estimated"
                ),
            ),
            spend=spend,
        )

    two = snapshot(
        (
            SpendRow(date(2026, 1, 2), "cat", None, 300),
            SpendRow(date(2026, 1, 3), "cat", None, 300),
        )
    )
    three = snapshot(
        (
            SpendRow(date(2026, 1, 2), "cat", None, 300),
            SpendRow(date(2026, 1, 2), "cat", None, 200),
            SpendRow(date(2026, 1, 3), "cat", None, 400),
        )
    )
    assert build_inputs(two).categories[0].baseline_source == "estimated"
    assert build_inputs(three).categories[0].baseline_source == "computed"
