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
from typing import cast

from arno.db.conn import connect
from arno.importers import categorize as categorize_mod
from arno.importers import rules as rules_mod
from arno.importers.commit import commit_import, undo_import
from arno.importers.periods import ledger_opening as opening_balance
from arno.importers.reconcile import insert_lines, reconcile
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from arno.ledger.common import ledger_balance
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
            "SELECT coalesce(sum(CASE WHEN to_account_id = $1 THEN amount_paise "
            "ELSE -amount_paise END), 0) FROM movement_legs "
            "WHERE to_account_id = $1 OR from_account_id = $1",
            account["id"],
        )
        == 100_000 + booked
    )
    assert await check_ledger(ledger) == []


# The save takes the import's row lock first, in the same order as the undo ------------------


LABEL = "Shopping"


async def _save(conn, import_id: str, key: str) -> None:
    """The categorizer's save, given one high answer, so it writes without a model."""
    category_id = str(await conn.fetchval("SELECT id FROM categories WHERE name = $1", LABEL))
    await categorize_mod._save(
        conn,
        import_id,
        [categorize_mod.Answer(key=key, category=LABEL, confidence="high")],
        {LABEL.lower(): (category_id, LABEL)},
    )


async def _groups(conn, import_id: str) -> list[str]:
    return list(
        await conn.fetchval("SELECT categorize_groups FROM imports WHERE id = $1", import_id)
    )


async def _labeled(conn, movement_id: str) -> int:
    return cast(
        int,
        await conn.fetchval(
            "SELECT count(*) FROM movement_legs WHERE movement_id = $1 AND category_id IS NOT NULL",
            movement_id,
        ),
    )


async def _backend_pid(conn) -> int:
    """The backend a connection is on, for watching whether it waits on a lock."""
    return cast(int, await conn.fetchval("SELECT pg_backend_pid()"))


async def _waiting_on_a_lock(conn, pid: int, *, timeout: float = 10.0) -> bool:
    """Whether backend `pid` is blocked on a lock, checked until `timeout` runs out."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = await conn.fetchrow(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = $1", pid
        )
        if row is not None and row["wait_event_type"] == "Lock":
            return True
        await asyncio.sleep(0.02)
    return False


async def _committed_expense(ledger) -> tuple[str, str, str]:
    account = await _account(ledger)
    import_id = await _review(
        ledger,
        account,
        rows=[
            {
                "date": "2026-04-01",
                "description": "UPI/DR/1/ALPHA STORE",
                "debit": 20000,
                "credit": 0,
                "balance": None,
                "counterparty": "Alpha Store",
            }
        ],
    )
    await commit_import(ledger, import_id)
    movement_id = str(
        await ledger.fetchval(
            "SELECT movement_id FROM statement_lines WHERE import_id = $1", import_id
        )
    )
    return import_id, movement_id, str(account["id"])


async def test_the_categorizer_waits_for_the_import_lock_instead_of_deadlocking_the_undo(
    ledger,
):
    """The undo locks `imports` first and labels later; the save must do the same or deadlock."""
    import_id, movement_id, _ledger_account = await _committed_expense(ledger)
    dsn = os.environ["DATABASE_URL"]

    async with connect(dsn) as undo_conn, connect(dsn) as save_conn, connect(dsn) as observer:
        save_pid = await _backend_pid(save_conn)
        async with undo_conn.transaction():
            # The undo's own first statement, which orders every write below it.
            await undo_conn.fetchrow("SELECT * FROM imports WHERE id = $1 FOR UPDATE", import_id)
            save = asyncio.create_task(_save(save_conn, import_id, "alpha store"))
            # The save has done its reads and holds whatever it takes before `imports`.
            assert await _waiting_on_a_lock(observer, save_pid)
            # The undo's next lock: the movement the commit wrote, taken by `reverse_movement`.
            # Before the fix the save held this row and wanted `imports`, so this deadlocked.
            await undo_conn.fetchrow(
                "SELECT * FROM movements WHERE id = $1 FOR UPDATE", movement_id
            )
        await save

    assert await _labeled(ledger, movement_id) == 1


async def test_the_rules_engine_also_takes_the_import_lock_before_labelling(ledger, monkeypatch):
    """The job's first write is the rules phase, so it needs the same order as the undo."""
    import_id, movement_id, _ledger_account = await _committed_expense(ledger)
    category_id = str(await ledger.fetchval("SELECT id FROM categories WHERE name = $1", LABEL))
    await ledger.execute(
        """
        INSERT INTO category_rules (match, pattern, category_id, source)
        VALUES ('contains', 'alpha store', $1, 'user')
        """,
        category_id,
    )
    dsn = os.environ["DATABASE_URL"]

    async with connect(dsn) as undo_conn, connect(dsn) as rules_conn, connect(dsn) as observer:
        rules_pid = await _backend_pid(rules_conn)
        async with undo_conn.transaction():
            await undo_conn.fetchrow("SELECT * FROM imports WHERE id = $1 FOR UPDATE", import_id)
            apply = asyncio.create_task(rules_mod.apply_rules(rules_conn, import_id))
            assert await _waiting_on_a_lock(observer, rules_pid)
            await undo_conn.fetchrow(
                "SELECT * FROM movements WHERE id = $1 FOR UPDATE", movement_id
            )
        result = await apply

    assert result == {"labeled": 1, "rows": 1}


