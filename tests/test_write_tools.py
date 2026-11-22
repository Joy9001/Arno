"""Regression tests for the plain-Python write tools.

A refused or failing write must leave nothing behind: payees are resolved and created inside the
same audited transaction as the movement, and a duplicate check runs before any write. Undo keeps
its CommitOutcomeUnknownError contract and its conversation scope.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from arno.ledger.accounts import create_account, get_account
from arno.ledger.common import today
from arno.ledger.errors import CommitOutcomeUnknownError
from arno.tools import write as write_tools


async def _bank(conn, name: str = "Tool bank", opening: int = 10_000_000) -> dict:
    return await create_account(conn, kind="bank", name=name, opening_balance_paise=opening)


async def _counts(conn) -> tuple[int, int, int]:
    return (
        await conn.fetchval("SELECT count(*) FROM payees"),
        await conn.fetchval("SELECT count(*) FROM movements"),
        await conn.fetchval("SELECT count(*) FROM audit_log"),
    )


async def _message(conn, suffix: str) -> str:
    conversation_id = f"cnv_write_{suffix}"
    await conn.execute(
        "INSERT INTO conversations (id, title) VALUES ($1, 'write tools') "
        "ON CONFLICT (id) DO NOTHING",
        conversation_id,
    )
    row = await conn.fetchrow(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES ($1, $2, 'user', '{}'::jsonb, 'audit') RETURNING id
        """,
        conversation_id,
        f"cli_write_{suffix}",
    )
    return str(row["id"])


# --- finding 3: a refusal writes nothing -------------------------------------------------------


async def test_a_refused_expense_creates_no_payee_or_audit_row(ledger):
    await _bank(ledger)
    before = await _counts(ledger)
    with pytest.raises(write_tools.ToolInputError):
        await write_tools.record_movement(
            ledger,
            {
                "kind": "expense",
                "amount_rupees": None,
                "payee": "Never created",
                "account": "Tool bank",
            },
            message_id=None,
            conversation_id=None,
            not_duplicate=True,
        )
    assert await _counts(ledger) == before


async def test_a_refused_split_rolls_back_every_payee_it_created(ledger):
    await _bank(ledger)
    before = await _counts(ledger)
    with pytest.raises(write_tools.ToolInputError, match="negative"):
        await write_tools.record_movement(
            ledger,
            {
                "kind": "split",
                "total_rupees": 100,
                "my_share_rupees": -1,
                "payee": "Swiggy",
                "people": [{"name": "Ravi", "amount_rupees": 50}],
            },
            message_id=None,
            conversation_id=None,
        )
    # Both the vendor and the person payee were resolved before the refusal; neither survives.
    assert await _counts(ledger) == before


async def test_a_split_payee_created_before_a_bad_person_is_rolled_back(ledger):
    await _bank(ledger)
    before = await _counts(ledger)
    with pytest.raises(write_tools.ToolInputError, match="person"):
        await write_tools.record_movement(
            ledger,
            {
                "kind": "split",
                "total_rupees": 100,
                "my_share_rupees": 50,
                "payee": "Swiggy",
                "people": [
                    {"name": "Ravi", "amount_rupees": 50},
                    {"name": "someone", "amount_rupees": 50},
                ],
            },
            message_id=None,
            conversation_id=None,
        )
    assert await _counts(ledger) == before


async def test_a_refused_correction_creates_no_payee(ledger):
    bank = await _bank(ledger)
    logged = await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 100, "payee": "Blue Tokai", "account": bank["name"]},
        message_id=None,
        conversation_id=None,
    )
    with pytest.raises(write_tools.ToolInputError, match="account"):
        await write_tools.correct_movement(
            ledger,
            movement_id=logged["movement_id"],
            changes={"payee": "Fresh vendor", "account": "No such account"},
            message_id=None,
        )
    assert (
        await ledger.fetchval("SELECT count(*) FROM payees WHERE display_name = 'Fresh vendor'")
        == 0
    )


async def test_a_duplicate_refusal_creates_no_payee(ledger):
    await _bank(ledger)
    movement = {"kind": "expense", "amount_rupees": 450, "payee": "Swiggy", "account": "Tool bank"}
    await write_tools.record_movement(
        ledger, dict(movement), message_id=None, conversation_id=None, not_duplicate=True
    )
    before = await _counts(ledger)
    # The payee now exists; the second call is a duplicate and must not write a new one.
    second = await write_tools.record_movement(
        ledger, dict(movement), message_id=None, conversation_id=None
    )
    assert second["status"] == "possible_duplicate"
    assert await _counts(ledger) == before


# --- finding 5: duplicates across capture modes, including capture-less -------------------------


