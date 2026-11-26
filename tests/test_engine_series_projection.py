"""Tests for detected recurring payment series, which enter the projection as dated outflows."""

from __future__ import annotations

from datetime import date, timedelta

from arno.engine.affordability import Hypothetical, afford
from arno.engine.projection import (
    CardAccount,
    CashEvent,
    LiquidAccount,
    ProjectionInput,
    SpendCategory,
    project,
)

AS_OF = date(2026, 1, 1)


def _series(
    series_id: str,
    name: str,
    amount: int,
    *dates: date,
    card_id: str | None = None,
) -> tuple[CashEvent, ...]:
    return tuple(
        CashEvent(
            date=day,
            amount_paise=-amount,
            name=name,
            kind="series",
            source_id=series_id,
            card_id=card_id,
        )
        for day in dates
    )


def _inputs(
    *events: CashEvent,
    start: int = 1_000_000,
    cards: tuple[CardAccount, ...] = (),
    categories: tuple[SpendCategory, ...] = (),
    horizon: int = 90,
) -> ProjectionInput:
    return ProjectionInput(
        as_of=AS_OF,
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=start, as_of=AS_OF),),
        events=events,
        cards=cards,
        categories=categories,
        ask_horizon_days=horizon,
    )


def test_fixed_monthly_series_lowers_median_end_balance_and_labels_quality():
    netflix = _series(
        "ser_netflix",
        "Netflix",
        64_900,
        date(2026, 1, 15),
        date(2026, 2, 15),
        date(2026, 3, 15),
        date(2026, 4, 15),  # past the 90-day horizon (ends 2026-04-01): never lands
    )
    base = project(_inputs(start=1_000_000), runs=200, seed=1)
    with_series = project(_inputs(*netflix, start=1_000_000), runs=200, seed=1)
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
    # A detected series is not a confirmed schedule.
    assert "ser_netflix" not in with_series["source_ids"]["schedules"]


def test_multiple_series_label_plural_gap_and_sources():
    events = (
        *_series("s1", "Netflix", 64_900, date(2026, 1, 15)),
        *_series("s2", "Airtel", 99_900, date(2026, 1, 20)),
        *_series("s3", "Gym", 150_000, date(2026, 1, 25)),
    )
    result = project(_inputs(*events, start=1_000_000), runs=100, seed=1)
    assert (
        "3 detected recurring payments you have not confirmed are counted as outflows: "
        "Netflix, Airtel, Gym"
    ) in result["data_quality"]["gaps"]
    for series_id, name in (("s1", "Netflix"), ("s2", "Airtel"), ("s3", "Gym")):
        assert {
            "kind": "series",
            "id": series_id,
            "name": name,
            "detail": "detected, not confirmed",
        } in result["sources"]


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
    phone = _series("s1", "Phone", 50_000, date(2026, 1, 11), card_id="c1")
    result = project(_inputs(*phone, start=500_000, cards=(card,)), runs=64, seed=1)
    by_date = {point["date"]: point["median_paise"] for point in result["curve"]}
    # Headroom is 30,000, so 20,000 overflows immediately to cash on Jan 11.
    assert by_date["2026-01-10"] == 500_000
    assert by_date["2026-01-11"] == 480_000
    assert by_date["2026-01-24"] == 480_000
    # Unbilled 70,000 + charged 30,000 = 100,000 bill paid on Jan 25.
    assert by_date["2026-01-25"] == 380_000
    assert result["data_quality"]["simulation"]["credit_limit_exceeded"] is True

    # Unknown card_id falls back to cash immediately.
    other = _series("s2", "Other", 10_000, date(2026, 1, 11), card_id="missing_card")
    cash_result = project(_inputs(*other, start=500_000, cards=(card,)), runs=64, seed=1)
    cash_by_date = {point["date"]: point["median_paise"] for point in cash_result["curve"]}
    assert cash_by_date["2026-01-11"] == 490_000


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
    gym = _series("s1", "Gym", 60_000, date(2026, 1, 21))
    inputs = ProjectionInput(
        as_of=AS_OF,
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=150_000),),
        events=gym,
        categories=(dummy,),
        ask_horizon_days=90,
    )
    result = afford(inputs, Hypothetical(amount_paise=100_000, date=AS_OF), runs=200, seed=1)
    assert result["verdict"] == "no"
    assert all("ratio" not in trade for trade in result["tradeoffs"])


def test_only_series_that_pay_inside_the_horizon_are_gaps_and_citations():
    # A series dated today is already in the balance: nothing pays inside the horizon.
    gone = _series("s_dead", "Gone", 1_000, AS_OF)
    result = project(_inputs(*gone, start=500_000), runs=32, seed=1)
    assert not any("detected recurring payment" in gap for gap in result["data_quality"]["gaps"])
    assert all(source["id"] != "s_dead" for source in result["sources"])

    # Two payments of one series cite one source, not one per payment.
    live = _series("s_live", "Live", 1_000, date(2026, 1, 15), date(2026, 2, 15))
    repeated = project(_inputs(*live, start=500_000), runs=32, seed=1)
    assert [source["id"] for source in repeated["sources"]].count("s_live") == 1
