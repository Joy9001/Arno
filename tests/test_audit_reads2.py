"""Audit: the read paths' periods, coverage, literal search, and corrected-row baselines.

Every dataset is built through the real write functions or the real import path, so the rows,
labels, and reversals are exactly what the app produces.
"""

from __future__ import annotations

from datetime import date

import pytest

from arno.importers.baselines import WINDOW_DAYS, recompute_baselines
from arno.importers.commit import undo_import
from arno.importers.synthetic import seed_committed_statement
from arno.ledger.accounts import create_account
from arno.ledger.movements import correct_movement, record_expense
from arno.ledger.payees import create_payee
from arno.tools import periods
from arno.tools import read as read_tools

# 2026-11-11 is a Wednesday, so the open week has run for three days and last week is whole.
WEDNESDAY = date(2026, 11, 11)
MONDAY = date(2026, 11, 9)


async def _category(conn, name: str) -> str:
    return str(await conn.fetchval("SELECT id FROM categories WHERE name = $1", name))


async def _bank(conn, name: str) -> str:
    return (
        await create_account(
            conn,
            kind="bank",
            name=name,
            opening_balance_paise=1_000_000,
            # The day before the first statement period, so the synthetic printed opening agrees.
            opening_balance_as_of=date(2026, 8, 31),
        )
    )["id"]


async def _statement(conn, account_id: str, *, start: date, end: date, day: date) -> dict:
    return await seed_committed_statement(
        conn,
        account_id=account_id,
        narration="Synthetic rent",
        amount_paise=100,
        day=day,
        period_start=start,
        period_end=end,
    )


def test_week_words_start_on_monday_and_an_open_week_ends_today():
    assert periods.resolve_period("this_week", WEDNESDAY) == (MONDAY, WEDNESDAY)
    assert periods.resolve_period("last_week", WEDNESDAY) == (
        date(2026, 11, 2),
        date(2026, 11, 8),
    )
    # On a Monday the open week is one day; on a Sunday it is all seven.
    assert periods.resolve_period("this_week", MONDAY) == (MONDAY, MONDAY)
    assert periods.resolve_period("this_week", date(2026, 11, 15)) == (
        MONDAY,
        date(2026, 11, 15),
    )


async def test_summarize_and_query_accept_the_week_words(ledger, monkeypatch):
    bank = await _bank(ledger, "Week bank")
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=45_000,
        description="This week coffee",
        date=date(2026, 11, 10),
    )
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=10_000,
        description="Last week tea",
        date=date(2026, 11, 3),
    )
    monkeypatch.setattr(read_tools, "ledger_today", lambda: WEDNESDAY)

    now = await read_tools.summarize_spending(ledger, group_by="none", period="this_week")
    assert now["period"] == {"from": "2026-11-09", "to": "2026-11-11"}
    assert now["total"]["paise"] == 45_000
    before = await read_tools.summarize_spending(ledger, group_by="none", period="last_week")
    assert before["period"] == {"from": "2026-11-02", "to": "2026-11-08"}
    assert before["total"]["paise"] == 10_000

    rows = await read_tools.query_movements(ledger, filters={"period": "last_week"})
    assert [row["description"] for row in rows["rows"]] == ["Last week tea"]


