from __future__ import annotations

from datetime import date

import pytest

from fin_agent.india.formatting import format_paise
from fin_agent.india.recurrence import expand_rule
from fin_agent.india.workdays import adjust_to_working_day, last_working_day
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.errors import InvalidLedgerError
from fin_agent.ledger.movements import record_expense
from fin_agent.ledger.schedules import create_schedule, upcoming


def _category(conn, name: str) -> str:
    return conn.execute("SELECT id FROM categories WHERE name = %s", (name,)).fetchone()["id"]


def test_last_working_day_skips_the_weekend():
    assert last_working_day(2026, 1) == date(2026, 1, 30)
    assert last_working_day(2026, 11) == date(2026, 11, 30)


def test_holiday_adjustment_moves_off_republic_day():
    assert adjust_to_working_day(date(2026, 1, 26), "none") == date(2026, 1, 26)
    assert adjust_to_working_day(date(2026, 1, 26), "next") == date(2026, 1, 27)
    assert adjust_to_working_day(date(2026, 1, 26), "previous") == date(2026, 1, 23)
    assert adjust_to_working_day(date(2026, 1, 24), "next") == date(2026, 1, 27)


def test_monthly_last_weekday_expands_to_twelve_dates():
    dates = expand_rule(
        "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1",
        date(2026, 1, 1),
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
    )

    assert len(dates) == 12
    assert dates[0] == date(2026, 1, 30)
    assert dates[-1] == date(2026, 12, 31)
    assert all(day.weekday() < 5 for day in dates)


def test_holiday_adjusted_bill_moves_to_the_next_working_day(ledger):
    schedule = create_schedule(
        ledger,
        kind="obligation",
        subtype="other",
        name="Insurance premium",
        amount_paise=120_000,
        rule="FREQ=MONTHLY;BYMONTHDAY=26",
        starts=date(2026, 1, 26),
        ends=date(2026, 2, 28),
        holiday_adjust="next",
    )

    dates = expand_rule(
        schedule["rule"],
        date(2026, 1, 26),
        start=date(2026, 1, 1),
        end=date(2026, 2, 28),
        holiday_adjust=schedule["holiday_adjust"],
    )
    assert dates == [date(2026, 1, 27), date(2026, 2, 26)]


def test_instalment_plan_needs_a_count_or_until(ledger):
    with pytest.raises(InvalidLedgerError, match="COUNT or UNTIL"):
        create_schedule(
            ledger,
            kind="instalment",
            name="No end",
            amount_paise=100_000,
            rule="FREQ=MONTHLY;BYMONTHDAY=5",
            starts=date(2026, 1, 5),
        )


def test_instalment_count_produces_three_occurrences(ledger):
    schedule = create_schedule(
        ledger,
        kind="instalment",
        name="Phone on card",
        amount_paise=833_300,
        rule="FREQ=MONTHLY;BYMONTHDAY=5;COUNT=3",
        starts=date(2026, 8, 5),
    )

    dates = expand_rule(
        schedule["rule"],
        date(2026, 8, 5),
        start=date(2026, 8, 1),
        end=date(2026, 12, 31),
    )
    assert dates == [date(2026, 8, 5), date(2026, 9, 5), date(2026, 10, 5)]


def test_variable_income_keeps_its_confirmed_flag(ledger):
    bank = create_account(ledger, kind="bank", name="SBI", opening_balance_paise=0)
    schedule = create_schedule(
        ledger,
        kind="income_variable",
        name="Freelance",
        amount_paise=2_500_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=15",
        starts=date(2026, 11, 1),
        confirmed=False,
    )

    assert schedule["confirmed"] is False
    events = upcoming(ledger, days=45, start=date(2026, 11, 1))["events"]
    freelance = [event for event in events if event["schedule_id"] == schedule["id"]]
    assert freelance
    assert all(event["confirmed"] is False for event in freelance)


def test_upcoming_includes_a_card_due_with_its_outstanding(ledger):
    card = create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    food = _category(ledger, "Food & Dining")
    record_expense(
        ledger, account_id=card["id"], amount_paise=300_000, description="Dinner", category_id=food
    )

    events = upcoming(ledger, days=60, start=date(2026, 11, 1))["events"]
    dues = [event for event in events if event["kind"] == "card_due"]
    assert dues
    assert dues[0]["date"] == "2026-11-25"
    assert dues[0]["amount_paise"] == 300_000


def test_family_transfer_is_an_obligation_subtype(ledger):
    bank = create_account(ledger, kind="bank", name="HDFC", opening_balance_paise=10_000_000)
    family = _category(ledger, "Family Transfer")
    schedule = create_schedule(
        ledger,
        kind="obligation",
        subtype="family_transfer",
        name="Transfer to Mom",
        amount_paise=1_500_000,
        account_id=bank["id"],
        category_id=family,
        rule="FREQ=MONTHLY;BYMONTHDAY=3",
        starts=date(2026, 11, 1),
        holiday_adjust="next",
    )

    assert schedule["subtype"] == "family_transfer"
    assert schedule["account_name"] == "HDFC"
    assert schedule["category_name"] == "Family Transfer"


def test_rupees_use_lakh_grouping():
    assert format_paise(123456789) == "\u20b912,34,567.89"
    assert format_paise(-500) == "-\u20b95.00"
    assert format_paise(0) == "\u20b90.00"
    assert format_paise(123456789012) == "\u20b91,23,45,67,890.12"
