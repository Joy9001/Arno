"""Check the affordability solver against exhaustive common-random-numbers walks."""

from dataclasses import replace
from datetime import date, timedelta

from arno.engine import affordability, criticals
from arno.engine.affordability import Hypothetical, afford
from arno.engine.projection import (
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    SpendCategory,
    project,
    reduce_categories,
)


def test_affordability_matches_exact_enumeration_on_512_shared_paths(monkeypatch):
    # Keep the exhaustive cut domain small while retaining one-basis-point resolution.
    cap = 20
    monkeypatch.setattr(affordability, "MAX_CUT_BPS", cap)
    runs, seed = 512, 7
    today = date(2026, 1, 1)
    tomorrow = today + timedelta(days=1)
    payday = today + timedelta(days=3)
    inputs = ProjectionInput(
        as_of=today,
        floor_paise=-60_000,
        liquid=(LiquidAccount(id="cash", name="Cash", balance_paise=100_000),),
        goals=(GoalPlan(id="goal", name="Reserve", target_paise=100_000, saved_paise=0),),
        events=(
            CashEvent(tomorrow, -45, "Reserve contribution", "goal_contribution", goal_id="goal"),
            CashEvent(today + timedelta(days=2), -40_045, "Rent", "obligation"),
            CashEvent(payday, 20_000_000, "Salary", "income_fixed"),
        ),
        categories=(
            SpendCategory(
                id="food",
                name="Food",
                baseline_per_day_paise=45_000,
                baseline_source="computed",
                reducible=True,
                history=tuple(
                    (today - timedelta(days=offset), 40_000 + (offset % 2) * 10_000)
                    for offset in range(30, 0, -1)
                ),
            ),
        ),
    )
    purchase = Hypothetical(amount_paise=10_000, date=today)
    bought = CashEvent(today, -10_000, "Purchase", "hypothetical", at_start=True)
    expected: set[tuple[tuple[str, ...], int]] = set()
    for skipped in ((), ("goal",)):
        events = tuple(event for event in inputs.events if event.goal_id not in skipped)
        applied = replace(inputs, events=(*events, bought))
        for bps in range(cap + 1):
            reduced, _ = reduce_categories(applied, bps / 10_000, until=payday)
            full = project(reduced, runs=runs, seed=seed)
            if full["floor_probability"] >= 0.9 and full["obligations_met"]:
                expected.add((skipped, bps))
                break

    assert expected == {((), 18), (("goal",), 9)}
    result = afford(inputs, purchase, runs=runs, seed=seed)
    assert result["verdict"] == "yes-but"
    actual = {
        (tuple(goal["id"] for goal in trade.get("goals", [])), round(trade["ratio"] * 10_000))
        for trade in result["tradeoffs"]
        if "ratio" in trade
    }
    assert actual == expected


def test_a_capped_card_falls_back_to_the_max_cut_and_prices_the_bisected_cut(monkeypatch):
    monkeypatch.setattr(affordability, "MAX_CUT_BPS", 20)
    runs, seed = 512, 7
    today = date(2026, 1, 1)
    payday = today + timedelta(days=3)
    inputs = ProjectionInput(
        as_of=today,
        floor_paise=-60_000,
        liquid=(LiquidAccount(id="cash", name="Cash", balance_paise=49_955),),
        cards=(CardAccount("card", "Card", 25, 27, 0, 100_000),),
        events=(
            CashEvent(today + timedelta(days=2), -100_000, "Bill", "obligation", card_id="card"),
            CashEvent(payday, 20_000_000, "Salary", "income_fixed"),
        ),
        categories=(
            SpendCategory(
                id="food",
                name="Food",
                baseline_per_day_paise=50_000,
                baseline_source="computed",
                reducible=True,
                history=tuple((today - timedelta(days=i), 50_000) for i in range(30, 0, -1)),
                card_id="card",
                card_share=1,
            ),
        ),
    )
    matrix = criticals.build_critical_matrix(
        "asked",
        inputs,
        cost_paise=0,
        meta={},
        cut_until=3,
        runs=runs,
        seed=seed,
    )
    assert matrix.capped
    verified = affordability._verify(
        affordability._Plan("asked", (), 1, 10),
        matrix,
        when=today,
        until=payday,
        runs=runs,
        seed=seed,
        threshold=0.9,
        spend_paise=100_000,
    )
    assert verified is not None
    assert verified.plan.cut_bps == 9
    assert verified.plan.cost_paise == 90