async def test_coverage_needs_every_statement_account_to_cover_the_month(ledger):
    covered = await _bank(ledger, "Covered bank")
    missing = await _bank(ledger, "Missing bank")
    await _statement(
        ledger, covered, start=date(2026, 10, 1), end=date(2026, 10, 31), day=date(2026, 10, 5)
    )
    # Missing bank does import statements, just not this month's.
    await _statement(
        ledger, missing, start=date(2026, 9, 1), end=date(2026, 9, 30), day=date(2026, 9, 5)
    )

    one = await read_tools.summarize_spending(ledger, group_by="none", period="2026-10")
    assert one["coverage"]["complete"] is False
    assert one["coverage"]["gaps"] == ["2026-10"]

    # The account filter still answers about the one account that has a statement.
    only = await read_tools.summarize_spending(
        ledger, group_by="none", period="2026-10", filters={"account": "Covered bank"}
    )
    assert only["coverage"]["complete"] is True
    assert only["coverage"]["gaps"] == []

    second = await _statement(
        ledger, missing, start=date(2026, 10, 1), end=date(2026, 10, 31), day=date(2026, 10, 6)
    )
    both = await read_tools.summarize_spending(ledger, group_by="none", period="2026-10")
    assert both["coverage"]["complete"] is True
    assert both["coverage"]["gaps"] == []

    # An account that never imports statements (cash) cannot hold completeness back.
    await create_account(ledger, kind="cash", name="Cash in hand", opening_balance_paise=0)
    still = await read_tools.summarize_spending(ledger, group_by="none", period="2026-10")
    assert still["coverage"]["complete"] is True

    # An undone import stops counting: its month is a gap again.
    await undo_import(ledger, second["import_id"])
    undone = await read_tools.summarize_spending(ledger, group_by="none", period="2026-10")
    assert undone["coverage"]["complete"] is False
    assert undone["coverage"]["gaps"] == ["2026-10"]


async def test_a_literal_text_search_does_not_treat_percent_or_underscore_as_wildcards(ledger):
    bank = await _bank(ledger, "Search bank")
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=45_000,
        description="Sale 50% off",
        date=date(2026, 10, 1),
    )
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=55_000,
        description="Sale 50 rupees off",
        date=date(2026, 10, 2),
    )
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=1_000,
        description="Plan_A renewal",
        date=date(2026, 10, 3),
    )
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=2_000,
        description="PlanXA renewal",
        date=date(2026, 10, 4),
    )

    percent = await read_tools.query_movements(ledger, filters={"text": "50%"})
    assert [row["description"] for row in percent["rows"]] == ["Sale 50% off"]
    percent_spend = await read_tools.summarize_spending(
        ledger, group_by="none", period="2026-10", filters={"text": "50%"}
    )
    assert percent_spend["total"]["paise"] == 45_000

    underscore = await read_tools.query_movements(ledger, filters={"text": "Plan_A"})
    assert [row["description"] for row in underscore["rows"]] == ["Plan_A renewal"]


async def test_a_upi_handle_resolves_regardless_of_case(ledger):
    bank = await _bank(ledger, "UPI bank")
    payee = await create_payee(
        ledger, display_name="Instamart", kind="merchant", upi_id="Instamart@YBL"
    )
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=30_000,
        description="Groceries",
        payee_id=payee["id"],
        date=date(2026, 10, 1),
    )

    rows = await read_tools.query_movements(ledger, filters={"payee": "instamart@ybl"})
    assert [row["payee"] for row in rows["rows"]] == ["Instamart"]
    with pytest.raises(ValueError, match="No payee named"):
        await read_tools.query_movements(ledger, filters={"payee": "instamart@other"})


async def test_a_corrected_bill_contributes_once_to_its_baseline(ledger):
    bank = await _bank(ledger, "Baseline bank")
    groceries = await _category(ledger, "Groceries")
    wrong = await record_expense(
        ledger,
        account_id=bank,
        amount_paise=250_000,
        description="Groceries, entered wrong",
        category_id=groceries,
        date=date(2026, 10, 1),
    )
    await correct_movement(ledger, wrong["id"], changes={"amount_paise": 225_000})
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=100_000,
        description="Groceries",
        category_id=groceries,
        date=date(2026, 10, 2),
    )
    await record_expense(
        ledger,
        account_id=bank,
        amount_paise=50_000,
        description="Groceries",
        category_id=groceries,
        date=date(2026, 10, 3),
    )

    await recompute_baselines(ledger, as_of=date(2026, 10, 5))
    row = await ledger.fetchrow(
        "SELECT amount_per_day_paise, source FROM baselines WHERE category_id = $1",
        groceries,
    )
    assert row["source"] == "computed"
    # (225,000 + 100,000 + 50,000) / 90 days. Counting the corrected-away 250,000 as well
    # would make it 6,944.
    assert row["amount_per_day_paise"] == round(375_000 / WINDOW_DAYS)
    assert row["amount_per_day_paise"] == 4_167
