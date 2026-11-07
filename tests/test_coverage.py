"""Coverage is derived from committed imports, at the part of a month that was asked for."""

from __future__ import annotations

import calendar
from datetime import date

from fin_agent.importers.synthetic import seed_committed_statement
from fin_agent.ledger.accounts import create_account
from fin_agent.tools.read import coverage


async def _account(conn, name: str) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name=name,
        institution="Synthetic Bank",
        last4="4321",
        opening_balance_paise=1_000_000,
        opening_balance_as_of=date(2020, 1, 1),
    )


async def _statement(conn, account_id: str, *, start: date, end: date) -> dict:
    return await seed_committed_statement(
        conn,
        account_id=account_id,
        narration="Synthetic rent",
        amount_paise=100,
        day=start,
        period_start=start,
        period_end=end,
    )


async def test_a_committed_statement_covers_its_month_and_leaves_older_months_gaps(ledger):
    account = await _account(ledger, "Covered bank")
    await _statement(
        ledger,
        account["id"],
        start=date(2026, 6, 1),
        end=date(2026, 6, 30),
    )

    june = await coverage(
        ledger, account_id=account["id"], start=date(2026, 6, 1), end=date(2026, 6, 30)
    )
    assert june["complete"] is True
    assert june["gaps"] == []

    may_june = await coverage(
        ledger, account_id=account["id"], start=date(2026, 5, 1), end=date(2026, 6, 30)
    )
    assert may_june["complete"] is False
    assert may_june["gaps"] == ["2026-05"]


async def test_a_partial_statement_does_not_make_the_rest_of_the_month_known(ledger):
    account = await _account(ledger, "Partial bank")
    await _statement(
        ledger,
        account["id"],
        start=date(2026, 6, 1),
        end=date(2026, 6, 5),
    )

    june = await coverage(
        ledger, account_id=account["id"], start=date(2026, 6, 1), end=date(2026, 6, 30)
    )
    assert june["complete"] is False
    assert june["gaps"] == ["2026-06"]


async def test_adjacent_statements_cover_a_month_together(ledger):
    account = await _account(ledger, "Split bank")
    await _statement(
        ledger,
        account["id"],
        start=date(2026, 6, 1),
        end=date(2026, 6, 15),
    )
    await _statement(
        ledger,
        account["id"],
        start=date(2026, 6, 16),
        end=date(2026, 6, 30),
    )

    june = await coverage(
        ledger, account_id=account["id"], start=date(2026, 6, 1), end=date(2026, 6, 30)
    )
    assert june["complete"] is True
    assert june["gaps"] == []


async def test_coverage_uses_the_actual_month_end(ledger):
    account = await _account(ledger, "Leap bank")
    last = date(2024, 2, calendar.monthrange(2024, 2)[1])
    assert last == date(2024, 2, 29)
    await _statement(
        ledger,
        account["id"],
        start=date(2024, 2, 1),
        end=last,
    )
    february = await coverage(ledger, account_id=account["id"], start=date(2024, 2, 1), end=last)
    assert february["complete"] is True
