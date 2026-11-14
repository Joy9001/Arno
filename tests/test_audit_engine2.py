"""Regressions from the second engine pass: spend history, job anchors, cuts, backtests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from arno.engine.backtest import Score, format_report, summarize
from arno.engine.projection import (
    CurvePoint,
    LiquidAccount,
    Projection,
    ProjectionInput,
    SpendCategory,
    project,
    reduce_categories,
)
from arno.india.recurrence import expand_rule, first_occurrence
from arno.jobs.risk import risk_warning
from arno.jobs.schedules import enqueue_due_schedules
from arno.ledger.accounts import create_account
from arno.ledger.common import today as ledger_today
from arno.ledger.goals import create_goal
from arno.ledger.movements import correct_movement, record_expense
from arno.ledger.schedules import create_schedule
from arno.scheduled_questions import create_question
from arno.tools.projection import load_inputs


def _curve(result: Projection) -> dict[str, CurvePoint]:
    return {point["date"]: point for point in result["curve"]}


# --- spend history: a corrected row is counted once -----------------------------------------


async def test_a_corrected_bill_is_counted_once_in_spend_history(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    category = (await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Shopping'"))["id"]
    bill = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=250_000,
        category_id=category,
        description="Sofa",
        date=ledger_today() - timedelta(days=40),
    )
    await correct_movement(ledger, bill["id"], changes={"amount_paise": 225_000})
    # Enough real days that the category is simulated from history, not from an estimate.
    for index in range(14):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=1_000,
            category_id=category,
            description="Stationery",
            date=ledger_today() - timedelta(days=index + 1),
        )

    inputs = await load_inputs(ledger, as_of=ledger_today())
    shopping = next(item for item in inputs.categories if item.id == category)
    # The original 250,000 is reversal-cancelled and its -250,000 reversal is not a charge:
    # 225,000 once, plus 14,000 of stationery, never 489,000.
    assert sum(amount for _day, amount in shopping.history) == 239_000


# --- job schedules: a finite rule exhausts -------------------------------------------------


async def test_a_finite_question_rule_runs_its_count_then_stops(db):
    await create_question(db, prompt="Did my salary land?", rule="FREQ=DAILY;COUNT=3")
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'scheduled_question'")
    first = schedule["next_run_at"]
    assert schedule["dtstart"] == first

    for step in range(3):
        moment = first + timedelta(days=step)
        assert await enqueue_due_schedules(db, now=moment) == 1
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'scheduled_question'")
    assert schedule["active"] is False
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind = 'scheduled_question'") == 3

    # The consumed rule cannot fire again on a later run.
    assert await enqueue_due_schedules(db, now=first + timedelta(days=10)) == 0


async def test_a_late_finite_question_still_counts_from_its_stored_anchor(db):
    await create_question(db, prompt="Any bills due?", rule="FREQ=DAILY;COUNT=2")
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'scheduled_question'")
    first = schedule["next_run_at"]

    # One run, then a catch-up five days later: the second occurrence is still the last one.
    assert await enqueue_due_schedules(db, now=first) == 1
    assert await enqueue_due_schedules(db, now=first + timedelta(days=5)) == 1
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'scheduled_question'")
    assert schedule["active"] is False
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind = 'scheduled_question'") == 2


# --- cut-category tradeoff: the cut stops at the payday ------------------------------------


def _flat_category_inputs() -> ProjectionInput:
    return ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=1_000_000),),
        categories=(
            SpendCategory(
                id="cat_food", name="Food", baseline_per_day_paise=10_000, reducible=True
            ),
        ),
        ask_horizon_days=20,
    )


def test_a_category_cut_applies_only_until_its_date():
    inputs = _flat_category_inputs()
    reduced, rows = reduce_categories(inputs, 0.5, until=date(2026, 1, 11))
    curve = _curve(project(reduced, runs=3))

    assert curve["2026-01-06"]["median_paise"] == 1_000_000 - 5 * 5_000
    assert curve["2026-01-11"]["median_paise"] == 1_000_000 - 10 * 5_000
    assert curve["2026-01-12"]["median_paise"] == 1_000_000 - 10 * 5_000 - 10_000
    assert rows[0]["new_baseline_per_day_paise"] == 5_000
    assert rows[0]["until"] == "2026-01-11"

    # Without a date the cut runs to the end of the horizon, which is what the old code did.
    permanent, _rows = reduce_categories(inputs, 0.5)
    curve = _curve(project(permanent, runs=3))
    assert curve["2026-01-12"]["median_paise"] == 1_000_000 - 11 * 5_000


def test_the_cut_tradeoff_ends_at_the_next_payday():
    from dataclasses import replace

    from arno.engine.affordability import Hypothetical, _cut_category_tradeoff
    from arno.engine.projection import CashEvent

    category = SpendCategory(
        id="cat_food",
        name="Food",
        baseline_per_day_paise=30_000,
        reducible=True,
        history=tuple((date(2025, 10, 1) + timedelta(days=index), 30_000) for index in range(90)),
    )
    inputs = ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=3_000_000,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=5_000_000),),
        categories=(category,),
        events=(
            CashEvent(
                date=date(2026, 1, 31),
                amount_paise=3_000_000,
                name="Salary",
                kind="income_fixed",
            ),
        ),
        ask_horizon_days=90,
    )
    purchase = Hypothetical(amount_paise=1_550_000, date=date(2026, 1, 1))
    with_purchase = project(
        replace(
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
        ),
        runs=100,
    )
    tradeoff = _cut_category_tradeoff(
        inputs, purchase, runs=100, seed=20261103, threshold=0.90, with_purchase=with_purchase
    )
    assert tradeoff is not None
    assert tradeoff["until"] == "2026-01-31"
    assert tradeoff["ratio"] <= 0.5
    assert tradeoff["categories"][0]["until"] == "2026-01-31"
    assert "then back to normal spending" in tradeoff["detail"]


# --- backtest: rows created after the as-of date are excluded -------------------------------


async def test_a_projection_as_of_a_past_day_ignores_rows_created_later(ledger):
    today = ledger_today()
    as_of = today - timedelta(days=60)
    # The account's own ledger starts after `as_of`, so nothing of it existed then. (A backfilled
    # entry dated on or before that day does count; see tests/test_audit_anchor.py.)
    bank = await create_account(
        ledger,
        kind="bank",
        name="SBI",
        opening_balance_paise=1_000_000,
        opening_balance_as_of=as_of + timedelta(days=5),
    )
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Rent",
        amount_paise=250_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=1",
        starts=as_of - timedelta(days=60),
    )
    goal = await create_goal(
        ledger, name="Goa", target_paise=6_000_000, deadline=as_of + timedelta(days=200)
    )

    past = await load_inputs(ledger, as_of=as_of, known_then=True)
    assert [account.id for account in past.liquid] == []
    assert [event for event in past.events if event.source_id == schedule["id"]] == []
    assert past.goals == ()

    # The same request without the backtest's "only what existed then" reads today's rows.
    today_inputs = await load_inputs(ledger, as_of=as_of)
    assert [account.id for account in today_inputs.liquid] == [bank["id"]]

    now = await load_inputs(ledger, as_of=today)
    assert [account.id for account in now.liquid] == [bank["id"]]
    assert any(event.source_id == schedule["id"] for event in now.events)
    assert [row.id for row in now.goals] == [goal["id"]]


def test_a_real_backfill_report_states_what_it_could_not_know():
    score = Score(
        group="ledger",
        as_of=date(2026, 1, 1),
        forecaster="flat",
        target="end",
        horizon=7,
        crps=0.0,
        pinball=(0.0, 0.0, 0.0),
        covered=True,
        width=0,
        pit=0.5,
        breach_probability=None,
        breached=None,
    )
    real = summarize([score], source="real ledger", dropped=0, names=["flat"])
    report = format_report(real)
    assert "created after an origin are excluded" in report
    assert "read in its current form" in report
    synthetic = summarize([score], source="synthetic personas", dropped=0, names=["flat"])
    assert "created after an origin" not in format_report(synthetic)


# --- recurrence: a month-end day never skips a month ----------------------------------------


@pytest.mark.parametrize("day", [29, 30, 31])
def test_a_monthly_obligation_on_a_month_end_day_never_skips(day):
    dates = expand_rule(
        f"FREQ=MONTHLY;BYMONTHDAY={day}",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
    )
    assert [moment.month for moment in dates] == list(range(1, 13))
    assert dates[1] == date(2026, 2, 28)
    assert dates[3] == date(2026, 4, min(day, 30))
    assert len(set(dates)) == 12


@pytest.mark.parametrize("day", [29, 30, 31])
def test_a_month_end_days_february_clamps_in_a_leap_year(day):
    dates = expand_rule(
        f"FREQ=MONTHLY;BYMONTHDAY={day}",
        date(2024, 1, 1),
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
    )
    assert dates == [date(2024, 1, min(day, 31)), date(2024, 2, 29), date(2024, 3, day)]


def test_a_month_end_rule_has_a_first_occurrence_in_a_short_month():
    assert first_occurrence("FREQ=MONTHLY;BYMONTHDAY=31", date(2026, 2, 1)) == date(2026, 2, 28)
    assert first_occurrence("FREQ=MONTHLY;BYMONTHDAY=30", date(2024, 2, 1)) == date(2024, 2, 29)


async def test_a_rent_schedule_on_the_31st_is_due_in_february(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Rent",
        amount_paise=2_500_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=31",
        starts=date(2027, 1, 31),
    )
    dates = expand_rule(
        schedule["rule"],
        date(2027, 1, 31),
        start=date(2027, 1, 1),
        end=date(2027, 4, 30),
    )
    assert dates == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]


def test_a_last_day_rule_is_left_as_it_is():
    dates = expand_rule(
        "FREQ=MONTHLY;BYMONTHDAY=-1",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
    )
    assert dates == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    # A weekday rule with its own BYSETPOS keeps its meaning.
    assert expand_rule(
        "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
    ) == [date(2026, 1, 30)]


# --- risk: no floor means the check is against zero ----------------------------------------


def _fake_projection(**overrides) -> dict:
    state = {
        "floor_paise": 0,
        "floor_probability": 0.55,
        "floor_threshold": 0.9,
        "first_floor_crossing": None,
        "median_low": {"date": "2026-11-20", "paise": -10_000},
        "data_quality": {"gaps": []},
        "source_ids": {},
    }
    return state | overrides


async def test_risk_warns_against_zero_when_no_floor_is_set(db, monkeypatch):
    async def fake_project(conn, **kwargs):
        del conn, kwargs
        return _fake_projection(first_floor_crossing="2026-11-20")

    monkeypatch.setattr("arno.tools.projection.project", fake_project)
    await db.execute("UPDATE risk_state SET first_floor_crossing = '2026-12-01' WHERE id = 1")
    row = await risk_warning(db)
    assert row is not None
    body = row["body"]
    assert "staying above zero" in body
    assert "below the 90% line" in body
    assert "goes below zero on 2026-11-20" in body
    assert "your floor" not in body
    assert "No floor is set" in body


async def test_risk_keeps_its_floor_wording_when_a_floor_is_set(db, monkeypatch):
    async def fake_project(conn, **kwargs):
        del conn, kwargs
        return _fake_projection(floor_paise=100_000, first_floor_crossing="2026-11-20")

    monkeypatch.setattr("arno.tools.projection.project", fake_project)
    await db.execute("UPDATE risk_state SET first_floor_crossing = '2026-12-01' WHERE id = 1")
    row = await risk_warning(db)
    assert row is not None
    body = row["body"]
    assert "staying above your floor" in body
    assert "goes below your floor on 2026-11-20" in body
    assert "zero" not in body


async def test_risk_is_quiet_against_zero_when_the_walks_hold(db, monkeypatch):
    async def fake_project(conn, **kwargs):
        del conn, kwargs
        return _fake_projection(floor_probability=0.95)

    monkeypatch.setattr("arno.tools.projection.project", fake_project)
    assert await risk_warning(db) is None
