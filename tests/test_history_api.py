"""The signed-in user can inspect the write log and the undo result each group would get."""

from __future__ import annotations

from datetime import UTC, datetime

from arno.db.conn import Connection
from arno.ledger.accounts import create_account
from arno.ledger.audit import undo_group, write_group
from arno.ledger.movements import record_expense
from arno.ledger.payees import create_payee
from arno.ledger.settings import update_settings
from arno.tools.write import delete_preference_for_good


async def _clear_history(conn: Connection) -> None:
    await conn.execute("DELETE FROM audit_log")


async def _remember(
    conn: Connection,
    body: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> tuple[str, str]:
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        row = await conn.fetchrow_required(
            "INSERT INTO preferences (body, created_by) VALUES ($1, $2) RETURNING *",
            body,
            "agent" if actor == "agent" else "user",
        )
        group.record_created("preferences", dict(row))
    return group.group_id, str(row["id"])


async def _edit(conn: Connection, preference_id: str, body: str) -> str:
    async with write_group(conn, actor="user") as group:
        before = await conn.fetchrow_required(
            "SELECT * FROM preferences WHERE id = $1", preference_id
        )
        after = await conn.fetchrow_required(
            "UPDATE preferences SET body = $2 WHERE id = $1 RETURNING *", preference_id, body
        )
        group.record_updated("preferences", preference_id, dict(before), dict(after))
    return group.group_id


async def _stamp(conn: Connection, group_id: str, day: int) -> None:
    await conn.execute(
        "UPDATE audit_log SET created_at = $2 WHERE group_id = $1",
        group_id,
        datetime(2026, 1, day, 9, tzinfo=UTC),
    )


def _entry(page: dict, undo_id: str) -> dict:
    return next(item for item in page["items"] if item["undo_id"] == undo_id)


async def test_history_requires_a_signed_in_session(client):
    assert client.get("/api/history").status_code == 401


async def test_history_lists_write_groups_newest_first_and_walks_them_with_a_cursor(
    authed_client, db
):
    await _clear_history(db)
    first, pref = await _remember(db, "First")
    second = await _edit(db, pref, "Second")
    third = await _edit(db, pref, "Third")
    await _stamp(db, first, 1)
    await _stamp(db, second, 2)
    await _stamp(db, third, 3)

    page = authed_client.get("/api/history?limit=2").json()
    assert [item["undo_id"] for item in page["items"]] == [third, second]
    assert page["next_cursor"]

    rest = authed_client.get("/api/history?limit=2", params={"cursor": page["next_cursor"]})
    assert rest.status_code == 200, rest.text
    assert [item["undo_id"] for item in rest.json()["items"]] == [first]
    assert rest.json()["next_cursor"] is None

    newest = page["items"][0]
    assert newest["created_at"].startswith("2026-01-03")
    assert newest["actor"] == "user"
    assert newest["message_id"] is None
    assert newest["conversation_id"] is None
    assert newest["summary"] == "Changed memory: Second → Third"
    assert newest["records"] == [{"table": "preferences", "id": pref}]
    assert newest["undoable"] is True
    assert newest["reason"] is None


async def test_a_later_edit_blocks_a_history_undo_with_the_same_reason_the_undo_returns(
    authed_client, db
):
    await _clear_history(db)
    remembered, pref = await _remember(db, "Before")
    edited = await _edit(db, pref, "After")
    await _stamp(db, remembered, 1)
    await _stamp(db, edited, 2)

    page = authed_client.get("/api/history").json()
    blocked = _entry(page, remembered)
    assert blocked["undoable"] is False
    assert "later write" in blocked["reason"]
    assert "Undo that change first" in blocked["reason"]
    assert _entry(page, edited)["undoable"] is True

    undo = authed_client.post(f"/api/undo/{remembered}")
    assert undo.status_code == 409
    assert blocked["reason"] == undo.json()["detail"]
    assert await db.fetchval("SELECT body FROM preferences WHERE id = $1", pref) == "After"


async def test_history_shows_a_group_as_already_undone_after_the_api_undo(authed_client, db):
    await _clear_history(db)
    remembered, pref = await _remember(db, "Before")
    edited = await _edit(db, pref, "After")

    undo = authed_client.post(f"/api/undo/{edited}")
    assert undo.status_code == 200, undo.text
    assert undo.json()["status"] == "undone"
    assert await db.fetchval("SELECT body FROM preferences WHERE id = $1", pref) == "Before"

    page = authed_client.get("/api/history").json()
    assert page["items"][0]["undo_id"] == undo.json()["undo_undo_id"]
    entry = _entry(page, edited)
    assert entry["undoable"] is False
    assert entry["reason"] == "That write was already undone."
    again = authed_client.post(f"/api/undo/{edited}")
    assert again.json()["summary"] == entry["reason"]
    # The undo itself is now the later write on that row, and history says so.
    restored = _entry(page, remembered)
    assert restored["undoable"] is False
    assert "later write" in restored["reason"]


async def test_history_names_the_agent_and_the_conversation_that_recorded_a_write(
    authed_client, db
):
    await _clear_history(db)
    await db.execute("INSERT INTO conversations (id) VALUES ('cnv_history')")
    await db.execute(
        """
        INSERT INTO messages (id, conversation_id, client_id, role, content)
        VALUES ('msg_history', 'cnv_history', 'client_history', 'assistant', '[]'::jsonb)
        """
    )
    group_id, _ = await _remember(db, "Agent note", actor="agent", message_id="msg_history")

    entry = _entry(authed_client.get("/api/history").json(), group_id)
    assert entry["actor"] == "agent"
    assert entry["message_id"] == "msg_history"
    assert entry["conversation_id"] == "cnv_history"


async def _expense(db: Connection) -> dict:
    account = await create_account(db, kind="bank", name="SBI Salary")
    payee = await create_payee(db, display_name="Swiggy")
    return await record_expense(
        db,
        account_id=account["id"],
        amount_paise=45_000,
        description="Lunch",
        payee_id=payee["id"],
    )


async def test_history_says_which_account_a_group_added(authed_client, db):
    await _clear_history(db)
    account = await create_account(db, kind="bank", name="HDFC Savings")

    entry = _entry(authed_client.get("/api/history").json(), account["undo_id"])
    assert entry["summary"] == "Added account HDFC Savings"
    assert entry["records"] == [{"table": "accounts", "id": account["id"]}]
    assert entry["undoable"] is True


async def test_history_reads_an_expense_with_its_amount_and_payee(authed_client, db):
    await _clear_history(db)
    expense = await _expense(db)

    entry = _entry(authed_client.get("/api/history").json(), expense["undo_id"])
    assert entry["summary"] == "Logged expense ₹450.00 · Swiggy"
    assert entry["undoable"] is True
    assert {"table": "movements", "id": expense["id"]} in entry["records"]


async def test_history_reads_an_undo_as_the_change_it_reversed(authed_client, db):
    await _clear_history(db)
    expense = await _expense(db)

    undone = await undo_group(db, expense["undo_id"])

    entry = _entry(authed_client.get("/api/history").json(), undone["undo_undo_id"])
    assert entry["summary"] == "Undid: Logged expense ₹450.00 · Swiggy"
    assert entry["undoable"] is True


async def test_history_reads_a_memory_and_never_repeats_a_deleted_one(authed_client, db):
    await _clear_history(db)
    group_id, preference_id = await _remember(db, "prefers tea")
    assert _entry(authed_client.get("/api/history").json(), group_id)["summary"] == (
        "Remembered: prefers tea"
    )

    await delete_preference_for_good(db, preference_id)
    page = authed_client.get("/api/history").json()
    assert any(item["summary"] == "Deleted a memory" for item in page["items"])
    assert all("prefers tea" not in item["summary"] for item in page["items"])


async def test_history_reads_a_floor_change_with_both_amounts(authed_client, db):
    await _clear_history(db)
    await update_settings(db, floor_paise=1_000_000)
    changed = await update_settings(db, floor_paise=2_000_000)

    entry = _entry(authed_client.get("/api/history").json(), changed["undo_id"])
    assert entry["summary"] == "Changed floor ₹10,000.00 → ₹20,000.00"


async def test_history_names_a_committed_import_with_its_file_and_row_count(authed_client, db):
    await _clear_history(db)
    group_id, _ = await _remember(db, "import note")
    await db.execute(
        """
        INSERT INTO attachments (id, kind, filename, sha256, data)
        VALUES ('att_probe', 'statement', 'statement.csv', 'probe', $1)
        """,
        b"\x00",
    )
    await db.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, commit_groups)
        VALUES ('imp_probe', 'probe', 'committed', 'att_probe', $1)
        """,
        [group_id],
    )
    for index in range(2):
        await db.execute(
            """
            INSERT INTO statement_lines
                (id, import_id, date, description_raw, amount_paise, content_hash, match_state)
            VALUES ($1, 'imp_probe', '2026-11-01', 'Line', -10000, $2, 'merged')
            """,
            f"stl_probe{index}",
            f"hash{index}",
        )

    entry = _entry(authed_client.get("/api/history").json(), group_id)
    assert entry["summary"] == "Committed import of statement.csv (2 rows)"


async def test_history_falls_back_to_the_row_count_when_a_table_has_no_phrase(authed_client, db):
    await _clear_history(db)
    async with write_group(db, actor="agent") as group:
        row = await db.fetchrow_required(
            """
            INSERT INTO scheduled_questions (prompt, rule)
            VALUES ('Did I overspend?', 'FREQ=WEEKLY') RETURNING *
            """
        )
        group.record_created("scheduled_questions", dict(row))

    entry = _entry(authed_client.get("/api/history").json(), group.group_id)
    assert entry["summary"] == "Changed 1 row in scheduled_questions"
