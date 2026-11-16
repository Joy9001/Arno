"""A commit and an undo of two imports on one account never deadlock.

The undo ends by releasing the rows a review import hid as duplicates of the import being undone,
so it wrote that import's row after it had locked the account's money rows. A commit took its own
import row first and the account's rows later. An overlapping re-import committing while the older
import was undone then left each side holding a row the other wanted, and Postgres aborted one with
a deadlock. Both paths now take the account's row lock first and the import's row under it, so two
imports on one account wait for each other instead of forming a circle.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

from arno.db.conn import connect
from arno.importers.commit import commit_import, undo_import
from arno.importers.periods import ledger_opening as opening_balance
from arno.importers.reconcile import insert_lines, reconcile
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from tests.invariants import check_ledger

PERIOD = (date(2026, 4, 1), date(2026, 4, 30))
ROUNDS = 20
PAYEE = "Swiggy"


async def _account(conn) -> dict:
    created = await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=100000,
        opening_balance_as_of=date(2026, 3, 31),
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


def _row(day: date, amount_paise: int, *, merchant: str = PAYEE) -> dict:
    return {
        "date": day.isoformat(),
        "description": f"UPI/DR/{merchant.upper()} {amount_paise}",
        "debit": amount_paise,
        "credit": 0,
        "balance": None,
        "counterparty": merchant,
    }


async def _review(conn, account: dict, *, rows: list[dict]) -> str:
    """An import in `review` whose named rows reconcile against the ledger as it stands now."""
    previous = await opening_balance(conn, account["id"], PERIOD[0])
    opening = int(previous["paise"]) if previous else 0
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             check_result, check_status, review, commit_groups)
        VALUES ($1, $2, 'review', $3, $4, $5, $6, 'VERIFIED', $7, '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
        PERIOD[0],
        PERIOD[1],
        {"opening_source": "manual", "opening_paise": opening},
        {"manual_opening_paise": opening},
    )
    await insert_lines(conn, import_id=import_id, account=account, parsed_account={"rows": rows})
    await reconcile(conn, import_id=import_id, account=account, period=PERIOD)
    return import_id


async def test_a_commit_and_an_undo_of_two_imports_on_one_account_never_deadlock(ledger):
    account = await _account(ledger)
    day = PERIOD[0]
    undone = await _review(ledger, account, rows=[_row(day, 1_000)])
    await commit_import(ledger, undone)
    # The re-import repeats the older statement's row, which its own review hid as a duplicate:
    # undoing the older import releases it, so that undo and this commit write the same row. Its
    # other row names the same merchant, so the two also meet on that payee.
    duplicate = _row(day, 1_000)
    dsn = os.environ["DATABASE_URL"]

    for index in range(ROUNDS):
        day = PERIOD[0] + timedelta(days=index + 1)
        amount = 10_000 + index
        committed = await _review(ledger, account, rows=[duplicate, _row(day, amount)])
        async with connect(dsn) as commit_conn, connect(dsn) as undo_conn:
            # Neither side may abort with a deadlock; both must reach their own end.
            committed_result, undone_result = await asyncio.gather(
                commit_import(commit_conn, committed),
                undo_import(undo_conn, undone),
            )
        assert committed_result["status"] == "committed"
        assert undone_result["status"] == "undone"
        undone = committed
        duplicate = _row(day, amount)

    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", undone) == "committed"
    assert await ledger.fetchval("SELECT count(*) FROM imports WHERE status = 'committed'") == 1
    # The rows the one committed import booked are the only money left on the account.
    booked = await ledger.fetchval(
        "SELECT coalesce(sum(amount_paise), 0) FROM statement_lines "
        "WHERE import_id = $1 AND match_state = 'merged'",
        undone,
    )
    assert (
        await ledger.fetchval(
            "SELECT coalesce(sum(e.amount), 0) FROM pgledger_entries e "
            "JOIN accounts a ON a.ledger_account_id = e.account_id WHERE a.id = $1",
            account["id"],
        )
        == 100_000 + booked
    )
    assert await check_ledger(ledger) == []