@pytest.mark.parametrize("first_kind", ["chat", "photo", "voice", None])
async def test_a_duplicate_is_seen_across_capture_modes(ledger, first_kind):
    await _bank(ledger)
    movement = {"kind": "expense", "amount_rupees": 450, "payee": "Swiggy", "account": "Tool bank"}
    if first_kind is None:
        first = await write_tools.record_movement(
            ledger, dict(movement), message_id=None, conversation_id=None, not_duplicate=True
        )
    else:
        first = await write_tools.record_movement(
            ledger,
            dict(movement),
            message_id=None,
            conversation_id=None,
            not_duplicate=True,
            capture_kind=first_kind,
            capture_ref=f"ref-{first_kind}",
        )
    assert first["status"] == "recorded"

    second = await write_tools.record_movement(
        ledger, dict(movement), message_id=None, conversation_id=None
    )
    assert second["status"] == "possible_duplicate"
    assert second["existing"]["movement_id"] == first["movement_id"]


# --- finding 4: undo keeps its contract and stays in its conversation --------------------------


async def test_undo_does_not_translate_an_unknown_commit_outcome(ledger, monkeypatch):
    async def boom(*args, **kwargs):
        raise CommitOutcomeUnknownError()

    monkeypatch.setattr(write_tools, "undo_group", boom)
    with pytest.raises(CommitOutcomeUnknownError):
        await write_tools.undo(ledger, undo_id="und_any", conversation_id=None, message_id=None)


async def test_undo_does_not_swallow_a_connection_error(ledger, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("connection dropped in commit")

    monkeypatch.setattr(write_tools, "undo_group", boom)
    with pytest.raises(RuntimeError, match="connection dropped"):
        await write_tools.undo(ledger, undo_id="und_any", conversation_id=None, message_id=None)


async def test_undo_without_an_id_refuses_when_this_conversation_has_no_writes(ledger):
    await _bank(ledger)
    await ledger.execute(
        "INSERT INTO conversations (id, title) VALUES ('cnv_write_empty', 'empty') "
        "ON CONFLICT (id) DO NOTHING"
    )
    with pytest.raises(write_tools.ToolInputError, match="Nothing to undo"):
        await write_tools.undo(
            ledger, undo_id=None, conversation_id="cnv_write_empty", message_id=None
        )


async def test_undo_without_an_id_never_crosses_conversation_boundaries(ledger):
    first_bank = await _bank(ledger, "Conversation bank")
    second_bank = await _bank(ledger, "Other bank")
    first_message = await _message(ledger, "scoped-first")
    second_message = await _message(ledger, "scoped-second")

    first = await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 100, "account": first_bank["name"]},
        message_id=first_message,
        conversation_id="cnv_write_scoped-first",
    )
    second = await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 200, "account": second_bank["name"]},
        message_id=second_message,
        conversation_id="cnv_write_scoped-second",
    )
    assert first["status"] == second["status"] == "recorded"

    undone = await write_tools.undo(
        ledger, undo_id=None, conversation_id="cnv_write_scoped-first", message_id=None
    )
    assert undone["status"] == "undone"
    assert (await get_account(ledger, first_bank["id"]))["balance_paise"] == 10_000_000
    assert (await get_account(ledger, second_bank["id"]))["balance_paise"] == 10_000_000 - 20_000


# --- section 5: normal write paths that had no test --------------------------------------------


async def test_an_unknown_account_or_category_is_refused_without_a_write(ledger):
    await _bank(ledger)
    before = await _counts(ledger)
    with pytest.raises(write_tools.ToolInputError, match="No account"):
        await write_tools.record_movement(
            ledger,
            {"kind": "expense", "amount_rupees": 100, "account": "No such bank"},
            message_id=None,
            conversation_id=None,
        )
    with pytest.raises(write_tools.ToolInputError, match="No category"):
        await write_tools.record_movement(
            ledger,
            {
                "kind": "expense",
                "amount_rupees": 100,
                "account": "Tool bank",
                "category": "No such category",
            },
            message_id=None,
            conversation_id=None,
        )
    assert await _counts(ledger) == before


async def test_a_non_loan_account_is_refused_as_a_loan_payment(ledger):
    await _bank(ledger)
    before = await _counts(ledger)
    with pytest.raises(write_tools.ToolInputError, match="loan account"):
        await write_tools.record_movement(
            ledger,
            {"kind": "loan", "amount_rupees": 100, "loan": "Tool bank", "account": "Tool bank"},
            message_id=None,
            conversation_id=None,
        )
    assert await _counts(ledger) == before


async def test_an_overdrawn_bank_balance_is_kept_as_a_negative_paise_balance(ledger):
    bank = await _bank(ledger)
    result = await write_tools.update_setup_item(
        ledger,
        entity="balance",
        action="update",
        data={"account": bank["name"], "balance_rupees": -500},
        message_id=None,
    )
    assert result["status"] == "saved"
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == -50_000


