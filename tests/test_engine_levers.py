"""The affordability plans held to exact arithmetic: the least cut, the combined plan, minimality,
and a card cut that frees nothing before its bill.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from arno.engine.affordability import Hypothetical, afford
from arno.engine.projection import (
    DEFAULT_SEED,
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    SpendCategory,
    card_due_date,
    project,
    reduce_categories,
)

RUNS = 200


def _category(name: str, per_day: int, *, card: str | None = None) -> SpendCategory:
    """A reducible category with 90 constant days, so its simulated spend is deterministic."""
    return SpendCategory(
        id=f"cat_{name.lower()}",
        name=name,
        baseline_per_day_paise=per_day,
        reducible=True,
        card_share=1.0 if card is not None else 0.0,
        card_id=card,
        history=tuple((date(2025, 10, 1) + timedelta(days=index), per_day) for index in range(90)),
    )


def _with_purchase(inputs: ProjectionInput, amount_paise: int) -> ProjectionInput:
    """The inputs with the purchase applied at the as-of date, as `afford` applies it."""
    return replace(
        inputs,
        events=inputs.events
        + (
            CashEvent(
                date=inputs.as_of,
                amount_paise=-amount_paise,
                name="Hypothetical purchase",
                kind="hypothetical",
                at_start=True,
            ),
        ),
    )


def test_the_least_cut_is_exact():
    # Start 4,000,000; the 1,000,000 purchase on day 0 leaves 3,000,000. Food is 20,000 a
    # day, so day 30 (the checkpoint before the Feb 1 salary) holds
    # 3,000,000 - 30 * 20,000 = 2,400,000, short 200,000 of the 2,600,000 floor. Days 1..30
    # are reducible: 600,000, so the least cut is ceil(10,000 * 200,000 / 600,000) = 3,334
    # basis points. The cumulative rounding frees 200,040 there; two points less frees
    # 199,920 and is short by 80, so the cut is exact, not merely sufficient.
    inputs = ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=2_600_000,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=4_000_000),),
        categories=(_category("Food", 20_000),),
        events=(
            CashEvent(
                date=date(2026, 2, 1),
                amount_paise=2_000_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
        ask_horizon_days=90,
    )
    purchase = Hypothetical(amount_paise=1_000_000, date=date(2026, 1, 1))
    with_purchase = _with_purchase(inputs, 1_000_000)
    assert project(with_purchase, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] < 0.9

    result = afford(inputs, purchase, runs=RUNS, seed=DEFAULT_SEED)
    tradeoff = next(trade for trade in result["tradeoffs"] if trade["kind"] == "cut_category")
    assert tradeoff["ratio"] == 0.3334
    assert tradeoff["until"] == "2026-02-01"
    assert tradeoff["floor_probability"] >= 0.9

    until = date.fromisoformat(tradeoff["until"])
    kept, _rows = reduce_categories(with_purchase, tradeoff["ratio"], until=until)
    assert project(kept, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] >= 0.9
    short, _rows = reduce_categories(with_purchase, tradeoff["ratio"] - 0.0002, until=until)
    assert project(short, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] < 0.9


def test_a_combined_plan_appears_when_neither_lever_alone_suffices():
    # Start 3,080,000; the 600,000 purchase on day 0 and the 300,000 goal contribution on
    # day 9 leave day 44 (before the Feb 15 salary) at
    # 3,080,000 - 600,000 - 44 * 20,000 - 300,000 = 1,300,000, short 700,000 of the
    # 2,000,000 floor. Days 1..44 are reducible: 880,000, so a cut alone needs 7,955 basis
    # points, over the 5,000 cap. Skipping the goal leaves 1,600,000, still 400,000 short.
    # Both together: 300,000 + 4,546 basis points of 880,000 = 300,000 + 400,048 clears it.
    inputs = ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=2_000_000,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=3_080_000),),
        categories=(_category("Food", 20_000),),
        goals=(GoalPlan(id="goal", name="Goa", target_paise=300_000, saved_paise=0),),
        events=(
            CashEvent(
                date=date(2026, 1, 10),
                amount_paise=-300_000,
                name="Goa contribution",
                kind="goal_contribution",
                goal_id="goal",
            ),
            CashEvent(
                date=date(2026, 2, 15),
                amount_paise=2_000_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
        ask_horizon_days=90,
    )
    purchase = Hypothetical(amount_paise=600_000, date=date(2026, 1, 1))
    with_purchase = _with_purchase(inputs, 600_000)
    payday = date(2026, 2, 15)
    assert project(with_purchase, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] < 0.9

    skipped = replace(
        with_purchase,
        events=tuple(event for event in with_purchase.events if event.kind != "goal_contribution"),
    )
    assert project(skipped, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] < 0.9
    cut_only, _rows = reduce_categories(with_purchase, 0.5, until=payday)
    assert project(cut_only, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] < 0.9
    both, _rows = reduce_categories(skipped, 0.4546, until=payday)
    assert project(both, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] >= 0.9

    result = afford(inputs, purchase, runs=RUNS, seed=DEFAULT_SEED)
    assert result["verdict"] == "yes-but"
    tradeoff = next(trade for trade in result["tradeoffs"] if trade["kind"] == "combined")
    assert set(tradeoff["levers"]) == {"delay_goal", "cut_category"}
    assert tradeoff["goal_id"] == "goal"
    assert tradeoff["ratio"] == 0.4546
    assert tradeoff["until"] == "2026-02-15"
    assert tradeoff["floor_probability"] >= 0.9


def test_a_passing_goal_skip_is_never_paired_with_a_cut():
    # Start 2,000,000; the 500,000 purchase, 10,000 a day of food and the 200,000 goal
    # contribution on day 9 leave day 44 at
    # 2,000,000 - 500,000 - 44 * 10,000 - 200,000 = 860,000, short 140,000 of the 1,000,000
    # floor. Skipping the contribution alone leaves 1,060,000, so the goal skip passes as it
    # is and no reported plan may pair it with a spending cut.
    inputs = ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=1_000_000,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=2_000_000),),
        categories=(_category("Food", 10_000),),
        goals=(GoalPlan(id="goal", name="Goa", target_paise=200_000, saved_paise=0),),
        events=(
            CashEvent(
                date=date(2026, 1, 10),
                amount_paise=-200_000,
                name="Goa contribution",
                kind="goal_contribution",
                goal_id="goal",
            ),
            CashEvent(
                date=date(2026, 2, 15),
                amount_paise=1_000_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
        ask_horizon_days=90,
    )
    with_purchase = _with_purchase(inputs, 500_000)
    skipped = replace(
        with_purchase,
        events=tuple(event for event in with_purchase.events if event.kind != "goal_contribution"),
    )
    assert project(with_purchase, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] < 0.9
    assert project(skipped, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] >= 0.9

    result = afford(
        inputs,
        Hypothetical(amount_paise=500_000, date=date(2026, 1, 1)),
        runs=RUNS,
        seed=DEFAULT_SEED,
    )
    assert any(trade["kind"] == "delay_goal" for trade in result["tradeoffs"])
    # `ratio` appears only on a cut tradeoff, `goal_id` only on one that skips a goal.
    assert not any("goal_id" in trade and "ratio" in trade for trade in result["tradeoffs"])
    assert all(trade["kind"] != "combined" for trade in result["tradeoffs"])


def test_a_card_cut_frees_nothing_before_the_bill():
    # The card closes on the 5th and is due on the 25th, so a charge on Jan 1 joins the bill
    # due Jan 25 (day 24, from card_due_date). The 500,000 purchase leaves 500,000, short
    # 100,000 of the 600,000 floor from day 0 through the day-18 checkpoint (Jan 19). Every
    # rupee of food is on the card, and the cut window ends at the Jan 20 salary, so a cut
    # frees nothing by day 18: card spend there joins bills due day 24 and day 55. The
    # salary lifts the balance to 2,000,000 and the bills (40,000, 310,000, 280,000) still
    # leave 1,370,000 on day 90, so no cut up to 50% can help the short check point.
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=10_000_000,
    )
    inputs = ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=600_000,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=1_000_000),),
        cards=(card,),
        categories=(_category("Food", 10_000, card="card"),),
        events=(
            CashEvent(
                date=date(2026, 1, 20),
                amount_paise=1_500_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
        ask_horizon_days=90,
    )
    due = card_due_date(card, date(2026, 1, 1))
    assert due == date(2026, 1, 25)
    assert date(2026, 1, 1) + timedelta(days=18) < due

    with_purchase = _with_purchase(inputs, 500_000)
    assert project(with_purchase, runs=RUNS, seed=DEFAULT_SEED)["floor_probability"] < 0.9
    result = afford(
        inputs,
        Hypothetical(amount_paise=500_000, date=date(2026, 1, 1)),
        runs=RUNS,
        seed=DEFAULT_SEED,
    )
    # A cut tradeoff always carries a ratio; none may be reported here, alone or combined.
    assert all("ratio" not in trade for trade in result["tradeoffs"])
    assert all(trade["kind"] != "cut_category" for trade in result["tradeoffs"])
    assert all("cut_category" not in (trade.get("levers") or []) for trade in result["tradeoffs"])


def test_a_goal_that_completes_late_does_not_stop_a_yes():
    # The goal's two 60,000 contributions reach its 120,000 target on Feb 5, 16 days after
    # its Jan 20 deadline, and the 50,000 purchase leaves 950,000 against a zero floor, so
    # the floor and every obligation pass. Goal timing is no part of the yes rule, and a
    # slip is reported only by a delay-a-goal plan, so `goal_slips` stays empty.
    inputs = ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=1_000_000),),
        goals=(
            GoalPlan(
                id="goal",
                name="Goa",
                target_paise=120_000,
                saved_paise=0,
                deadline=date(2026, 1, 20),
            ),
        ),
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
        ask_horizon_days=90,
    )
    result = afford(
        inputs,
        Hypothetical(amount_paise=50_000, date=date(2026, 1, 1)),
        runs=RUNS,
        seed=DEFAULT_SEED,
    )
    assert result["goals"][0]["completes_on"] == "2026-02-05"
    assert result["goals"][0]["slips_days"] == 16
    assert result["verdict"] == "yes"
    assert result["goal_slips"] == []
    assert result["tradeoffs"] == []
