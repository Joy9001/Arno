"""Regression tests from the stable-release audit of the projection and money engine."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fin_agent.engine.affordability import Hypothetical, _delay_goal_tradeoff, afford
from fin_agent.engine.money import amortize
from fin_agent.engine.patterns import _load_charges, detect_missed
from fin_agent.engine.projection import (
    DEFAULT_SEED,
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    card_due_date,
    project,
)
from fin_agent.india.recurrence import expand_rule
from fin_agent.jobs.patterns import _suggested_rule
from fin_agent.jobs.reminders import send_reminders
from fin_agent.jobs.risk import risk_warning
from fin_agent.jobs.schedules import INDIA, enqueue_due_schedules, period_for
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.common import today as ledger_today
from fin_agent.ledger.movements import (
    correct_movement,
    record_expense,
    record_lending,
    record_refund,
    record_settlement,
    record_split,
)
from fin_agent.ledger.payees import create_payee
from fin_agent.ledger.schedule_payments import link_payment
from fin_agent.ledger.schedules import create_schedule
from fin_agent.tools.projection import card as projection_card
from fin_agent.tools.projection import load_inputs
from fin_agent.tools.projection import summary as projection_summary

UTC = ZoneInfo("UTC")


def _inputs(
    *,
    start: int = 1_000_000,
    floor: int = 0,
    events: tuple[CashEvent, ...] = (),
    cards: tuple[CardAccount, ...] = (),
    goals: tuple[GoalPlan, ...] = (),
    as_of: date = date(2026, 1, 1),
) -> ProjectionInput:
    return ProjectionInput(
        as_of=as_of,
        floor_paise=floor,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=start),),
        events=events,
        cards=cards,
        goals=goals,
        ask_horizon_days=90,
    )


def _curve(result: dict) -> dict[str, dict]:
    return {point["date"]: point for point in result["curve"]}


# --- projection: hypotheticals -------------------------------------------------------------


def test_a_future_hypothetical_purchase_is_applied_on_its_date():
    result = afford(
        _inputs(start=100_000, floor=50_000),
        Hypothetical(amount_paise=90_000, date=date(2026, 3, 1)),
        runs=20,
    )
    assert result["median_low"]["paise"] == 10_000
    assert result["floor_probability"] == 0.0
    assert result["verdict"] != "yes"


def test_a_future_card_purchase_is_paid_on_its_bill_date():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    result = afford(
        _inputs(start=100_000, floor=50_000, cards=(card,)),
        Hypothetical(amount_paise=90_000, date=date(2026, 3, 1), method="card"),
        runs=20,
    )
    assert result["median_low"]["paise"] == 10_000
    assert result["floor_probability"] == 0.0


def test_a_future_emi_is_charged_over_its_installment_dates():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    result = afford(
        _inputs(start=100_000, floor=50_000, cards=(card,)),
        Hypothetical(amount_paise=120_000, date=date(2026, 3, 1), method="emi", emi_months=3),
        runs=20,
    )
    assert result["median_low"]["paise"] < 50_000
    assert result["floor_probability"] < 1.0


def test_an_over_limit_card_purchase_is_a_no_with_no_cash_tradeoff():
    card = CardAccount(
        id="card",
        name="Small card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=50_000,
    )
    result = afford(
        _inputs(start=1_000_000, cards=(card,)),
        Hypothetical(amount_paise=120_000, date=date(2026, 6, 1), method="card"),
        runs=10,
    )
    assert result["hypothetical"]["feasible"] is False
    # Cash would cover it on the asked date, so there is no wait to offer, and paying later is
    # not a same-date tradeoff: the verdict stays `no` (e4 F11/F12).
    assert result["verdict"] == "no"
    assert result["tradeoffs"] == []
    assert "available credit" in result["verdict_reason"]


def test_two_what_ifs_share_the_same_baseline_and_write_nothing():
    inputs = _inputs(start=100_000, floor=10_000)
    first = afford(inputs, Hypothetical(amount_paise=10_000, date=date(2026, 1, 1)), runs=10)
    second = afford(inputs, Hypothetical(amount_paise=20_000, date=date(2026, 1, 1)), runs=10)
    assert first["baseline"] == second["baseline"]
    assert len(inputs.events) == 0
    assert inputs.ask_horizon_days == 90


def test_an_emi_installment_split_adds_up_to_the_amount():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    result = afford(
        _inputs(cards=(card,)),
        Hypothetical(amount_paise=120_001, date=date(2026, 1, 1), method="emi", emi_months=3),
        runs=10,
    )
    installments = result["hypothetical"]["installments"]
    assert sum(item["amount_paise"] for item in installments) == 120_001
    assert installments[-1]["amount_paise"] == 40_001


def test_a_delayed_goal_that_never_completes_does_not_print_none_days():
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
                amount_paise=-20_000,
                name="Goa",
                kind="goal_contribution",
                goal_id="goal",
            ),
            CashEvent(
                date=date(2026, 2, 5),
                amount_paise=-20_000,
                name="Goa",
                kind="goal_contribution",
                goal_id="goal",
            ),
            CashEvent(
                date=date(2026, 3, 5),
                amount_paise=-20_000,
                name="Goa",
                kind="goal_contribution",
                goal_id="goal",
            ),
        ),
    )
    baseline = project(inputs, runs=10)
    tradeoff = _delay_goal_tradeoff(
        inputs,
        Hypothetical(amount_paise=20_000, date=date(2026, 1, 1)),
        runs=10,
        seed=DEFAULT_SEED,
        threshold=0.90,
        baseline_goals=baseline["goals"],
    )
    assert tradeoff is not None
    assert tradeoff["slips_days"] is None
    assert "None" not in tradeoff["detail"]


# --- projection: card cycles ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement_day", "due_day", "spend", "expected"),
    [
        (5, 10, date(2026, 1, 5), date(2026, 1, 10)),
        (5, 10, date(2026, 1, 6), date(2026, 2, 10)),
        (25, 5, date(2026, 1, 10), date(2026, 2, 5)),
        # A 30/31 pair in April collapses unless the due date must follow the statement.
        (30, 31, date(2026, 4, 1), date(2026, 5, 31)),
        # February has no 29th or 30th in 2026, so the bill rolls to the next month.
        (29, 30, date(2026, 2, 1), date(2026, 3, 30)),
        (29, 5, date(2028, 2, 1), date(2028, 3, 5)),
        (31, 10, date(2026, 2, 10), date(2026, 3, 10)),
        (31, 10, date(2026, 3, 1), date(2026, 4, 10)),
    ],
)
def test_card_due_dates_respect_the_cycle_and_short_months(
    statement_day: int, due_day: int, spend: date, expected: date
) -> None:
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=statement_day,
        due_day=due_day,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    assert card_due_date(card, spend) == expected


def test_a_purchase_after_the_statement_day_rolls_to_the_next_bill():
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=5,
        due_day=10,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    result = project(
        _inputs(
            cards=(card,),
            events=(
                CashEvent(
                    date=date(2026, 1, 6),
                    amount_paise=-30_000,
                    name="Card bill",
                    kind="obligation",
                    card_id="card",
                ),
            ),
        ),
        runs=10,
    )
    curve = _curve(result)
    assert curve["2026-01-10"]["median_paise"] == 1_000_000
    assert curve["2026-02-10"]["median_paise"] == 970_000


def test_an_initial_card_bill_due_today_is_reserved_today():
    card = CardAccount(
        id="card",
        name="Card",
        statement_day=5,
        due_day=10,
        outstanding_paise=50_000,
        credit_limit_paise=1_000_000,
    )
    result = project(
        _inputs(cards=(card,), as_of=date(2026, 1, 10)),
        runs=10,
    )
    curve = _curve(result)
    assert curve["2026-01-10"]["median_paise"] == 950_000
    assert curve["2026-02-10"]["median_paise"] == 950_000


# --- money ---------------------------------------------------------------------------------


def test_amortize_clears_the_principal_when_the_emi_rounds_down():
    result = amortize(100, 0, 3)
    assert result["months"] == 3
    assert sum(row["principal_paise"] for row in result["schedule"]) == 100
    assert result["schedule"][-1]["balance_paise"] == 0
    assert result["total_paid"]["paise"] == 100


@pytest.mark.parametrize("at_emi", [0, 13])
def test_a_prepayment_outside_the_loan_tenure_is_refused(at_emi: int):
    with pytest.raises(ValueError, match="at_emi"):
        amortize(100_000, 12, 12, prepay={"amount_paise": 1_000, "at_emi": at_emi})


def test_amortize_total_paid_is_the_sum_of_the_actual_installments():
    result = amortize(100_000, 12, 12)
    actual = sum(row["principal_paise"] + row["interest_paise"] for row in result["schedule"])
    assert result["total_paid"]["paise"] == actual
    assert actual == 100_000 + result["total_interest"]["paise"]


# --- recurrence ----------------------------------------------------------------------------


def test_monthly_on_the_31st_falls_on_the_last_day_of_a_short_month():
    dates = expand_rule(
        "FREQ=MONTHLY;BYMONTHDAY=31",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
    )
    assert dates == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
        date(2026, 7, 31),
        date(2026, 8, 31),
        date(2026, 9, 30),
        date(2026, 10, 31),
        date(2026, 11, 30),
        date(2026, 12, 31),
    ]


def test_monthly_on_the_29th_clamps_to_february():
    plain = expand_rule(
        "FREQ=MONTHLY;BYMONTHDAY=29",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
    )
    assert date(2026, 2, 28) in plain
    leap = expand_rule(
        "FREQ=MONTHLY;BYMONTHDAY=29",
        date(2028, 1, 1),
        start=date(2028, 1, 1),
        end=date(2028, 12, 31),
    )
    assert date(2028, 2, 29) in leap


def test_biweekly_and_year_boundary_occurrences_are_exact():
    biweekly = expand_rule(
        "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU",
        date(2026, 11, 1),
        start=date(2026, 11, 1),
        end=date(2026, 12, 31),
    )
    assert biweekly == [
        date(2026, 11, 1),
        date(2026, 11, 15),
        date(2026, 11, 29),
        date(2026, 12, 13),
        date(2026, 12, 27),
    ]
    year_end = expand_rule(
        "FREQ=DAILY",
        date(2026, 12, 30),
        start=date(2026, 12, 30),
        end=date(2027, 1, 2),
    )
    assert year_end == [
        date(2026, 12, 30),
        date(2026, 12, 31),
        date(2027, 1, 1),
        date(2027, 1, 2),
    ]


def test_a_utc_until_rule_is_accepted_and_includes_its_date():
    dates = expand_rule(
        "FREQ=MONTHLY;BYMONTHDAY=31;UNTIL=20261231T000000Z",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2027, 1, 31),
    )
    assert dates[-1] == date(2026, 12, 31)


def test_the_last_occurrence_survives_a_holiday_pushed_past_the_end_date():
    dates = expand_rule(
        "FREQ=MONTHLY;BYMONTHDAY=26;COUNT=1",
        date(2026, 1, 26),
        start=date(2026, 1, 1),
        end=date(2026, 2, 28),
        ends=date(2026, 1, 26),
        holiday_adjust="next",
    )
    assert dates == [date(2026, 1, 27)]


def test_expanding_the_same_rule_twice_is_idempotent():
    rule = "FREQ=MONTHLY;BYMONTHDAY=31"
    first = expand_rule(rule, date(2026, 1, 1), start=date(2026, 1, 1), end=date(2026, 12, 31))
    second = expand_rule(rule, date(2026, 1, 1), start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert first == second
    assert len(first) == len(set(first))


def test_ist_midnight_selects_the_right_day_and_period():
    assert period_for("risk_check", datetime(2026, 11, 8, 18, 29, tzinfo=UTC)) == "2026-11-08"
    assert period_for("risk_check", datetime(2026, 11, 8, 18, 30, tzinfo=UTC)) == "2026-11-09"


def test_a_monthly_charge_on_a_short_month_end_suggests_the_last_day():
    assert _suggested_rule(30, date(2026, 1, 31)) == "FREQ=MONTHLY;BYMONTHDAY=-1"
    assert _suggested_rule(30, date(2026, 1, 15)) == "FREQ=MONTHLY;BYMONTHDAY=15"


def test_backtest_median_error_and_band_calibration_are_computed():
    from fin_agent.engine.backtest import _summarize

    rows = [
        {"error_paise": 10, "absolute_error_paise": 10, "in_band": True},
        {"error_paise": -20, "absolute_error_paise": 20, "in_band": False},
        {"error_paise": 30, "absolute_error_paise": 30, "in_band": True},
        {"error_paise": 40, "absolute_error_paise": 40, "in_band": False},
    ]
    result = _summarize(rows, "test")
    assert result["months"] == 4
    assert result["median_error_paise"] == 20
    assert result["median_absolute_error_paise"] == 25
    assert result["band_calibration"] == 0.5


def test_a_month_end_recurrence_keeps_its_day_of_month():
    from fin_agent.engine.hackerrank import _events_for

    def settled(day: str) -> dict[str, str]:
        return {
            "event_id": f"ev_{day}",
            "status": "settled",
            "direction": "debit",
            "event_type": "subscription",
            "category": "Subscriptions",
            "event_date": day,
            "settlement_date": "",
            "amount": "649",
            "description": "Streaming",
        }

    events, _covered, _sources = _events_for(
        [settled("2025-11-30"), settled("2025-12-30"), settled("2026-01-30")],
        date(2026, 2, 1),
    )
    assert [event.date.isoformat() for event in events] == [
        "2026-02-28",
        "2026-03-30",
        "2026-04-30",
    ]
    assert all(event.amount_paise == -64_900 for event in events)


# --- jobs: recurring phase -----------------------------------------------------------------


async def test_a_late_biweekly_run_keeps_its_fourteen_day_phase(db):
    rule = "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU;BYHOUR=8;BYMINUTE=0"
    occurrence = datetime(2026, 11, 1, 8, 0, tzinfo=INDIA)
    await db.execute(
        """INSERT INTO job_schedules (kind, payload, rule, next_run_at)
           VALUES ('weekly_briefing', '{}'::jsonb, $1, $2)""",
        rule,
        occurrence,
    )
    now = datetime(2026, 11, 3, 10, 0, tzinfo=INDIA)
    assert await enqueue_due_schedules(db, now=now) == 1
    row = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'weekly_briefing'")
    assert row["next_run_at"] == datetime(2026, 11, 15, 8, 0, tzinfo=INDIA)
    job = await db.fetchrow("SELECT * FROM jobs")
    assert job["key"] == f"weekly_briefing:{period_for('weekly_briefing', occurrence)}"


# --- reminders and risk --------------------------------------------------------------------


async def test_a_card_due_forecast_follows_the_statement_close(ledger):
    from fin_agent.ledger.schedules import upcoming

    card = await create_account(
        ledger,
        kind="card",
        name="Month-end card",
        credit_limit_paise=10_000_000,
        statement_day=30,
        due_day=31,
    )
    await record_expense(ledger, account_id=card["id"], amount_paise=300_000, description="Dinner")
    events = (await upcoming(ledger, days=60, start=date(2026, 4, 1)))["events"]
    dues = [event for event in events if event["kind"] == "card_due"]
    assert dues
    assert dues[0]["date"] == "2026-05-31"


async def test_a_zero_day_reminder_lead_still_runs(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    due = ledger_today()
    await create_schedule(
        ledger,
        kind="obligation",
        name="Rent",
        amount_paise=250_000,
        account_id=bank["id"],
        rule="FREQ=DAILY;COUNT=1",
        starts=due,
    )
    reminders = await send_reminders(ledger, as_of=due, lead_days=0)
    assert len(reminders) == 1
    assert reminders[0]["data"]["date"] == due.isoformat()


async def test_a_paid_bill_gets_no_reminder(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    due = ledger_today() + timedelta(days=3)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Rent",
        amount_paise=250_000,
        account_id=bank["id"],
        rule="FREQ=DAILY;COUNT=1",
        starts=due,
    )
    payment = await record_expense(
        ledger, account_id=bank["id"], amount_paise=250_000, description="Rent paid"
    )
    await link_payment(ledger, schedule_id=schedule["id"], due_date=due, movement_id=payment["id"])
    assert await send_reminders(ledger, as_of=ledger_today(), lead_days=3) == []


async def test_a_partially_paid_bill_reminds_only_the_remaining_amount(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    due = ledger_today() + timedelta(days=3)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Rent",
        amount_paise=250_000,
        account_id=bank["id"],
        rule="FREQ=DAILY;COUNT=1",
        starts=due,
    )
    payment = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Part of rent"
    )
    await link_payment(ledger, schedule_id=schedule["id"], due_date=due, movement_id=payment["id"])
    reminders = await send_reminders(ledger, as_of=ledger_today(), lead_days=3)
    assert len(reminders) == 1
    assert "\u20b91,500" in reminders[0]["body"]


async def test_risk_does_not_warn_from_an_unknown_balance(ledger):
    await ledger.execute("UPDATE settings SET floor_paise = 100000 WHERE id = 1")
    account = await create_account(ledger, kind="bank", name="SBI")
    await record_expense(
        ledger, account_id=account["id"], amount_paise=150_000, description="Lunch"
    )
    assert await risk_warning(ledger) is None
    assert (
        await ledger.fetchval("SELECT count(*) FROM notifications WHERE kind = 'risk_warning'") == 0
    )


# --- patterns ------------------------------------------------------------------------------


async def test_an_unrelated_same_amount_expense_does_not_hide_a_missed_bill(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    starts = ledger_today() - timedelta(days=40)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Rent",
        amount_paise=250_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=1;COUNT=1",
        starts=starts,
    )
    dates = expand_rule(
        schedule["rule"],
        starts,
        start=ledger_today() - timedelta(days=90),
        end=ledger_today() - timedelta(days=3),
    )
    assert len(dates) == 1
    await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=250_000,
        description="An unrelated purchase",
        date=dates[0],
    )
    proposals = await detect_missed(ledger, as_of=ledger_today())
    assert len(proposals) == 1
    assert proposals[0]["schedule_id"] == schedule["id"]


async def test_a_partially_paid_bill_is_not_reported_missed(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    starts = ledger_today() - timedelta(days=40)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Rent",
        amount_paise=250_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=1;COUNT=1",
        starts=starts,
    )
    dates = expand_rule(
        schedule["rule"],
        starts,
        start=ledger_today() - timedelta(days=90),
        end=ledger_today() - timedelta(days=3),
    )
    payment = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Part of rent",
        date=dates[0],
    )
    await link_payment(
        ledger, schedule_id=schedule["id"], due_date=dates[0], movement_id=payment["id"]
    )
    assert await detect_missed(ledger, as_of=ledger_today()) == []


async def test_a_missed_bill_pushes_a_missed_notification(ledger, monkeypatch):
    from fin_agent.jobs import patterns as jobs_patterns

    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    await create_schedule(
        ledger,
        kind="obligation",
        name="Rent",
        amount_paise=250_000,
        account_id=bank["id"],
        rule="FREQ=DAILY;COUNT=1",
        starts=ledger_today() - timedelta(days=10),
    )
    pushed: list[str] = []

    async def record(conn, kind):
        del conn
        pushed.append(kind)

    monkeypatch.setattr(jobs_patterns, "send_push_for_kind", record)
    result = await jobs_patterns.handle_pattern_detection({})
    assert result["created"] == 1
    assert pushed == ["missed_recurring"]


async def test_a_reversed_refund_is_not_a_recurring_charge(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    swiggy = await create_payee(ledger, display_name="Swiggy", kind="merchant")
    refund = await record_refund(
        ledger,
        account_id=bank["id"],
        amount_paise=30_000,
        description="Swiggy refund",
        payee_id=swiggy["id"],
        date=ledger_today() - timedelta(days=10),
    )
    await correct_movement(ledger, refund["id"], changes={"delete": True})
    assert await _load_charges(ledger, ledger_today()) == []


# --- projection adapter --------------------------------------------------------------------


async def test_a_configured_income_outside_the_horizon_is_not_a_setup_gap(ledger):
    bank = await create_account(
        ledger,
        kind="bank",
        name="SBI",
        opening_balance_paise=1_000_000,
        opening_balance_as_of=ledger_today(),
    )
    await create_schedule(
        ledger,
        kind="income_fixed",
        name="Annual bonus",
        amount_paise=5_000_000,
        account_id=bank["id"],
        rule="FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=1",
        starts=ledger_today() + timedelta(days=120),
    )
    inputs = await load_inputs(ledger, as_of=ledger_today(), horizon_days=90)
    assert "no confirmed income schedule" not in inputs.data_quality["setup_gaps"]


async def test_a_far_out_emi_loads_the_income_after_the_purchase(ledger):
    from fin_agent.tools import projection as projection_tools

    bank = await create_account(
        ledger,
        kind="bank",
        name="SBI",
        opening_balance_paise=1_000_000,
        opening_balance_as_of=date(2026, 1, 1),
    )
    await create_account(
        ledger,
        kind="card",
        name="Card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
        opening_balance_paise=0,
        opening_balance_as_of=date(2026, 1, 1),
    )
    await create_schedule(
        ledger,
        kind="income_fixed",
        name="Salary",
        amount_paise=500_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=1",
        starts=date(2026, 1, 1),
    )
    result = await projection_tools.project(
        ledger,
        as_of=date(2026, 1, 1),
        runs=20,
        hypothetical={
            "amount_paise": 120_000,
            "date": date(2026, 3, 21),
            "method": "emi",
            "emi_months": 3,
        },
    )
    curve = {point["date"]: point["median_paise"] for point in result["curve"]}
    assert result["horizon_days"] >= 200
    assert curve["2026-07-01"] - curve["2026-06-30"] == 500_000


async def test_a_repaid_debt_is_not_projected_as_a_receipt(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")
    food = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=40_000,
        people=[
            {
                "payee_id": ravi["id"],
                "amount_paise": 20_000,
                "expected_date": ledger_today() + timedelta(days=5),
            }
        ],
        total_paise=60_000,
        category_id=food,
    )
    await record_settlement(
        ledger,
        account_id=bank["id"],
        payee_id=ravi["id"],
        amount_paise=20_000,
        direction="they_paid",
    )
    await record_lending(
        ledger,
        account_id=bank["id"],
        payee_id=ravi["id"],
        amount_paise=200_000,
        expected_date=ledger_today() + timedelta(days=30),
    )
    inputs = await load_inputs(ledger, as_of=ledger_today(), horizon_days=90)
    events = [event for event in inputs.events if event.source_id == ravi["id"]]
    assert len(events) == 1
    assert events[0].amount_paise == 200_000


async def test_money_i_owe_is_a_projected_payment(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")
    await record_lending(
        ledger,
        account_id=bank["id"],
        payee_id=ravi["id"],
        amount_paise=50_000,
        direction="i_borrowed",
        expected_date=ledger_today() + timedelta(days=10),
    )
    inputs = await load_inputs(ledger, as_of=ledger_today(), horizon_days=90)
    events = [event for event in inputs.events if event.source_id == ravi["id"]]
    assert len(events) == 1
    assert events[0].amount_paise == -50_000
    assert events[0].kind == "owed_payment"


async def test_an_undone_expense_is_not_spend_history(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    food = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    await ledger.execute(
        """INSERT INTO baselines (category_id, amount_per_day_paise, source)
           VALUES ($1, 5000, 'estimated')""",
        food,
    )
    movement = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=25_000,
        description="Lunch",
        category_id=food,
        date=ledger_today() - timedelta(days=10),
    )
    await correct_movement(ledger, movement["id"], changes={"delete": True})
    inputs = await load_inputs(ledger, as_of=ledger_today(), horizon_days=30)
    category = next(item for item in inputs.categories if item.id == food)
    assert sum(amount for _day, amount in category.history) == 0


async def test_an_undone_refund_does_not_become_spend(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    food = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    await ledger.execute(
        """INSERT INTO baselines (category_id, amount_per_day_paise, source)
           VALUES ($1, 5000, 'estimated')""",
        food,
    )
    refund = await record_refund(
        ledger,
        account_id=bank["id"],
        amount_paise=30_000,
        description="Swiggy refund",
        category_id=food,
        date=ledger_today() - timedelta(days=10),
    )
    await correct_movement(ledger, refund["id"], changes={"delete": True})
    inputs = await load_inputs(ledger, as_of=ledger_today(), horizon_days=30)
    category = next(item for item in inputs.categories if item.id == food)
    assert sum(amount for _day, amount in category.history) == 0


async def test_a_split_counts_only_my_share_as_spend(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")
    food = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    await ledger.execute(
        """INSERT INTO baselines (category_id, amount_per_day_paise, source)
           VALUES ($1, 5000, 'estimated')""",
        food,
    )
    await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=40_000,
        people=[{"payee_id": ravi["id"], "amount_paise": 20_000}],
        total_paise=60_000,
        category_id=food,
        date=ledger_today() - timedelta(days=5),
    )
    inputs = await load_inputs(ledger, as_of=ledger_today(), horizon_days=30)
    category = next(item for item in inputs.categories if item.id == food)
    assert sum(amount for _day, amount in category.history) == 40_000


# --- summary and card render the computed numbers ------------------------------------------


def test_summary_and_card_report_the_engine_numbers_exactly():
    result = project(
        _inputs(
            start=100_000,
            floor=10_000,
            goals=(
                GoalPlan(
                    id="goal",
                    name="Goa",
                    target_paise=50_000,
                    saved_paise=10_000,
                    deadline=date(2026, 3, 1),
                ),
            ),
        ),
        runs=10,
    )
    summary = projection_summary(result)
    assert summary["floor"]["paise"] == result["floor_paise"]
    assert summary["median_low"]["amount"]["paise"] == result["median_low"]["paise"]
    assert summary["p10_low"]["paise"] == result["p10_low_paise"]
    assert summary["goals"][0]["target"]["paise"] == 50_000
    card = projection_card(result)
    assert card["curve"] == result["curve"]
    assert card["median_low"] == summary["median_low"]
    assert card["floor_probability"] == result["floor_probability"]
