"""Every write card that can be undone carries the audit id the undo button needs."""

from __future__ import annotations

from fin_agent.tools import write as write_tools


async def _expense(conn, *, description="Card undo lunch"):
    return await write_tools.record_movement(
        conn,
        {
            "kind": "expense",
            "amount_rupees": 450,
            "payee": "Swiggy",
            "account": "SBI Salary",
            "category": "Food & Dining",
            "description": description,
        },
        message_id=None,
        conversation_id="cnv_test",
        not_duplicate=True,
    )


async def test_a_movement_card_carries_the_result_undo_id(demo_ledger):
    result = await _expense(demo_ledger)

    assert result["status"] == "recorded"
    assert result["undo_id"]
    assert result["card"]["undo_id"] == result["undo_id"]


async def test_a_setup_card_carries_the_result_undo_id_and_a_no_op_has_none(demo_ledger):
    added = await write_tools.update_setup_item(
        demo_ledger,
        entity="account",
        action="add",
        data={"name": "Card Undo Bank", "kind": "bank"},
        message_id=None,
    )
    assert added["card"]["undo_id"] == added["undo_id"]
    assert added["card"]["undo_id"]

    unchanged = await write_tools.update_setup_item(
        demo_ledger,
        entity="account",
        action="update",
        data={"name": "Card Undo Bank"},
        message_id=None,
    )
    assert unchanged["undo_id"] is None
    assert unchanged["card"].get("undo_id") is None


async def test_a_preference_card_carries_its_undo_id_and_a_repeat_has_none(demo_ledger):
    created = await write_tools.preference(
        demo_ledger,
        action="remember",
        text="Use UPI first",
        preference_id=None,
        message_id=None,
    )
    assert created["card"]["undo_id"] == created["undo_id"]
    assert created["card"]["undo_id"]

    repeated = await write_tools.preference(
        demo_ledger,
        action="remember",
        text="Use UPI first",
        preference_id=None,
        message_id=None,
    )
    assert repeated["undo_id"] is None
    assert repeated["card"].get("undo_id") is None


async def test_a_correction_card_carries_its_undo_id_but_a_no_op_does_not(demo_ledger):
    movement = await _expense(demo_ledger)

    corrected = await write_tools.correct_movement(
        demo_ledger,
        movement_id=movement["movement_id"],
        changes={"category": "Groceries"},
        message_id=None,
    )
    assert corrected["card"]["undo_id"] == corrected["undo_id"]
    assert corrected["card"]["undo_id"]

    unchanged = await write_tools.correct_movement(
        demo_ledger,
        movement_id=movement["movement_id"],
        changes={},
        message_id=None,
    )
    assert unchanged["card"].get("undo_id") is None
