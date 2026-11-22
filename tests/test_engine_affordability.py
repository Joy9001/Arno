"""Affordability: the verdict, its four tradeoffs, and the card-limit guard."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from arno.engine import affordability, criticals
from arno.engine.affordability import (
    MAX_DEFERRABLE_GOALS,
    Hypothetical,
    _dominates,
    _least_cut,
    _Plan,
    afford,
    project_scenario,
)
from arno.engine.criticals import CriticalMatrix, min_hold_count
from arno.engine.projection import (
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    Projection,
    ProjectionInput,
    SeriesForecast,
    SpendCategory,
    Tradeoff,
    project,
    reduce_categories,
)
from arno.engine.scenario import ChangeAmount, StopIncome


def _inputs(
    *,
    start: int = 1_000_000,
    floor: int = 0,
    events: tuple[CashEvent, ...] = (),
    cards: tuple[CardAccount, ...] = (),
    goals: tuple[GoalPlan, ...] = (),
    categories: tuple[SpendCategory, ...] = (),
    series: tuple[SeriesForecast, ...] = (),
) -> ProjectionInput:
    return ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=floor,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=start),),
        events=events,
        cards=cards,
        goals=goals,
        categories=categories,
        series=series,
        ask_horizon_days=90,
    )


def _trade(result: Projection, kind: str) -> Tradeoff:
    return next(trade for trade in result["tradeoffs"] if trade["kind"] == kind)


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


def test_unconfirmed_income_cannot_fund_a_yes_verdict():
    inputs = _inputs(
        start=50_000,
        events=(
            CashEvent(
                date=date(2026, 1, 2),
                amount_paise=500_000,
                name="Unconfirmed salary",
                kind="income_variable",
                confirmed=False,
            ),
        ),
    )
    result = afford(inputs, Hypothetical(amount_paise=100_000, date=date(2026, 1, 3)), runs=10)
    assert result["verdict"] != "yes"
    assert result["floor_probability"] == 0.0
    # The same unconfirmed income does clear the floor when counted, so the no is its exclusion.
    assert result["optimistic"] is not None
    assert result["optimistic"]["floor_probability"] == 1.0


def test_a_goal_priority_change_revises_which_goal_the_plan_delays():
    base = _inputs(
        start=1_700_000,
        floor=1_000_000,
        goals=(
            GoalPlan(id="goal_a", name="Goa", target_paise=200_000, saved_paise=0, priority=1),
            GoalPlan(id="goal_b", name="Car", target_paise=200_000, saved_paise=0, priority=3),
        ),
        events=(
            CashEvent(
                date=date(2026, 1, 10),
                amount_paise=-200_000,
                name="Goa contribution",
                kind="goal_contribution",
                goal_id="goal_a",
            ),
            CashEvent(
                date=date(2026, 1, 10),
                amount_paise=-200_000,
                name="Car contribution",
                kind="goal_contribution",
                goal_id="goal_b",
            ),
        ),
    )
    swapped = replace(
        base,
        goals=(replace(base.goals[0], priority=3), replace(base.goals[1], priority=1)),
    )

    before = project(base, runs=50)
    after = project(swapped, runs=50)
    assert [goal["name"] for goal in before["goals"]] == ["Goa", "Car"]
    assert [goal["name"] for goal in after["goals"]] == ["Car", "Goa"]

    def amounts(result: Projection) -> dict[str, tuple[int, int, int, str | None]]:
        return {
            goal["id"]: (
                goal["target_paise"],
                goal["saved_paise"],
                goal["remaining_after_plan_paise"],
                goal["completes_on"],
            )
            for goal in result["goals"]
        }

    assert amounts(before) == amounts(after)
    assert before["median_low"] == after["median_low"]
    assert before["floor_probability"] == after["floor_probability"]

    # The baseline projection is unchanged; what changes is which goal a plan offers to delay.
    # Both contributions cost the same, so the less important goal is offered first.
    purchase = Hypothetical(amount_paise=500_000, date=date(2026, 1, 1))
    plans_before = afford(base, purchase, runs=50)
    plans_after = afford(swapped, purchase, runs=50)

    def first_delayed(result: Projection) -> str | None:
        return next(
            (t.get("goal_id") for t in result["tradeoffs"] if t["kind"] == "delay_goal"), None
        )

    assert first_delayed(plans_before) == "goal_b"
    assert first_delayed(plans_after) == "goal_a"
    assert plans_before["verdict"] == plans_after["verdict"]
    for trade in plans_before["tradeoffs"]:
        if trade["kind"] != "delay_goal":
            continue
        goal_row = next(goal for goal in plans_before["goals"] if goal["id"] == trade["goal_id"])
        assert trade["goal"] == goal_row["name"]


def test_a_later_salary_alone_keeps_the_verdict_no_and_shows_the_wait():
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
    # A later date is a caveat, not a way to afford it as asked (e4 F11/F12).
    assert result["verdict"] == "no"
    wait = next(trade for trade in result["tradeoffs"] if trade["kind"] == "wait")
    assert wait["date"] == "2026-02-01"


def test_the_wait_date_has_to_meet_an_obligation_too():
    # Balance 110, a 10 series charge on day 1, a 100 obligation on day 5 and a 500 payday on
    # day 8. The 5 purchase fits the floor on any date, but paying before the fifth can leave too
    # little for the obligation, so the first date that passes both is day 5.
    result = afford(
        _inputs(
            start=110,
            events=(
                CashEvent(
                    date=date(2026, 1, 6),
                    amount_paise=-100,
                    name="Card bill",
                    kind="obligation",
                ),
                CashEvent(
                    date=date(2026, 1, 9),
                    amount_paise=500,
                    name="Salary",
                    kind="income_fixed",
                ),
            ),
            series=(
                SeriesForecast(
                    id="s1",
                    name="Gym",
                    expected=(date(2026, 1, 2),),
                    jitter=((0, 1.0),),
                    skip=0.95,
                    alive=1.0,
                    amounts=(10,),
                ),
            ),
        ),
        Hypothetical(amount_paise=5, date=date(2026, 1, 1)),
        runs=200,
    )
    wait = _trade(result, "wait")
    assert wait["days"] >= 5
    assert wait["date"] == "2026-01-06"


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
    assert card_trade["account"] == "Slice card"
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
    assert project(modified, runs=200)["floor_probability"] < 0.9
    result = afford(inputs, purchase, runs=200)
    assert result["verdict"] == "yes-but"
    tradeoff = _trade(result, "cut_category")
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
    result = afford(inputs, Hypothetical(amount_paise=20_000, date=date(2026, 1, 1)), runs=50)
    tradeoff = _trade(result, "delay_goal")
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
            # Waiting for a later date can still be listed; nothing else may be.
            assert all(trade["kind"] == "wait" for trade in result["tradeoffs"])


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
    # The only tradeoff left is waiting for the salary, which keeps the verdict `no`.
    assert result["verdict"] == "no"
    assert all(trade["kind"] == "wait" for trade in result["tradeoffs"])


def test_stop_income_reports_first_floor_crossing_and_runway_days():
    result = project_scenario(
        _inputs(
            start=500_000,
            floor=100_000,
            events=(
                CashEvent(
                    date=date(2026, 1, 15),
                    amount_paise=1_000_000,
                    name="Salary",
                    kind="income_fixed",
                    source_id="sch_sal",
                ),
                CashEvent(
                    date=date(2026, 1, 20),
                    amount_paise=-450_000,
                    name="Rent",
                    kind="obligation",
                    source_id="sch_rent",
                ),
            ),
        ),
        [StopIncome(start=date(2026, 1, 1))],
        income_source_ids={"sch_sal"},
        runs=40,
    )
    assert result["first_floor_crossing"] == "2026-01-20"
    assert result["scenario"]["runway_days"] == 19
    assert result["scenario"]["holds"] is False
    assert result["scenario"]["baseline"]["first_floor_crossing"] is None
    assert result["scenario"]["baseline"]["floor_probability"] == 1.0


def test_purchase_plus_rent_rise_verifies_tradeoffs_against_changed_inputs():
    card = CardAccount(
        id="card_1",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    inputs = _inputs(
        start=400_000,
        floor=100_000,
        cards=(card,),
        events=(
            CashEvent(
                date=date(2026, 1, 10),
                amount_paise=-200_000,
                name="Rent",
                kind="obligation",
                source_id="sch_rent",
            ),
            CashEvent(
                date=date(2026, 1, 30),
                amount_paise=1_000_000,
                name="Salary",
                kind="income_fixed",
                source_id="sch_sal",
            ),
        ),
    )
    result = project_scenario(
        inputs,
        [
            ChangeAmount(
                source_id="sch_rent",
                amount_paise=300_000,
                start=date(2026, 1, 1),
                label_name="Rent",
            )
        ],
        hypothetical=Hypothetical(amount_paise=150_000, date=date(2026, 1, 1), method="account"),
        income_source_ids={"sch_sal"},
        runs=40,
    )
    assert result["verdict"] in {"yes-but", "no"}
    assert "holds" not in result["scenario"]
    assert result["scenario"]["baseline"]["floor_probability"] == 1.0
    assert result["tradeoffs"], "a failed account purchase must offer a later or card path"
    kinds = {trade["kind"] for trade in result["tradeoffs"]}
    assert kinds <= {"wait", "pay_by_card"}
    assert "wait" in kinds or "pay_by_card" in kinds
    for trade in result["tradeoffs"]:
        if trade["kind"] == "wait":
            assert trade["date"] >= "2026-01-30"
        if trade["kind"] == "pay_by_card":
            assert trade["method"] in {"card", "emi"}


def test_assumed_raise_is_flagged_in_scenario_assumptions():
    result = project_scenario(
        _inputs(
            start=500_000,
            floor=100_000,
            events=(
                CashEvent(
                    date=date(2026, 1, 31),
                    amount_paise=500_000,
                    name="Salary",
                    kind="income_fixed",
                    source_id="sch_sal",
                ),
            ),
        ),
        [
            ChangeAmount(
                source_id="sch_sal",
                amount_paise=800_000,
                start=date(2026, 1, 1),
                label_name="Salary",
            )
        ],
        income_source_ids={"sch_sal"},
        runs=30,
    )
    assert result["scenario"]["holds"] is True
    assert any("assumed" in note.lower() for note in result["scenario"]["assumptions"])


def test_tiny_purchase_does_not_raise_when_tradeoffs_consider_card_emi():
    card = CardAccount(
        id="card_1",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=500_000,
    )
    # 1 paisa fails account when floor=100 and start=100, forcing _tradeoffs to evaluate EMI
    # where the 3-month rounded monthly installment is 0 paise and cannot repay the loan.
    result = afford(
        _inputs(start=100, floor=100, cards=(card,)),
        Hypothetical(amount_paise=1, date=date(2026, 1, 1), method="account"),
        runs=30,
    )
    assert result["verdict"] == "no"
    assert all(trade.get("method") != "emi" for trade in result["tradeoffs"])

    asked_emi = afford(
        _inputs(start=100, floor=100, cards=(card,)),
        Hypothetical(amount_paise=1, date=date(2026, 1, 1), method="emi", emi_months=3),
        runs=30,
    )
    assert asked_emi["hypothetical"]["feasible"] is False
    assert "repay" in asked_emi["verdict_reason"].lower()


def test_plans_defer_only_the_top_eight_goals(monkeypatch):
    goals = tuple(
        GoalPlan(
            id=f"goal_{index:02d}",
            name=f"Goal {index:02d}",
            target_paise=100_000,
            saved_paise=0,
            priority=index,
        )
        for index in range(1, 16)
    )
    events = tuple(
        CashEvent(
            date=date(2026, 1, 10),
            amount_paise=-50_000,
            name=f"Goal {index:02d} contribution",
            kind="goal_contribution",
            goal_id=f"goal_{index:02d}",
        )
        for index in range(1, 16)
    )
    inputs = _inputs(start=600_000, floor=0, goals=goals, events=events)
    seen: list[tuple[str, ...]] = []
    original = affordability._goal_relief

    def spy(current, goal_ids, when):
        seen.append(goal_ids)
        return original(current, goal_ids, when)

    monkeypatch.setattr(affordability, "_goal_relief", spy)
    result = afford(inputs, Hypothetical(amount_paise=50_000, date=date(2026, 1, 1)), runs=20)
    top_eight = {f"goal_{index:02d}" for index in range(1, 9)}
    assert seen
    assert all(set(goal_ids) <= top_eight for goal_ids in seen)
    assert max(len(goal_ids) for goal_ids in seen) <= MAX_DEFERRABLE_GOALS
    assert result["verdict"] == "yes-but"
    delayed = {trade["goal_id"] for trade in result["tradeoffs"] if "goal_id" in trade}
    assert delayed and delayed <= top_eight


def test_min_held_reads_the_threshold_as_written():
    assert min_hold_count(25, 0.28) == 7
    assert min_hold_count(16_384, 0.90) == 14_746
    assert min_hold_count(100, 0.99) == 99


def test_a_card_bill_is_an_obligation_the_verdict_cannot_ignore():
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=5,
        due_day=25,
        outstanding_paise=50_000,
        credit_limit_paise=500_000,
        unbilled_paise=0,
    )
    inputs = _inputs(start=10_000, floor=-100_000, cards=(card,))
    result = project(inputs, runs=50)
    assert result["floor_probability"] == 1.0
    assert result["obligations_met"] is False
    verdict = afford(inputs, Hypothetical(amount_paise=1_000, date=date(2026, 1, 1)), runs=50)
    assert verdict["verdict"] != "yes"


def test_a_cut_can_fix_an_obligation_that_the_floor_alone_would_pass():
    category = SpendCategory(
        id="cat_food",
        name="Food",
        baseline_per_day_paise=5_000,
        reducible=True,
        history=tuple((date(2026, 1, 1) - timedelta(days=index + 1), 5_000) for index in range(60)),
    )
    inputs = _inputs(
        start=60_000,
        floor=-500_000,
        categories=(category,),
        events=(
            CashEvent(
                date=date(2026, 1, 11),
                amount_paise=-15_000,
                name="Rent",
                kind="obligation",
            ),
        ),
    )
    purchase = Hypothetical(amount_paise=2_000, date=date(2026, 1, 2))
    applied = replace(
        inputs,
        events=inputs.events
        + (
            CashEvent(
                date=date(2026, 1, 2),
                amount_paise=-2_000,
                name="Hypothetical purchase",
                kind="hypothetical",
            ),
        ),
    )
    result = afford(inputs, purchase, runs=512, seed=7)
    assert result["floor_probability"] == 1.0
    assert result["obligations_met"] is False
    assert result["verdict"] == "yes-but"
    trade = next(row for row in result["tradeoffs"] if row["kind"] == "cut_category")
    assert 0 < trade["ratio"] <= 0.5
    reduced, _rows = reduce_categories(
        applied, trade["ratio"], until=date.fromisoformat(trade["until"])
    )
    assert project(reduced, runs=512, seed=7)["obligations_met"] is True


def test_a_future_card_purchase_uses_credit_freed_by_an_earlier_bill():
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=5,
        due_day=25,
        outstanding_paise=100_000,
        credit_limit_paise=150_000,
        unbilled_paise=0,
    )
    result = afford(
        _inputs(start=1_000_000, cards=(card,)),
        Hypothetical(
            amount_paise=100_000,
            date=date(2026, 2, 20),
            method="card",
            account="Card",
        ),
        runs=20,
    )
    assert result["hypothetical"]["feasible"] is True


def test_strict_dominance_keeps_card_and_emi_peers_with_equal_values():
    card = _Plan("card", (), 100, 500)
    emi = _Plan("emi", (), 100, 500)
    assert not _dominates(card, emi)
    assert not _dominates(emi, card)
    assert _dominates(_Plan("asked", (), 0, 0), card)
    assert _dominates(_Plan("card", (), 50, 500), card)


def test_a_tradeoff_lists_every_goal_it_delays():
    goals = (
        GoalPlan(id="goal_a", name="Goa", target_paise=100_000, saved_paise=0, priority=1),
        GoalPlan(id="goal_b", name="Car", target_paise=100_000, saved_paise=0, priority=2),
    )
    inputs = _inputs(
        start=10_000,
        floor=0,
        goals=goals,
        events=(
            CashEvent(
                date=date(2026, 1, 9),
                amount_paise=-4_000,
                name="Goa contribution",
                kind="goal_contribution",
                goal_id="goal_a",
            ),
            CashEvent(
                date=date(2026, 1, 8),
                amount_paise=-4_000,
                name="Car contribution",
                kind="goal_contribution",
                goal_id="goal_b",
            ),
            CashEvent(
                date=date(2026, 1, 11),
                amount_paise=-6_000,
                name="Rent",
                kind="obligation",
            ),
        ),
    )
    result = afford(
        inputs,
        Hypothetical(amount_paise=2_000, date=date(2026, 1, 2)),
        runs=50,
    )
    trade = next(row for row in result["tradeoffs"] if row["kind"] == "delay_goal")
    assert {goal["id"] for goal in trade["goals"]} == {"goal_a", "goal_b"}
    assert trade["goal_id"] == trade["goals"][0]["id"]


def test_a_skipped_goal_contribution_before_an_obligation_counts():
    goal = GoalPlan(id="goal", name="Goa", target_paise=100_000, saved_paise=0)
    inputs = _inputs(
        start=8_000,
        floor=-100_000,
        goals=(goal,),
        events=(
            CashEvent(
                date=date(2026, 1, 10),
                amount_paise=-4_000,
                name="Goa contribution",
                kind="goal_contribution",
                goal_id="goal",
            ),
            CashEvent(
                date=date(2026, 1, 11),
                amount_paise=-6_000,
                name="Rent",
                kind="obligation",
            ),
        ),
    )
    result = afford(
        inputs,
        Hypothetical(amount_paise=2_000, date=date(2026, 1, 2)),
        runs=512,
        seed=7,
    )
    assert result["obligations_met"] is False
    assert result["verdict"] == "yes-but"
    trade = next(row for row in result["tradeoffs"] if row["kind"] == "delay_goal")
    assert trade["goal_id"] == "goal"


def test_obligation_days_are_gated_independently_across_a_percent():
    scenarios = 512
    needed = min_hold_count(scenarios, 0.99)
    payables = np.zeros((scenarios, 2), np.int64)
    payables[:5, 0] = -10
    payables[5:10, 1] = -10
    reducible = np.zeros((scenarios, 2), np.int64)
    reducible[:10, :] = 100
    matrix = CriticalMatrix(
        method="asked",
        inputs=_inputs(),
        cost_paise=0,
        meta={},
        marks=(1, 2),
        balances=np.zeros((scenarios, 2), np.int64),
        reducible=None,
        capped=False,
        obligation_days=(1, 2),
        payables=payables,
        obligation_reducible=reducible,
    )
    # Five scenarios fail each day, disjoint; each day is inside its own 1% allowance, so no
    # cut is needed. A scenario-max gate would see ten failing scenarios and demand a cut.
    assert _least_cut(matrix, matrix.balances, payables, 0, needed, needed) == 0


def test_a_provisional_capped_candidate_does_not_prune_a_goal_superset(monkeypatch):
    goals = (
        GoalPlan(id="a", name="A", target_paise=100_000, saved_paise=0),
        GoalPlan(id="b", name="B", target_paise=100_000, saved_paise=0),
    )
    inputs = _inputs(
        goals=goals,
        events=(
            CashEvent(
                date=date(2026, 1, 9),
                amount_paise=-1_000,
                name="A contribution",
                kind="goal_contribution",
                goal_id="a",
            ),
            CashEvent(
                date=date(2026, 1, 9),
                amount_paise=-1_000,
                name="B contribution",
                kind="goal_contribution",
                goal_id="b",
            ),
        ),
    )
    matrix = CriticalMatrix(
        method="asked",
        inputs=inputs,
        cost_paise=0,
        meta={},
        marks=(0,),
        balances=np.zeros((4, 1), np.int64),
        reducible=np.zeros((4, 1), np.int64),
        capped=True,
        obligation_days=(),
        payables=None,
        obligation_reducible=None,
    )
    monkeypatch.setattr(affordability, "_least_cut", lambda *args, **kwargs: None)
    plans = affordability._plans(
        [matrix],
        ("a", "b"),
        date(2026, 1, 1),
        floor=0,
        needed=3,
        obligation_needed=4,
        spend_paise=0,
    )
    assert ("a", "b") in {plan.goals for plan in plans}
    assert all(plan.provisional for plan in plans)


def test_verify_recomputes_the_cost_at_the_bisected_cut(monkeypatch):
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=10_000,
        reducible=True,
        history=tuple(
            (date(2026, 1, 1) - timedelta(days=index + 1), 10_000) for index in range(60)
        ),
    )
    inputs = _inputs(start=1_000_000, categories=(category,))
    plan = _Plan("asked", (), 5_000, 999_999, provisional=True)
    matrix = CriticalMatrix(
        method="asked",
        inputs=inputs,
        cost_paise=0,
        meta={},
        marks=(0,),
        balances=np.zeros((4, 1), np.int64),
        reducible=np.ones((4, 1), np.int64),
        capped=True,
        obligation_days=(),
        payables=None,
        obligation_reducible=None,
    )

    def fake_evaluate_cut(self, cut_bps):
        return {"floor_probability": 1.0 if cut_bps >= 2_500 else 0.0, "obligations_met": True}

    monkeypatch.setattr(criticals.PreparedPlan, "evaluate_cut", fake_evaluate_cut)
    verified = affordability._verify(
        plan,
        matrix,
        when=date(2026, 1, 1),
        until=date(2026, 2, 1),
        runs=4,
        seed=1,
        threshold=0.9,
        spend_paise=10_000,
    )
    assert verified is not None
    assert verified.plan.cut_bps == 2_500
    assert verified.plan.cost_paise == 2_500
    assert verified.plan.provisional is False


def test_criticals_reused_spec_matches_full_project_bit_for_bit():
    """One prepared spec, varied only by cut, must reproduce a full project() rerun exactly.

    The inputs put far more card spend on the card than its limit (so a cut also changes capped
    cash) and defer a goal contribution, covering both levers at cut 0 and above.
    """
    today = date(2026, 1, 1)
    payday = today + timedelta(days=5)
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=10_000,
        baseline_source="computed",
        reducible=True,
        history=tuple(
            (today - timedelta(days=offset), 8_000 + offset * 100) for offset in range(60, 0, -1)
        ),
        card_id="card",
        card_share=1,
    )
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=2,
        due_day=4,
        outstanding_paise=0,
        credit_limit_paise=50_000,
        unbilled_paise=0,
    )
    goal = GoalPlan(id="goal", name="Goa", target_paise=100_000, saved_paise=0)
    applied = _inputs(
        start=200_000,
        categories=(category,),
        cards=(card,),
        goals=(goal,),
        events=(
            CashEvent(
                date=today + timedelta(days=2),
                amount_paise=-100_000,
                name="Card bill",
                kind="obligation",
                card_id="card",
            ),
            CashEvent(
                date=today + timedelta(days=2),
                amount_paise=-5_000,
                name="Goa contribution",
                kind="goal_contribution",
                goal_id="goal",
            ),
            CashEvent(date=payday, amount_paise=500_000, name="Salary", kind="income_fixed"),
        ),
    )
    assert criticals.build_critical_matrix(
        "asked",
        applied,
        cost_paise=0,
        meta={},
        cut_until=(payday - today).days,
        runs=256,
        seed=7,
    ).capped

    base, removed = affordability._goal_relief(applied, ("goal",), today)
    assert removed
    prepared = criticals.prepare_plan(base, until=payday, runs=256, seed=7, threshold=0.9)

    for cut in (0, 1, 2_500, 10_000):
        reused = prepared.evaluate_cut(cut)
        full_inputs = base if cut == 0 else reduce_categories(base, cut / 10_000, until=payday)[0]
        full = project(full_inputs, runs=256, seed=7, threshold=0.9)
        assert reused == full
