"""Audit wave 3 (reads3): the age, credit, and coverage numbers the read path reports.

Each test pins one figure the context snapshot or an account payload states, so that the age a
balance claims, the credit a card can still use, and the setup gaps cannot drift from the ledger
rows behind them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from fin_agent.engine.money import money
from fin_agent.engine.projection import CardAccount, card_due_date, statement_close
from fin_agent.ledger import goals
from fin_agent.ledger.accounts import create_account, get_account, set_statement_balance
from fin_agent.ledger.common import today as ledger_today
from fin_agent.ledger.movements import record_expense, record_transfer
from fin_agent.ledger.schedules import create_schedule
from fin_agent.scheduled_questions import archive_question, create_question
from fin_agent.tools import periods
from fin_agent.tools import read as read_tools


def _context_account(context: dict, account_id: str) -> dict:
    return next(item for item in context["accounts"] if item["id"] == account_id)


async def test_balance_age_is_measured_from_the_latest_evidence(ledger):
    today = ledger_today()
    stated = today - timedelta(days=100)
    account = await create_account(
        ledger,
        kind="bank",
        name="Aged bank",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=stated,
    )

    context = await read_tools.get_financial_context(ledger)
    row = _context_account(context, account["id"])
    assert row["as_of"] == stated.isoformat()
    assert row["age_days"] == 100
    assert "Aged bank balance is 100 days old" in context["warnings"]

    # The ledger balance is exact as of its last movement: 2 days old, not 100.
    await record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=1_000,
        description="Chai",
        date=today - timedelta(days=2),
    )
    context = await read_tools.get_financial_context(ledger)
    row = _context_account(context, account["id"])
    assert row["as_of"] == (today - timedelta(days=2)).isoformat()
    assert row["age_days"] == 2
    assert not any("Aged bank balance is" in warning for warning in context["warnings"])

    payload = await get_account(ledger, account["id"])
    assert payload["balance_age_days"] == 2
    # The statement anchor keeps its own, literally named age.
    assert payload["statement_age_days"] == 100


async def test_a_goal_account_balance_is_known_by_construction(ledger):
    goal = await goals.create_goal(ledger, name="Goa trip", target_paise=12_000_000)
    context = await read_tools.get_financial_context(ledger)
    row = _context_account(context, goal["account_id"])
    assert row["known"] is True
    assert row["balance"] == money(0)
    assert "an account has no stated balance" not in context["setup_gaps"]

    bank = (
        await create_account(ledger, kind="bank", name="Bank", opening_balance_paise=5_000_000)
    )["id"]
    await record_transfer(
        ledger,
        from_account_id=bank,
        to_account_id=goal["account_id"],
        amount_paise=500_000,
    )
    context = await read_tools.get_financial_context(ledger)
    row = _context_account(context, goal["account_id"])
    assert row["known"] is True
    assert row["balance"] == money(500_000)


async def test_available_credit_counts_a_credit_balance(ledger):
    today = ledger_today()
    card = await create_account(
        ledger,
        kind="card",
        name="Credit card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    await set_statement_balance(ledger, card["id"], balance_paise=-100_000, as_of=today)
    assert (await get_account(ledger, card["id"]))["available_credit_paise"] == 9_900_000

    # An overpayment leaves the card in credit: the limit plus the credit is usable.
    await set_statement_balance(ledger, card["id"], balance_paise=50_000, as_of=today)
    payload = await get_account(ledger, card["id"])
    assert payload["card_outstanding_paise"] == 0
    assert payload["available_credit_paise"] == 10_050_000

    context = await read_tools.get_financial_context(ledger)
    assert _context_account(context, card["id"])["available_credit"] == money(10_050_000)


async def test_financial_context_keeps_active_and_the_loan_terms(ledger):
    loan = await create_account(
        ledger,
        kind="loan",
        name="Car loan",
        opening_balance_paise=-50_000_000,
        opening_balance_as_of=ledger_today(),
        rate_bps=1250,
        tenure_months=60,
    )
    context = await read_tools.get_financial_context(ledger)
    row = _context_account(context, loan["id"])
    assert row["active"] is True
    assert row["rate_bps"] == 1250
    assert row["tenure_months"] == 60


async def test_financial_context_lists_the_active_scheduled_questions(ledger):
    question = await create_question(
        ledger, prompt="What did I spend on food last week?", rule="FREQ=WEEKLY;BYDAY=MO"
    )
    context = await read_tools.get_financial_context(ledger)
    listed = next(item for item in context["scheduled_questions"] if item["id"] == question["id"])
    assert listed["prompt"] == "What did I spend on food last week?"
    assert listed["rule"] == "FREQ=WEEKLY;BYDAY=MO"
    run_at = datetime.fromisoformat(listed["next_run"])
    assert run_at.utcoffset() == timedelta(hours=5, minutes=30)

    await archive_question(ledger, question["id"])
    context = await read_tools.get_financial_context(ledger)
    assert question["id"] not in {item["id"] for item in context["scheduled_questions"]}


async def test_a_schedule_in_the_context_lists_dates_past_thirty_days(ledger):
    today = ledger_today()
    bank = (
        await create_account(ledger, kind="bank", name="Bank", opening_balance_paise=5_000_000)
    )["id"]
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Flat rent",
        amount_paise=2_800_000,
        account_id=bank,
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2025, 1, 5),
        holiday_adjust="none",
    )
    context = await read_tools.get_financial_context(ledger)
    row = next(item for item in context["schedules"] if item["id"] == schedule["id"])
    dates = row["next_dates"]
    assert len(dates) == 4
    assert all(day.endswith("-05") for day in dates)
    assert dates[0] > today.isoformat()
    assert dates[3] > (today + timedelta(days=30)).isoformat()


async def test_financial_context_gives_a_card_its_next_close_and_bill_due(ledger):
    today = ledger_today()
    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    await set_statement_balance(ledger, card["id"], balance_paise=0, as_of=today)
    terms = CardAccount(
        id=card["id"],
        name="Slice card",
        statement_day=5,
        due_day=25,
        outstanding_paise=0,
        credit_limit_paise=10_000_000,
    )
    context = await read_tools.get_financial_context(ledger)
    row = _context_account(context, card["id"])
    assert row["next_statement_close"] == statement_close(terms, today).isoformat()
    assert row["next_bill_due"] == card_due_date(terms, today).isoformat()


def test_a_single_iso_date_is_a_one_day_period():
    today = date(2026, 11, 9)
    assert periods.resolve_period("2026-11-09", today) == (
        date(2026, 11, 9),
        date(2026, 11, 9),
    )
    assert periods.resolve_period("2026-11-01", today) == (
        date(2026, 11, 1),
        date(2026, 11, 1),
    )
    with pytest.raises(ValueError, match="Unknown period") as error:
        periods.resolve_period("9 Nov", today)
    assert '"YYYY-MM-DD", or' in str(error.value)
    with pytest.raises(ValueError, match="has not started"):
        periods.resolve_period("2026-11-10", today)


async def test_query_movements_accepts_a_single_date_period(ledger):
    bank = (
        await create_account(ledger, kind="bank", name="Bank", opening_balance_paise=5_000_000)
    )["id"]
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=45_000,
        description="Coffee",
        date=date(2026, 11, 9),
    )
    result = await read_tools.query_movements(ledger, filters={"period": "2026-11-09"})
    assert result["total_count"] == 1
