"""Payee attribution for statement and split write counterparties."""

from __future__ import annotations

from datetime import date

import pytest

from arno.importers.commit import commit_import
from arno.importers.pipeline import import_payload
from arno.ledger.accounts import create_account
from tests.fixtures.import_review import review_statement

PERIOD = (date(2026, 4, 1), date(2026, 4, 30))
OPENING = 29020000
DEBIT = 432100


async def _bank(conn) -> dict:
    created = await create_account(
        conn,
        kind="bank",
        name="SBI Salary",
        institution="SBI",
        last4="6789",
        opening_balance_paise=OPENING,
        opening_balance_as_of=date(2026, 3, 31),
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def _review(conn, account: dict, *, counterparty: str | None) -> str:
    return await review_statement(
        conn,
        account,
        [
            {
                "date": "2026-04-10",
                "description": "UPI/DR/555000111222/BOOKWORM PUBLISHERS",
                "debit": DEBIT,
                "credit": 0,
                "balance": None,
                "counterparty": counterparty,
            }
        ],
        start=PERIOD[0],
        end=PERIOD[1],
        opening=OPENING,
        closing=OPENING - DEBIT,
        check={
            "opening_source": "manual",
            "opening_paise": OPENING,
            "closing_paise": OPENING - DEBIT,
        },
        review={"manual_opening_paise": OPENING},
    )


async def _expense(conn, account: dict) -> dict:
    return await conn.fetchrow(
        """
        SELECT * FROM movements
        WHERE kind = 'expense'
          AND EXISTS (
              SELECT 1 FROM movement_legs ml
              WHERE ml.movement_id = movements.id
                AND ml.from_account_id = $1
          )
        """,
        account["id"],
    )


async def _expense_payee(conn, movement_id: str) -> str | None:
    return await conn.fetchval(
        """
        SELECT p.display_name FROM movement_legs tl
        JOIN payees p ON p.id = tl.payee_id
        WHERE tl.movement_id = $1
        """,
        movement_id,
    )


@pytest.mark.parametrize("counterparty", ["N/A", "Unknown", "unknown shop", "someone"])
async def test_a_placeholder_counterparty_creates_no_payee(ledger, counterparty):
    account = await _bank(ledger)
    import_id = await _review(ledger, account, counterparty=counterparty)
    detail = await import_payload(ledger, import_id)
    assert detail["lines"][0]["counterparty"] == counterparty

    result = await commit_import(ledger, import_id)

    assert result["rows"] == 1
    assert (
        await ledger.fetchval("SELECT count(*) FROM movement_legs WHERE payee_id IS NOT NULL") == 0
    )
    assert await ledger.fetchval("SELECT count(*) FROM captures WHERE kind = 'statement_line'") == 1
    expense = await _expense(ledger, account)
    assert await _expense_payee(ledger, str(expense["id"])) is None


async def test_a_real_counterparty_still_becomes_the_payee(ledger):
    account = await _bank(ledger)
    import_id = await _review(ledger, account, counterparty="Bookworm Publishers")

    await commit_import(ledger, import_id)

    expense = await _expense(ledger, account)
    assert await _expense_payee(ledger, str(expense["id"])) == "Bookworm Publishers"


async def test_a_split_share_owed_by_a_placeholder_person_is_refused(demo_ledger):
    from arno.tools.write import ToolInputError, record_movement

    with pytest.raises(ToolInputError, match="isn't a person"):
        await record_movement(
            demo_ledger,
            {
                "kind": "split",
                "total_rupees": 900,
                "my_share_rupees": 300,
                "description": "Dinner",
                "people": [
                    {"name": "someone", "amount_rupees": 300},
                    {"name": "Asha", "amount_rupees": 300},
                ],
            },
            message_id=None,
            conversation_id="cnv_split_placeholder",
        )
    created = await demo_ledger.fetchval(
        "SELECT count(*) FROM payees WHERE lower(display_name) = 'someone'"
    )
    assert created == 0
