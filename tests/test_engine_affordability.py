"""Affordability: the verdict, its four tradeoffs, and the card-limit guard."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from fin_agent.engine.affordability import (
    Hypothetical,
    _cut_category_tradeoff,
    _delay_goal_tradeoff,
    afford,
)
from fin_agent.engine.projection import (
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    SpendCategory,
    project,
)


def _inputs(
    *,
    start: int = 1_000_000,
    floor: int = 0,
    events: tuple[CashEvent, ...] = (),
    cards: tuple[CardAccount, ...] = (),
    goals: tuple[GoalPlan, ...] = (),
    categories: tuple[SpendCategory, ...] = (),
) -> ProjectionInput:
    return ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=floor,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=start),),
        events=events,
        cards=cards,
        goals=goals,
        categories=categories,
        ask_horizon_days=90,
    )


def test_plenty_of_cash_is_a_yes_with_no_tradeoffs():
    result = afford(
        _inputs(start=1_000_000),
        Hypothetical(amount_paise=100_000, date=date(2026, 1, 1)),
        runs=50,
    )
    assert result["verdict"] == "yes"
    assert result["tradeoffs"] == []
    assert result["floor_probability"] == 1.0
    assert result["goal_slips"] == []


def test_a_later_salary_makes_wait_the_yes_but_tradeoff():
    result = afford(
        _inputs(
            start=50_000,
            events=(
                CashEvent(
                    date=date(2026, 2, 1),
                    amount_paise=200_000,
                    name="Salary",
                    kind="income_fixed",
                ),
            ),
        ),
        Hypothetical(amount_paise=100_000, date=date(2026, 1, 1)),
        runs=50,
    )
    assert result["verdict"] == "yes-but"
    wait = next(trade for trade in result["tradeoffs"] if trade["kind"] == "wait")
    assert wait["date"] == "2026-02-01"


def test_card_float_is_a_yes_but_when_the_bank_balance_is_short():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    result = afford(
        _inputs(
            start=50_000,
            cards=(card,),
            events=(
                CashEvent(
                    date=date(2026, 1, 20),
                    amount_paise=200_000,
                    name="Salary",
                    kind="income_fixed",
                ),
            ),
        ),
        Hypothetical(amount_paise=100_000, date=date(2026, 1, 1), method="account"),
        runs=50,
    )
    assert result["verdict"] == "yes-but"
    card_trade = next(trade for trade in result["tradeoffs"] if trade["kind"] == "pay_by_card")
    assert card_trade["method"] == "card"
    assert card_trade["due_date"] == "2026-01-25"


def test_a_card_purchase_over_the_available_limit_is_infeasible():
    card = CardAccount(
        id="card",
        name="Small card",
        statement_day=5,
        due_day=25,
        outstanding_paise=90_000,
        credit_limit_paise=100_000,
    )
    result = afford(
        _inputs(start=1_000_000, cards=(card,)),
        Hypothetical(
            amount_paise=50_000, date=date(2026, 1, 1), method="card", account="Small card"
        ),
        runs=20,
    )
    assert result["hypothetical"]["feasible"] is False
    assert "available credit" in result["verdict_reason"]
    assert result["verdict"] in {"yes-but", "no"}


def test_cutting_a_reducible_category_frees_a_known_shortfall():
    category = SpendCategory(
        id="cat_food",
        name="Food",
        baseline_per_day_paise=30_000,
        reducible=True,
        history=tuple((date(2025, 10, 1) + timedelta(days=index), 30_000) for index in range(90)),
    )
    inputs = _inputs(
        start=5_000_000,
        floor=3_000_000,
        categories=(category,),
        events=(
            CashEvent(
                date=date(2026, 1, 31),
                amount_paise=3_000_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
    )
    purchase = Hypothetical(amount_paise=1_550_000, date=date(2026, 1, 1))
    modified = replace(
        inputs,
        events=inputs.events
        + (
            CashEvent(
                date=date(2026, 1, 1),
                amount_paise=-1_550_000,
                name="purchase",
                kind="hypothetical",
                at_start=True,
            ),
        ),
    )
    projected = project(modified, runs=100)
    tradeoff = _cut_category_tradeoff(
        inputs, purchase, runs=100, seed=20261103, threshold=0.90, with_purchase=projected
    )
    assert tradeoff is not None
    assert tradeoff["kind"] == "cut_category"
    assert 0 < tradeoff["ratio"] <= 0.5
    assert tradeoff["categories"][0]["category"] == "Food"


def test_delaying_a_goal_reports_how_late_it_completes():
    goal = GoalPlan(
        id="goal",
        name="Goa",
        target_paise=60_000,
        saved_paise=0,
        deadline=date(2026, 4, 1),
    )
    inputs = _inputs(
        start=100_000,
        goals=(goal,),
        events=(
            CashEvent(
                date=date(2026, 1, 5),
                amount_paise=-60_000,
                name="Goa contribution",
                kind="goal_contribution",
                goal_id="goal",
            ),
            CashEvent(
                date=date(2026, 2, 5),
                amount_paise=-60_000,
                name="Goa contribution",
                kind="goal_contribution",
                goal_id="goal",
            ),
        ),
    )
    baseline = project(inputs, runs=50)
    tradeoff = _delay_goal_tradeoff(
        inputs,
        Hypothetical(amount_paise=20_000, date=date(2026, 1, 1)),
        runs=50,
        seed=20261103,
        threshold=0.90,
        baseline_goals=baseline["goals"],
    )
    assert tradeoff is not None
    assert tradeoff["kind"] == "delay_goal"
    assert tradeoff["slips_days"] is not None and tradeoff["slips_days"] > 0


def test_a_future_purchase_date_extends_the_horizon_to_cover_it():
    result = afford(
        _inputs(start=1_000_000),
        Hypothetical(amount_paise=100_000, date=date(2026, 6, 1)),
        runs=20,
    )
    assert result["horizon_days"] >= 152


def _card(*, limit: int = 1_000_000, outstanding: int = 0) -> CardAccount:
    return CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=outstanding,
        credit_limit_paise=limit,
    )


def test_an_emi_without_a_tenure_defaults_to_three_months_and_says_so():
    result = afford(
        _inputs(start=1_000_000, cards=(_card(),)),
        Hypothetical(amount_paise=120_000, date=date(2026, 1, 1), method="emi"),
        runs=20,
    )
    assert result["hypothetical"]["emi_months"] == 3
    assert len(result["hypothetical"]["installments"]) == 3


def test_an_emi_reports_the_users_chosen_tenure():
    result = afford(
        _inputs(start=1_000_000, cards=(_card(),)),
        Hypothetical(
            amount_paise=120_000,
            date=date(2026, 1, 1),
            method="emi",
            emi_months=6,
        ),
        runs=20,
    )
    assert result["hypothetical"]["emi_months"] == 6
    assert len(result["hypothetical"]["installments"]) == 6


def test_an_infeasible_emi_still_reports_its_effective_tenure():
    result = afford(
        _inputs(start=1_000_000, cards=(_card(limit=50_000),)),
        Hypothetical(amount_paise=120_000, date=date(2026, 1, 1), method="emi"),
        runs=10,
    )
    assert result["hypothetical"]["feasible"] is False
    assert result["hypothetical"]["emi_months"] == 3


def test_an_emi_tenure_outside_two_to_forty_eight_months_is_rejected():
    for months in (0, 1, 49):
        with pytest.raises(ValueError, match="EMI tenure"):
            afford(
                _inputs(start=1_000_000, cards=(_card(),)),
                Hypothetical(
                    amount_paise=120_000,
                    date=date(2026, 1, 1),
                    method="emi",
                    emi_months=months,
                ),
                runs=10,
            )


def test_a_non_emi_purchase_ignores_an_irrelevant_tenure():
    result = afford(
        _inputs(start=1_000_000),
        Hypothetical(
            amount_paise=100_000,
            date=date(2026, 1, 1),
            method="account",
            emi_months=6,
        ),
        runs=10,
    )
    assert result["hypothetical"]["emi_months"] is None


def test_every_verdict_is_one_of_the_three_and_yes_clears_the_threshold():
    scenarios = [
        Hypothetical(amount_paise=10_000, date=date(2026, 1, 1)),
        Hypothetical(amount_paise=9_000_000, date=date(2026, 1, 1)),
    ]
    for purchase in scenarios:
        result = afford(_inputs(start=100_000), purchase, runs=30)
        assert result["verdict"] in {"yes", "yes-but", "no"}
        if result["verdict"] == "yes":
            assert result["floor_probability"] >= result["floor_threshold"]
            assert result["tradeoffs"] == []
        if result["verdict"] == "no":
            assert result["tradeoffs"] == []


def test_an_unknown_starting_balance_gets_no_verdict_and_no_tradeoffs():
    inputs = replace(
        _inputs(start=50_000),
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=50_000, known=False),),
    )
    result = afford(
        inputs,
        Hypothetical(amount_paise=10_000, date=date(2026, 1, 1)),
        runs=20,
    )
    assert "verdict" not in result
    assert result["tradeoffs"] == []
    assert result["verdict_reason"] == (
        "No liquid account has a known balance, so there is no affordability verdict."
    )
    assert "floor_probability" in result


def test_a_card_purchase_on_an_unknown_card_is_infeasible():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
        known=False,
    )
    result = afford(
        _inputs(start=1_000_000, cards=(card,)),
        Hypothetical(
            amount_paise=100_000,
            date=date(2026, 1, 1),
            method="card",
            account="Slice card",
        ),
        runs=20,
    )
    assert result["hypothetical"]["feasible"] is False
    assert result["verdict_reason"] == (
        "Slice card has no known outstanding balance, so its available credit is unknown."
    )


def test_the_card_tradeoff_is_not_offered_for_an_unknown_card():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
        known=False,
    )
    result = afford(
        _inputs(
            start=50_000,
            cards=(card,),
            events=(
                CashEvent(
                    date=date(2026, 1, 20),
                    amount_paise=200_000,
                    name="Salary",
                    kind="income_fixed",
                ),
            ),
        ),
        Hypothetical(amount_paise=100_000, date=date(2026, 1, 1), method="account"),
        runs=50,
    )
    assert result["verdict"] == "yes-but"
    assert all(trade["kind"] != "pay_by_card" for trade in result["tradeoffs"])