async def test_a_category_can_be_added_renamed_archived_and_undone(ledger):
    added = await write_tools.update_setup_item(
        ledger, entity="category", action="add", data={"name": "Pets"}, message_id=None
    )
    category_id = added["result"]["id"]
    assert await ledger.fetchval("SELECT count(*) FROM categories WHERE id = $1", category_id) == 1

    renamed = await write_tools.update_setup_item(
        ledger,
        entity="category",
        action="update",
        data={"id": category_id, "new_name": "Pet care"},
        message_id=None,
    )
    assert renamed["result"]["name"] == "Pet care"
    await write_tools.undo(
        ledger, undo_id=renamed["undo_id"], conversation_id=None, message_id=None
    )
    assert (await ledger.fetchrow("SELECT name FROM categories WHERE id = $1", category_id))[
        "name"
    ] == "Pets"

    archived = await write_tools.update_setup_item(
        ledger, entity="category", action="archive", data={"id": category_id}, message_id=None
    )
    assert archived["result"]["active"] is False


async def test_a_schedule_payment_links_by_name_and_unlinks(ledger):
    from arno.ledger.schedules import create_schedule, get_occurrence

    bank = await _bank(ledger)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Tool rent",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=DAILY;COUNT=90",
        starts=today() - timedelta(days=30),
    )
    movement = await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 1000, "account": bank["name"]},
        message_id=None,
        conversation_id=None,
    )
    due = today() - timedelta(days=2)
    linked = await write_tools.schedule_payment(
        ledger,
        schedule="Tool rent",
        due_date=due,
        movement_id=movement["movement_id"],
        message_id=None,
    )
    assert linked["status"] == "linked"
    assert (await get_occurrence(ledger, schedule["id"], due))["payment_status"] == "paid"

    unlinked = await write_tools.schedule_payment(
        ledger,
        schedule="Tool rent",
        due_date=due,
        movement_id=movement["movement_id"],
        action="unlink",
        message_id=None,
    )
    assert unlinked["status"] == "unlinked"
    assert (await get_occurrence(ledger, schedule["id"], due))["payment_status"] == "unmatched"


async def test_a_scheduled_question_can_be_renamed(ledger):
    added = await write_tools.update_setup_item(
        ledger,
        entity="scheduled_question",
        action="add",
        data={"prompt": "Old question?", "rule": "FREQ=DAILY"},
        message_id=None,
    )
    row = await ledger.fetchrow("SELECT * FROM scheduled_questions WHERE prompt = 'Old question?'")
    updated = await write_tools.update_setup_item(
        ledger,
        entity="scheduled_question",
        action="update",
        data={"id": row["id"], "prompt": "New question?"},
        message_id=None,
    )
    assert updated["status"] == "saved"
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM scheduled_questions WHERE prompt = 'New question?'"
        )
        == 1
    )
    assert added["status"] == "saved"


async def test_reremembering_a_forgotten_memory_makes_one_new_active_row(ledger):
    first = await write_tools.preference(
        ledger, action="remember", text="Use UPI", preference_id=None, message_id=None
    )
    await write_tools.preference(
        ledger, action="forget", text=None, preference_id=first["id"], message_id=None
    )
    again = await write_tools.preference(
        ledger, action="remember", text="Use UPI", preference_id=None, message_id=None
    )
    assert again["id"] != first["id"]
    assert (
        await ledger.fetchval("SELECT count(*) FROM preferences WHERE active AND body = 'Use UPI'")
        == 1
    )

    await write_tools.undo(ledger, undo_id=again["undo_id"], conversation_id=None, message_id=None)
    assert (
        await ledger.fetchval("SELECT active FROM preferences WHERE id = $1", again["id"]) is False
    )
    assert (
        await ledger.fetchval("SELECT active FROM preferences WHERE id = $1", first["id"]) is False
    )


async def test_forgetting_an_already_forgotten_memory_keeps_one_inactive_row(ledger):
    remembered = await write_tools.preference(
        ledger, action="remember", text="Pay rent first", preference_id=None, message_id=None
    )
    await write_tools.preference(
        ledger, action="forget", text=None, preference_id=remembered["id"], message_id=None
    )
    again = await write_tools.preference(
        ledger, action="forget", text=None, preference_id=remembered["id"], message_id=None
    )
    assert again["status"] == "forgotten"
    assert (
        await ledger.fetchval("SELECT count(*) FROM preferences WHERE id = $1", remembered["id"])
        == 1
    )
    assert (
        await ledger.fetchval("SELECT active FROM preferences WHERE id = $1", remembered["id"])
        is False
    )
