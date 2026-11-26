"""Ledger setup and context facts: the values the read and write paths must state in code.

A person's balance is known by construction like a goal's, a payee that names nobody is never
created, the dates the agent quotes carry their weekday, the context lists open imports, and a
card stated "in credit" is one write with the right sign.
"""

from __future__ import annotations

import pytest

from arno.ledger.accounts import create_account, get_account
from arno.tools import write as write_tools


def _context_account(context: dict, account_id: str) -> dict:
    return next(item for item in context["accounts"] if item["id"] == account_id)


async def _bank(conn, name: str = "Audit bank", opening: int = 10_000_000) -> dict:
    return await create_account(conn, kind="bank", name=name, opening_balance_paise=opening)


# 1. A person's balance is the ledger's own receivable -------------------------------------------


# 2. A payee that names nobody is never created --------------------------------------------------


PLACEHOLDERS = (
    "Unknown",
    "Unknown shop",
    "unknown shop",
    "shop",
    "Store",
    "merchant",
    "misc",
    "N/A",
    "someone",
    "Groceries",
)


@pytest.mark.parametrize("name", PLACEHOLDERS)
async def test_a_placeholder_payee_is_not_created(ledger, name):
    bank = await _bank(ledger)

    result = await write_tools.record_movement(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": 1500,
            "payee": name,
            "category": "Groceries",
            "account": bank["name"],
        },
        message_id=None,
        conversation_id=None,
    )

    assert result["status"] == "recorded"
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM payees WHERE lower(display_name) = lower($1)", name
        )
        == 0
    )
    assert (
        await ledger.fetchval("SELECT count(*) FROM movement_legs WHERE payee_id IS NOT NULL") == 0
    )
    assert result["transfers"][0]["payee_name"] is None


async def test_a_real_merchant_name_is_still_created(ledger):
    bank = await _bank(ledger)

    result = await write_tools.record_movement(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": 250,
            "payee": "Blue Tokai",
            "account": bank["name"],
        },
        message_id=None,
        conversation_id=None,
    )

    assert result["transfers"][0]["payee_name"] == "Blue Tokai"
    assert (
        await ledger.fetchval("SELECT count(*) FROM payees WHERE display_name = 'Blue Tokai'") == 1
    )


async def test_a_settlement_names_a_person_or_asks(ledger):
    bank = await _bank(ledger)

    with pytest.raises(write_tools.ToolInputError, match="person"):
        await write_tools.record_movement(
            ledger,
            {
                "kind": "settlement",
                "amount_rupees": 200,
                "person": "someone",
                "account": bank["name"],
            },
            message_id=None,
            conversation_id=None,
        )

    assert await ledger.fetchval("SELECT count(*) FROM payees") == 0


async def test_a_correction_refuses_a_placeholder_payee(ledger):
    bank = await _bank(ledger)
    logged = await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 250, "payee": "Blue Tokai", "account": bank["name"]},
        message_id=None,
        conversation_id=None,
    )

    with pytest.raises(write_tools.ToolInputError, match="not a payee"):
        await write_tools.correct_movement(
            ledger,
            movement_id=logged["movement_id"],
            changes={"payee": "Unknown shop"},
            message_id=None,
        )

    assert await ledger.fetchval("SELECT count(*) FROM payees") == 1
    labels = await ledger.fetch(
        "SELECT p.display_name FROM movement_legs tl JOIN payees p ON p.id = tl.payee_id"
    )
    assert [row["display_name"] for row in labels] == ["Blue Tokai"]


# 3. The weekday a date falls on comes from the tool, never from the model -----------------------


# 4. The context names every open import, so the agent can read before it says -------------------


# 5. A card stated "in credit" is one write with the sign the ledger means -----------------------


async def _card(conn) -> dict:
    return await create_account(
        conn,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
        opening_balance_paise=0,
    )


async def test_a_negative_card_balance_is_a_credit_in_one_write(ledger):
    card = await _card(ledger)

    result = await write_tools.update_setup_item(
        ledger,
        entity="balance",
        action="update",
        data={"account": "Slice card", "balance_rupees": -500},
        message_id=None,
    )

    assert result["summary"] == "Set Slice card balance to ₹500 in credit"
    refreshed = await get_account(ledger, card["id"])
    assert refreshed["balance_paise"] == 50_000
    assert refreshed["card_outstanding_paise"] == 0
    assert refreshed["available_credit_paise"] == 10_050_000
    # One stated balance is one visible adjustment, never a flip-flop of writes.
    assert await ledger.fetchval("SELECT count(*) FROM movements") == 1
    assert await ledger.fetchval("SELECT count(*) FROM undo_log WHERE id = $1", result["undo_id"])


async def test_a_positive_card_balance_is_still_what_is_owed(ledger):
    card = await _card(ledger)

    result = await write_tools.update_setup_item(
        ledger,
        entity="balance",
        action="update",
        data={"account": "Slice card", "balance_rupees": 5000},
        message_id=None,
    )

    assert result["summary"] == "Set Slice card balance to ₹5,000 due"
    refreshed = await get_account(ledger, card["id"])
    assert refreshed["balance_paise"] == -500_000
    assert refreshed["card_outstanding_paise"] == 500_000
    assert refreshed["available_credit_paise"] == 9_500_000


async def test_a_loan_balance_cannot_be_a_credit(ledger):
    loan = await create_account(
        ledger, kind="loan", name="Home loan", opening_balance_paise=-1_000_000
    )

    with pytest.raises(write_tools.ToolInputError, match="credit"):
        await write_tools.update_setup_item(
            ledger,
            entity="balance",
            action="update",
            data={"account": "Home loan", "balance_rupees": -500},
            message_id=None,
        )

    assert (await get_account(ledger, loan["id"]))["balance_paise"] == -1_000_000


async def test_a_card_added_in_credit_uses_the_same_sign(ledger):
    result = await write_tools.update_setup_item(
        ledger,
        entity="account",
        action="add",
        data={
            "name": "Overpaid card",
            "kind": "card",
            "credit_limit_rupees": 100_000,
            "statement_day": 5,
            "due_day": 25,
            "opening_balance_rupees": -500,
        },
        message_id=None,
    )

    added = await get_account(ledger, result["result"]["id"])
    assert added["balance_paise"] == 50_000
    assert added["card_outstanding_paise"] == 0
    assert added["available_credit_paise"] == 10_050_000
