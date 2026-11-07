from __future__ import annotations

from datetime import date, timedelta

from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import undo_group
from fin_agent.ledger.movements import correct_movement, list_movements, record_expense
from fin_agent.ledger.payees import create_payee


async def _bank(conn, name: str = "Pagination bank"):
    return await create_account(conn, kind="bank", name=name, opening_balance_paise=0)


async def _category(conn, name: str = "Food & Dining") -> str:
    return (await conn.fetchrow("SELECT id FROM categories WHERE name = $1", name))["id"]


async def _expense(
    conn,
    bank,
    category_id,
    *,
    description: str,
    when: date,
    payee_id: str | None = None,
):
    return await record_expense(
        conn,
        account_id=bank["id"],
        amount_paise=10_000,
        description=description,
        category_id=category_id,
        payee_id=payee_id,
        date=when,
    )


async def _walk(conn, *, limit: int, **filters):
    seen: list[str] = []
    totals: list[int] = []
    cursor = None
    while True:
        page = await list_movements(conn, limit=limit, cursor=cursor, **filters)
        totals.append(page["total_count"])
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return seen, totals, page
        assert page["items"], "a page with a cursor must not be empty"


async def test_cursor_pages_cover_every_visible_row_and_stop_on_the_last(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger)
    created = [
        await _expense(
            ledger,
            bank,
            food,
            description=f"Lunch {index}",
            when=date(2026, 10, 1) + timedelta(days=index),
        )
        for index in range(5)
    ]
    expected = [row["id"] for row in reversed(created)]
    assert [row["id"] for row in (await list_movements(ledger, limit=200))["items"]] == expected

    first = await list_movements(ledger, limit=2)
    assert len(first["items"]) == 2
    assert first["total_count"] == 5
    assert first["next_cursor"]

    second = await list_movements(ledger, limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 2
    assert second["total_count"] == 5
    assert second["next_cursor"]

    last = await list_movements(ledger, limit=2, cursor=second["next_cursor"])
    assert [item["id"] for item in last["items"]] == expected[-1:]
    assert last["total_count"] == 5
    assert last["next_cursor"] is None

    exact = await list_movements(ledger, limit=5)
    assert len(exact["items"]) == 5
    assert exact["next_cursor"] is None

    empty = await list_movements(ledger, kind="income")
    assert empty["items"] == []
    assert empty["total_count"] == 0
    assert empty["next_cursor"] is None


async def test_many_hidden_reversals_do_not_shorten_or_strand_a_page(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger)
    visible = [
        await _expense(
            ledger,
            bank,
            food,
            description=f"Kept {index}",
            when=date(2026, 9, 1) + timedelta(days=index),
        )
        for index in range(12)
    ]
    hidden: list[str] = []
    older = await _expense(ledger, bank, food, description="Deleted older", when=date(2026, 8, 1))
    await correct_movement(ledger, older["id"], changes={"delete": True})
    hidden.append(older["id"])
    for index in range(12):
        movement = await _expense(
            ledger,
            bank,
            food,
            description=f"Deleted {index}",
            when=date(2026, 10, 1) + timedelta(days=index),
        )
        await correct_movement(ledger, movement["id"], changes={"delete": True})
        hidden.append(movement["id"])
    visible_ids = {movement["id"] for movement in visible}

    page = await list_movements(ledger, limit=5)
    assert len(page["items"]) == 5
    assert page["total_count"] == 12
    assert page["next_cursor"]
    assert {item["id"] for item in page["items"]} <= visible_ids

    seen, totals, last = await _walk(ledger, limit=5)
    assert seen == [movement["id"] for movement in reversed(visible)]
    assert totals == [12, 12, 12]
    assert last["items"] and last["next_cursor"] is None
    assert set(seen).isdisjoint(hidden)

    everything = await list_movements(ledger, limit=200, include_reversed=True)
    assert everything["total_count"] == len(everything["items"]) == 2 * len(hidden) + len(visible)
    assert set(hidden) <= {item["id"] for item in everything["items"]}
    assert all(
        item["kind"] != "reversal" for item in (await list_movements(ledger, limit=200))["items"]
    )


async def test_undo_of_undo_is_visible_again_with_parity_and_counts(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger)
    movement = await _expense(ledger, bank, food, description="Chai", when=date(2026, 10, 1))

    first = await undo_group(ledger, movement["undo_id"])
    hidden = await list_movements(ledger, kind="expense")
    assert hidden["items"] == []
    assert hidden["total_count"] == 0
    assert hidden["next_cursor"] is None

    await undo_group(ledger, first["undo_undo_id"])
    page = await list_movements(ledger, kind="expense")
    assert [item["id"] for item in page["items"]] == [movement["id"]]
    assert page["total_count"] == 1
    assert page["next_cursor"] is None

    everything = await list_movements(ledger, limit=2, include_reversed=True)
    assert everything["total_count"] == 3
    assert len(everything["items"]) == 2
    assert everything["next_cursor"]
    rest = await list_movements(
        ledger, limit=2, cursor=everything["next_cursor"], include_reversed=True
    )
    assert rest["total_count"] == 3
    assert rest["next_cursor"] is None

    rows = everything["items"] + rest["items"]
    by_id = {row["id"]: row for row in rows}
    assert by_id[movement["id"]]["reversed"] is False
    reversal = next(row for row in rows if row["reverses_id"] == movement["id"])
    assert reversal["reversed"] is True
    assert by_id[reversal["reversed_by"]]["reverses_id"] == reversal["id"]


async def test_same_day_rows_page_by_id_descending_without_duplicates(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger)
    same_day = date(2026, 10, 15)
    for index in range(4):
        await _expense(ledger, bank, food, description=f"Same day {index}", when=same_day)
    await _expense(ledger, bank, food, description="Older", when=same_day - timedelta(days=1))
    await _expense(ledger, bank, food, description="Newer", when=same_day + timedelta(days=1))

    expected = [
        row["id"]
        for row in await ledger.fetch(
            """
            SELECT id FROM movements
            WHERE kind = 'expense'
            ORDER BY date DESC, id DESC
            """
        )
    ]

    seen, totals, last = await _walk(ledger, limit=2)
    assert seen == expected
    assert totals == [6, 6, 6]
    assert last["next_cursor"] is None


async def test_filters_narrow_the_page_and_the_total_across_all_pages(ledger):
    first_bank = await _bank(ledger, name="First bank")
    second_bank = await _bank(ledger, name="Second bank")
    food = await _category(ledger, "Food & Dining")
    groceries = await _category(ledger, "Groceries")
    payee = await create_payee(ledger, display_name="Corner cafe", kind="organization")
    for index in range(3):
        await _expense(
            ledger,
            first_bank,
            food,
            description=f"Cafe {index}",
            when=date(2026, 10, 1),
            payee_id=payee["id"],
        )
    for index in range(2):
        await _expense(
            ledger,
            second_bank,
            groceries,
            description=f"Store {index}",
            when=date(2026, 10, 5),
        )

    by_account = await list_movements(ledger, account_id=first_bank["id"], limit=2)
    assert len(by_account["items"]) == 2
    assert by_account["total_count"] == 3
    assert by_account["next_cursor"]
    rest = await list_movements(
        ledger, account_id=first_bank["id"], limit=2, cursor=by_account["next_cursor"]
    )
    assert len(rest["items"]) == 1
    assert rest["total_count"] == 3
    assert rest["next_cursor"] is None

    assert (await list_movements(ledger, category_id=food, limit=200))["total_count"] == 3
    assert (await list_movements(ledger, payee_id=payee["id"], limit=200))["total_count"] == 3
    assert (
        await list_movements(
            ledger,
            kind="expense",
            start=date(2026, 10, 5),
            end=date(2026, 10, 5),
            limit=200,
        )
    )["total_count"] == 2
    assert (await list_movements(ledger, start=date(2026, 10, 6), limit=200))["total_count"] == 0