async def test_an_undo_arriving_during_the_save_takes_the_labels_it_caused_with_it(
    ledger, monkeypatch
):
    """The save commits while the undo waits; the undo then sees and reverts its group."""
    import_id, movement_id, ledger_account = await _committed_expense(ledger)
    dsn = os.environ["DATABASE_URL"]
    recorded = asyncio.Event()
    release = asyncio.Event()
    original = rules_mod.record_categorize_group

    async def gated(conn, *, import_id: str, group_id: str) -> None:
        recorded.set()
        await release.wait()
        await original(conn, import_id=import_id, group_id=group_id)

    monkeypatch.setattr(rules_mod, "record_categorize_group", gated)

    async with connect(dsn) as save_conn, connect(dsn) as undo_conn, connect(dsn) as observer:
        undo_pid = await _backend_pid(undo_conn)
        save = asyncio.create_task(_save(save_conn, import_id, "alpha store"))
        await asyncio.wait_for(recorded.wait(), timeout=10)
        undo = asyncio.create_task(undo_import(undo_conn, import_id))
        assert await _waiting_on_a_lock(observer, undo_pid)
        release.set()
        done = await undo
        await save

    assert done["status"] == "undone"
    # The group the save recorded while the undo waited is undone with the import.
    groups = await _groups(ledger, import_id)
    assert groups
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM undo_log WHERE id = ANY($1) AND undone_at IS NULL",
            groups,
        )
        == 0
    )
    assert await ledger.fetchval("SELECT count(*) FROM category_rules WHERE source = 'llm'") == 0
    assert await _labeled(ledger, movement_id) == 0
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == (
        "rejected"
    )
    assert await ledger_balance(ledger, ledger_account) == 100000
    assert await check_ledger(ledger) == []


async def test_a_save_arriving_after_the_undo_writes_nothing_and_does_not_fail(ledger, monkeypatch):
    """The undo deleted the rows first; the waiting save finds nothing left to label."""
    import_id, movement_id, _ledger_account = await _committed_expense(ledger)
    dsn = os.environ["DATABASE_URL"]

    async with connect(dsn) as undo_conn, connect(dsn) as save_conn, connect(dsn) as observer:
        save_pid = await _backend_pid(save_conn)
        async with undo_conn.transaction():
            await undo_conn.fetchrow("SELECT * FROM imports WHERE id = $1 FOR UPDATE", import_id)
            save = asyncio.create_task(_save(save_conn, import_id, "alpha store"))
            assert await _waiting_on_a_lock(observer, save_pid)
            done = await undo_import(undo_conn, import_id)
        await save

    assert done["status"] == "undone"
    assert await ledger.fetchval("SELECT count(*) FROM category_rules WHERE source = 'llm'") == 0
    assert await _labeled(ledger, movement_id) == 0
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == (
        "rejected"
    )
    assert await check_ledger(ledger) == []
