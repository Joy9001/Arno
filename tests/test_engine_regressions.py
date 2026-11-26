"""Engine regressions from the stable-release audit, folded into one feature module."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from arno.engine import series
from arno.engine.affordability import Hypothetical, afford
from arno.engine.money import amortize
from arno.engine.patterns import KnownSchedule, family_interval_days, is_covered
from arno.engine.projection import (
    DEFAULT_SEED,
    LEGACY_MODEL,
    CardAccount,
    CashEvent,
    CurvePoint,
    GoalPlan,
    LiquidAccount,
    Projection,
    ProjectionInput,
    SpendCategory,
    card_due_date,
    project,
    reduce_categories,
)
from arno.india.recurrence import expand_rule, first_occurrence
from arno.jobs.patterns import _new_subscription
from arno.jobs.reminders import send_reminders
from arno.jobs.risk import risk_warning
from arno.ledger.accounts import create_account
from arno.ledger.common import today as ledger_today
from arno.ledger.goals import create_goal
from arno.ledger.movements import (
    correct_movement,
    record_expense,
    record_lending,
    record_refund,
    record_settlement,
    record_split,
)
from arno.ledger.payees import create_payee
from arno.ledger.schedule_payments import link_payment
from arno.ledger.schedules import create_schedule
from arno.scheduled_questions import INDIA, next_occurrence
from arno.tools import projection as projection_tools
from arno.tools.patterns import _load_spend_payments, detect_missed
from arno.tools.projection import (
    card as projection_card,
)
from arno.tools.projection import (
    load_inputs,
)
from arno.tools.projection import (
    summary as projection_summary,
)

# --- projection, money, recurrence, jobs and loader regressions --------------------------

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


def _curve(result: Projection) -> dict[str, CurvePoint]:
    return {point["date"]: point for point in result["curve"]}


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
        Hypothetical(
            amount_paise=120_001,
            date=date(2026, 1, 1),
            method="emi",
            emi_months=3,
            no_cost=True,
            emi_fee_paise=0,
        ),
        runs=10,
    )
    installments = result["hypothetical"]["installments"]
    assert sum(item["amount_paise"] for item in installments) == 120_001
    assert installments[-1]["amount_paise"] == 40_001


def test_an_emi_with_no_known_rate_carries_the_labeled_default_cost():
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
        Hypothetical(amount_paise=120_000, date=date(2026, 1, 1), method="emi", emi_months=3),
        runs=10,
    )
    meta = result["hypothetical"]
    assert meta["emi_rate_bps"] == 1_600
    assert meta["emi_cost_paise"] > 0
    assert (
        sum(item["amount_paise"] for item in meta["installments"])
        == 120_000 + meta["emi_cost_paise"]
    )
    assert any("assumes 16% a year" in gap for gap in result["data_quality"]["gaps"])


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
    # Paid as asked the balance goes below zero; skipping January's contribution keeps it above,
    # and the goal then never completes inside the horizon.
    result = afford(
        inputs, Hypothetical(amount_paise=50_000, date=date(2026, 1, 1)), runs=10, seed=DEFAULT_SEED
    )
    tradeoff = next(trade for trade in result["tradeoffs"] if trade["kind"] == "delay_goal")
    assert tradeoff["slips_days"] is None
    assert "None" not in tradeoff["detail"]


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


def test_a_proposed_rule_is_the_rule_the_detector_found():
    proposal = {
        "kind": "new_subscription",
        "merchant": "Rent",
        "normalized": "rent",
        "interval_days": 15,
        "amount_paise": 2_800_000,
        "occurrences": 6,
        "first_date": "2026-01-01",
        "last_date": "2026-03-31",
        "rule": {
            "family": "twice_monthly",
            "rrule": "FREQ=MONTHLY;BYMONTHDAY=1,31",
            "description": "twice monthly on the 1st and 31st",
        },
    }
    _, _, _, data = _new_subscription(proposal)
    assert data["suggested_rule"] == "FREQ=MONTHLY;BYMONTHDAY=1,31"


async def test_a_card_due_forecast_follows_the_statement_close(ledger):
    from arno.ledger.schedules import upcoming

    card = await create_account(
        ledger,
        kind="card",
        name="Month-end card",
        credit_limit_paise=10_000_000,
        statement_day=30,
        due_day=31,
    )
    await record_expense(
        ledger,
        account_id=card["id"],
        amount_paise=300_000,
        description="Dinner",
        date=date(2026, 4, 1),
    )
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
    assert await _load_spend_payments(ledger, ledger_today()) == []


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
    from arno.tools import projection as projection_tools

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


# --- spend history, category cuts and job anchors ----------------------------------------


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
    # The legacy model spends a flat estimate exactly, so the cut's arithmetic is visible.
    curve = _curve(project(reduced, runs=3, model=LEGACY_MODEL))

    assert curve["2026-01-06"]["median_paise"] == 1_000_000 - 5 * 5_000
    assert curve["2026-01-11"]["median_paise"] == 1_000_000 - 10 * 5_000
    assert curve["2026-01-12"]["median_paise"] == 1_000_000 - 10 * 5_000 - 10_000
    assert rows[0]["new_baseline_per_day_paise"] == 5_000
    assert rows[0]["until"] == "2026-01-11"

    # Without a date the cut runs to the end of the horizon, which is what the old code did.
    permanent, _rows = reduce_categories(inputs, 0.5)
    curve = _curve(project(permanent, runs=3, model=LEGACY_MODEL))
    assert curve["2026-01-12"]["median_paise"] == 1_000_000 - 11 * 5_000


def test_the_cut_tradeoff_ends_at_the_next_payday():
    from dataclasses import replace

    from arno.engine.affordability import Hypothetical, afford
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
    assert with_purchase["floor_probability"] < 0.9
    result = afford(inputs, purchase, runs=100)
    tradeoff = next(trade for trade in result["tradeoffs"] if trade["kind"] == "cut_category")
    assert tradeoff["until"] == "2026-01-31"
    assert tradeoff["ratio"] <= 0.5
    assert tradeoff["categories"][0]["until"] == "2026-01-31"
    assert "then spend as usual" in tradeoff["detail"]


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

    # The same request without the "only what existed then" reads today's rows.
    today_inputs = await load_inputs(ledger, as_of=as_of)
    assert [account.id for account in today_inputs.liquid] == [bank["id"]]

    now = await load_inputs(ledger, as_of=today)
    assert [account.id for account in now.liquid] == [bank["id"]]
    assert any(event.source_id == schedule["id"] for event in now.events)
    assert [row.id for row in now.goals] == [goal["id"]]


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
    row = await risk_warning(db)
    assert row is not None
    body = row["body"]
    assert "staying above zero" in body
    assert "below the 90% line" in body
    assert "your floor" not in body
    assert "No floor is set" in body


async def test_risk_keeps_its_floor_wording_when_a_floor_is_set(db, monkeypatch):
    async def fake_project(conn, **kwargs):
        del conn, kwargs
        return _fake_projection(floor_paise=100_000, first_floor_crossing="2026-11-20")

    monkeypatch.setattr("arno.tools.projection.project", fake_project)
    row = await risk_warning(db)
    assert row is not None
    body = row["body"]
    assert "staying above your floor" in body
    assert "zero" not in body


async def test_risk_is_quiet_against_zero_when_the_walks_hold(db, monkeypatch):
    async def fake_project(conn, **kwargs):
        del conn, kwargs
        return _fake_projection(floor_probability=0.95)

    monkeypatch.setattr("arno.tools.projection.project", fake_project)
    assert await risk_warning(db) is None


# --- prepayment, verdicts, pattern detection and demo regressions ------------------------

DEMO_DAY = date(2026, 11, 1)


def _demo_loan(**extra):
    # The demo persona's loan: ₹33,88,000 at 8.5%, ₹31,500 a month, 204 EMIs left of 240.
    return amortize(
        338_800_000,
        8.5,
        240,
        emi_paise=3_150_000,
        start_date=date(2026, 11, 10),
        **extra,
    )


def test_reducing_the_emi_keeps_the_emas_a_loan_actually_has_left():
    base = _demo_loan()
    assert base["months"] == 204

    reduced = _demo_loan(prepay={"amount_paise": 50_000_000, "at_emi": 1, "reduce": "emi"})
    prepay = reduced["prepay"]
    assert prepay is not None
    # ₹5,00,000 off ₹33,88,000 with 204 EMIs left: ₹26,809.08 a month, still paid off in 204.
    assert prepay["new_emi"]["paise"] == 2_680_908
    assert prepay["new_months"] == 204
    assert prepay["months_saved"] == 0
    assert prepay["interest_saved"]["paise"] == 43_446_199


def test_no_prepayment_option_ever_reports_a_negative_saving_or_a_longer_loan():
    base = _demo_loan()
    for reduce in ("emi", "tenure"):
        prepay = _demo_loan(prepay={"amount_paise": 50_000_000, "at_emi": 1, "reduce": reduce})[
            "prepay"
        ]
        assert prepay is not None
        assert prepay["interest_saved"]["paise"] > 0, reduce
        assert prepay["months_saved"] >= 0, reduce
        assert prepay["new_months"] <= base["months"], reduce


async def test_a_named_loan_prepayment_uses_its_real_remaining_emis(demo_ledger):
    from arno.ledger.accounts import loan_terms

    terms = await loan_terms(demo_ledger, "Home loan")
    base = amortize(
        terms["principal_paise"],
        terms["annual_rate"],
        terms["months"],
        emi_paise=terms["emi_paise"],
        start_date=DEMO_DAY,
    )
    reduced = amortize(
        terms["principal_paise"],
        terms["annual_rate"],
        terms["months"],
        emi_paise=terms["emi_paise"],
        start_date=DEMO_DAY,
        prepay={"amount_paise": 50_000_000, "at_emi": 1, "reduce": "emi"},
    )
    prepay = reduced["prepay"]
    assert prepay is not None
    assert prepay["new_emi"]["paise"] < base["emi"]["paise"]
    assert prepay["new_months"] <= base["months"]
    assert prepay["interest_saved"]["paise"] > 0


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=INDIA)


def _next(rule: str, after: datetime) -> datetime | None:
    return next_occurrence(rule, after=after, created_at=_at(10), at=time(8, 0))


def test_a_count_one_question_runs_once_and_is_never_restarted():
    rule = "FREQ=WEEKLY;BYDAY=MO;COUNT=1"
    assert _next(rule, _at(10)) == datetime(2026, 11, 16, 8, 0, tzinfo=INDIA)
    assert _next(rule, _at(20)) is None
    assert _next(rule, _at(30)) is None


def test_a_count_three_question_runs_three_mondays_and_stops():
    rule = "FREQ=WEEKLY;BYDAY=MO;COUNT=3"
    assert _next(rule, _at(10)) == datetime(2026, 11, 16, 8, 0, tzinfo=INDIA)
    assert _next(rule, _at(16)) == datetime(2026, 11, 23, 8, 0, tzinfo=INDIA)
    assert _next(rule, _at(23)) == datetime(2026, 11, 30, 8, 0, tzinfo=INDIA)
    assert _next(rule, _at(30)) is None


def _verdict_inputs(
    *,
    start: int = 1_000_000,
    events: tuple[CashEvent, ...] = (),
    cards: tuple[CardAccount, ...] = (),
) -> ProjectionInput:
    return ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=0,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=start),),
        events=events,
        cards=cards,
        ask_horizon_days=90,
    )


def _salary(when: date, amount: int = 200_000) -> CashEvent:
    return CashEvent(date=when, amount_paise=amount, name="Salary", kind="income_fixed")


def test_a_wait_date_alone_keeps_the_verdict_no_and_is_still_listed():
    result = afford(
        _verdict_inputs(start=50_000, events=(_salary(date(2026, 2, 1)),)),
        Hypothetical(amount_paise=100_000, date=date(2026, 1, 1)),
        runs=50,
    )
    assert result["verdict"] == "no"
    assert [trade["kind"] for trade in result["tradeoffs"]] == ["wait"]
    assert result["tradeoffs"][0]["date"] == "2026-02-01"
    assert "of simulated futures" in result["verdict_reason"]


def test_a_wait_date_equal_to_the_asked_date_is_not_a_tradeoff():
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
        _verdict_inputs(start=1_000_000, cards=(card,)),
        Hypothetical(
            amount_paise=100_000, date=date(2026, 1, 1), method="card", account="Slice card"
        ),
        runs=20,
    )
    assert result["hypothetical"]["feasible"] is False
    assert result["verdict"] == "no"
    assert result["tradeoffs"] == []
    assert "unknown" in result["verdict_reason"]


def test_a_same_date_tradeoff_still_makes_it_yes_but():
    card = CardAccount(
        id="card",
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=1_000_000,
    )
    result = afford(
        _verdict_inputs(start=50_000, events=(_salary(date(2026, 1, 20)),), cards=(card,)),
        Hypothetical(amount_paise=100_000, date=date(2026, 1, 1), method="account"),
        runs=50,
    )
    assert result["verdict"] == "yes-but"
    by_kind = {trade["kind"]: trade for trade in result["tradeoffs"]}
    assert by_kind["wait"]["date"] == "2026-01-20"
    assert by_kind["pay_by_card"]["due_date"] == "2026-01-25"


async def test_the_projection_names_its_goal_remaining_apart_from_a_goal_card(demo_ledger):
    result = await projection_tools.project(demo_ledger, as_of=DEMO_DAY, runs=50)
    goal = next(row for row in result["goals"] if row["name"] == "Goa trip")
    assert "remaining_paise" not in goal
    assert goal["remaining_after_plan_paise"] >= 0

    compact = projection_tools.summary(result)
    compact_goal = next(row for row in compact["goals"] if row["name"] == "Goa trip")
    assert "remaining" not in compact_goal
    assert compact_goal["remaining_after_plan"]["paise"] == goal["remaining_after_plan_paise"]

    card = projection_tools.card(result)
    card_goal = next(row for row in card["goals"] if row["name"] == "Goa trip")
    assert card_goal["remaining_after_plan"]["paise"] == goal["remaining_after_plan_paise"]
    assert "remaining" not in card_goal

    # The goal card's own figure still means target minus saved.
    from arno.ledger.goals import list_goals

    listed = next(row for row in await list_goals(demo_ledger) if row["name"] == "Goa trip")
    assert listed["remaining_paise"] == 9_000_000


def _rent_payments() -> list[series.Payment]:
    return [
        series.Payment(movement_id=f"mov_{index}", date=when, amount_paise=2_800_000)
        for index, when in enumerate(
            (date(2026, 8, 5), date(2026, 9, 5), date(2026, 10, 5), date(2026, 11, 5))
        )
    ]


def _rent_schedule(*, amount_paise: int = 2_800_000, interval_days: int = 30) -> KnownSchedule:
    return KnownSchedule(
        id="sch_rent",
        name="Rent",
        normalized="rent",
        amount_paise=amount_paise,
        category_id="cat_rent",
        interval_days=interval_days,
    )


def _is_rent_covered(schedule: KnownSchedule) -> bool:
    as_of = date(2026, 11, 10)
    detected = series.detect(
        _rent_payments(),
        as_of=as_of,
        window_start=date(2026, 1, 1),
    )
    assert detected is not None
    return is_covered(
        normalized=series.normalize("Ramesh (landlord)"),
        categories={"cat_rent"},
        amount_paise=detected.amounts.level_paise,
        interval_days=family_interval_days(detected.rule.family),
        schedules=[schedule],
    )


def test_a_schedule_covers_a_payee_charge_by_category_amount_and_cadence():
    # "Rent" and "Ramesh (landlord)" share no word, so name matching alone proposed it again.
    assert _is_rent_covered(_rent_schedule()) is True


def test_a_different_amount_is_still_a_new_recurring_charge():
    assert _is_rent_covered(_rent_schedule(amount_paise=2_000_000)) is False


def test_a_different_cadence_is_still_a_new_recurring_charge():
    assert _is_rent_covered(_rent_schedule(interval_days=7)) is False


async def test_the_demo_personas_rent_is_not_proposed_as_a_new_charge(demo_ledger):
    from arno.tools import patterns

    proposals = await patterns.detect(demo_ledger, as_of=DEMO_DAY)
    assert not any(
        proposal["kind"] == "new_subscription" and "Ramesh" in proposal["merchant"]
        for proposal in proposals
    )


async def test_series_detection_proposes_subscription_and_income_and_skips_random_and_covered(
    ledger,
):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_expense, record_income
    from arno.ledger.payees import create_payee
    from arno.ledger.schedules import create_schedule
    from arno.tools import patterns

    bank = await create_account(ledger, kind="bank", name="HDFC", opening_balance_paise=10_000_000)
    sub_payee = await create_payee(ledger, display_name="Cloud Storage", kind="merchant")
    rand_payee = await create_payee(ledger, display_name="Local Cafe", kind="merchant")
    salary_payee = await create_payee(ledger, display_name="Acme Corp", kind="organization")
    rent_cat = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Rent' AND active")

    for month in (6, 7, 8, 9):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=99_900,
            description="Cloud Storage",
            payee_id=sub_payee["id"],
            category_id=str(rent_cat) if rent_cat else None,
            date=date(2026, month, 5),
        )
        await record_income(
            ledger,
            account_id=bank["id"],
            amount_paise=10_000_000,
            description="Monthly salary",
            payee_id=salary_payee["id"],
            date=date(2026, month, 1),
        )

    for when, amt in (
        (date(2026, 6, 2), 18_000),
        (date(2026, 6, 21), 62_000),
        (date(2026, 7, 30), 25_000),
        (date(2026, 9, 4), 140_000),
    ):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amt,
            description="Coffee",
            payee_id=rand_payee["id"],
            date=when,
        )

    as_of = date(2026, 10, 15)
    proposals = await patterns.detect(ledger, as_of=as_of)
    sub = next(p for p in proposals if p["kind"] == "new_subscription")
    assert sub["payee_id"] == sub_payee["id"]
    assert sub["confidence"] >= 0.9
    assert sub["rule"]["family"] == "monthly"
    assert sub["rule"]["description"]
    assert not any(p.get("payee_id") == rand_payee["id"] for p in proposals)

    inc = next(p for p in proposals if p["kind"] == "new_income")
    assert inc["payee_id"] == salary_payee["id"]
    assert inc["confidence"] >= 0.9
    assert inc["rule"]["family"] == "monthly"

    await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Housing",
        amount_paise=99_900,
        account_id=bank["id"],
        category_id=str(rent_cat) if rent_cat else None,
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 6, 5),
    )
    after_cover = await patterns.detect(ledger, as_of=as_of)
    assert not any(
        p["kind"] == "new_subscription" and p["payee_id"] == sub_payee["id"] for p in after_cover
    )


async def test_a_stepped_spend_series_is_proposed_as_a_price_change(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_expense
    from arno.ledger.payees import create_payee
    from arno.tools import patterns

    bank = await create_account(ledger, kind="bank", name="HDFC", opening_balance_paise=10_000_000)
    cloud = await create_payee(
        ledger, display_name="Cloud Storage", kind="merchant", outside_text=True
    )
    for index, amount in enumerate([49_900, 49_900, 49_900, 59_900, 59_900, 59_900]):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="Cloud Storage",
            payee_id=cloud["id"],
            date=date(2026, 6 + index, 5),
        )

    proposals = await patterns.detect(ledger, as_of=date(2026, 11, 20))
    changes = [p for p in proposals if p["kind"] == "price_change"]
    assert len(changes) == 1
    change = changes[0]
    assert change["payee_id"] == cloud["id"]
    assert change["merchant"] == "Cloud Storage"
    assert change["outside_text"] is True
    assert (change["old_paise"], change["new_paise"]) == (49_900, 59_900)
    assert change["step_date"] == "2026-09-05"
    assert len(change["movement_ids"]) == 3


async def test_a_schedule_paid_at_a_new_level_is_proposed_once_as_a_price_change(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_expense
    from arno.ledger.payees import create_payee
    from arno.ledger.schedule_payments import link_payment
    from arno.ledger.schedules import create_schedule
    from arno.tools import patterns

    bank = await create_account(ledger, kind="bank", name="HDFC", opening_balance_paise=10_000_000)
    insurer = await create_payee(ledger, display_name="HDFC Life", kind="merchant")
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="insurance",
        name="HDFC Life",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 6, 5),
    )
    for month, amount in ((6, 100_000), (7, 100_000), (8, 120_000), (9, 120_000)):
        payment = await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="HDFC Life premium",
            payee_id=insurer["id"],
            date=date(2026, month, 5),
        )
        await link_payment(
            ledger,
            schedule_id=schedule["id"],
            due_date=date(2026, month, 5),
            movement_id=payment["id"],
        )

    proposals = await patterns.detect(ledger, as_of=date(2026, 9, 20))
    changes = [p for p in proposals if p["kind"] == "price_change"]
    # The schedule owns the charge, so its series is not reported as well.
    assert len(changes) == 1
    assert changes[0]["schedule_id"] == schedule["id"]
    assert (changes[0]["old_paise"], changes[0]["new_paise"]) == (100_000, 120_000)
    assert changes[0]["step_date"] == "2026-08-05"
    assert len(changes[0]["movement_ids"]) == 2
    assert not any(p["kind"] == "new_subscription" for p in proposals)


async def test_a_partial_payment_in_progress_is_not_a_price_drop(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_expense
    from arno.ledger.payees import create_payee
    from arno.ledger.schedule_payments import link_payment
    from arno.ledger.schedules import create_schedule
    from arno.tools import patterns

    bank = await create_account(ledger, kind="bank", name="HDFC", opening_balance_paise=10_000_000)
    insurer = await create_payee(ledger, display_name="HDFC Life", kind="merchant")
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="insurance",
        name="HDFC Life",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 6, 5),
    )
    for month, amount in ((6, 100_000), (7, 60_000)):
        payment = await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=amount,
            description="HDFC Life premium",
            payee_id=insurer["id"],
            date=date(2026, month, 5),
        )
        await link_payment(
            ledger,
            schedule_id=schedule["id"],
            due_date=date(2026, month, 5),
            movement_id=payment["id"],
        )

    # Two days past due, the short payment may still be topped up.
    early = await patterns.detect(ledger, as_of=date(2026, 7, 7))
    assert not [p for p in early if p["kind"] == "price_change"]
    # Past the missed-charge grace it is evidence of a lower level.
    late = await patterns.detect(ledger, as_of=date(2026, 7, 9))
    changes = [p for p in late if p["kind"] == "price_change"]
    assert len(changes) == 1
    assert (changes[0]["old_paise"], changes[0]["new_paise"]) == (100_000, 60_000)


async def test_the_demo_loan_book_a_months_interest_at_its_rate(demo_ledger):
    rows = await demo_ledger.fetch(
        """
        SELECT m.id, m.date,
               sum(CASE WHEN a.kind = 'spending' THEN l.amount_paise ELSE 0 END)::bigint
                   AS interest_paise,
               sum(CASE WHEN a.kind = 'loan' THEN l.amount_paise ELSE 0 END)::bigint
                   AS principal_paise
        FROM movements m
        JOIN movement_legs l ON l.movement_id = m.id
        JOIN accounts a ON a.id = l.to_account_id
        WHERE m.kind = 'loan'
        GROUP BY m.id, m.date
        ORDER BY m.date
        """
    )
    assert len(rows) >= 3
    first = rows[0]
    # 8.5% of the seeded ₹35,00,000 over a year is ₹24,791.67 a month, not a flat ₹3,500.
    assert 2_470_000 <= first["interest_paise"] <= 2_490_000
    assert first["interest_paise"] + first["principal_paise"] == 3_150_000
    # Interest falls as the principal does.
    assert all(row["interest_paise"] < first["interest_paise"] for row in rows[1:]), [
        row["interest_paise"] for row in rows
    ]


async def test_no_live_demo_movement_still_says_it_was_entered_wrong(demo_ledger):
    rows = await demo_ledger.fetch("SELECT description FROM movements")
    assert not any("wrong" in str(row["description"]).lower() for row in rows)
