from __future__ import annotations

from datetime import date

import pytest

from fin_agent.ledger.accounts import create_account, get_account
from fin_agent.ledger.audit import undo_group
from fin_agent.ledger.categories import resolve_category, upsert_rule
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError
from fin_agent.ledger.movements import (
    correct_movement,
    get_movement,
    list_movements,
    record_expense,
    record_income,
    record_lending,
    record_loan_payment,
    record_refund,
    record_settlement,
    record_split,
    record_transfer,
)
from fin_agent.ledger.payees import create_payee


async def _category(conn, name: str) -> str:
    return (await conn.fetchrow("SELECT id FROM categories WHERE name = $1", name))["id"]


async def _spending(conn) -> int:
    return (await conn.fetchrow("SELECT coalesce(sum(amount_paise), 0) AS n FROM v_spending"))["n"]


async def _bank(conn, opening: int = 10_000_000):
    return await create_account(conn, kind="bank", name="Test bank", opening_balance_paise=opening)


async def test_expense_and_refund_net_spending(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")

    await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=3_000_000,
        description="Dinner",
        category_id=food,
    )
    await record_refund(
        ledger,
        account_id=bank["id"],
        amount_paise=1_000_000,
        description="Refund",
        category_id=food,
    )

    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 8_000_000
    assert await _spending(ledger) == 2_000_000


async def test_card_bill_and_sip_are_not_spending(ledger):
    bank = await _bank(ledger)
    card = await create_account(
        ledger,
        kind="card",
        name="Card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    fund = await create_account(ledger, kind="asset", name="Mutual funds")

    await record_transfer(
        ledger, from_account_id=bank["id"], to_account_id=card["id"], amount_paise=300_000
    )
    await record_transfer(
        ledger, from_account_id=bank["id"], to_account_id=fund["id"], amount_paise=500_000
    )

    assert await _spending(ledger) == 0
    assert (await get_account(ledger, card["id"]))["balance_paise"] == 300_000
    assert (await get_account(ledger, fund["id"]))["balance_paise"] == 500_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_200_000


async def test_income_comes_from_the_income_account(ledger):
    bank = await _bank(ledger, opening=0)
    employer = await create_payee(ledger, display_name="Acme", kind="organization")

    result = await record_income(
        ledger,
        account_id=bank["id"],
        amount_paise=12_000_000,
        description="Salary",
        payee_id=employer["id"],
    )

    assert result["amount_paise"] == 12_000_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 12_000_000
    assert await _spending(ledger) == 0
    assert (
        await ledger.fetchrow(
            """
            SELECT balance::bigint AS b
            FROM pgledger_accounts a
            JOIN accounts x ON x.ledger_account_id = a.id
            WHERE x.kind = 'income'
            """
        )
    )["b"] == -12_000_000


async def test_split_moves_each_persons_share_to_their_ledger_account(ledger):
    bank = await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")
    amit = await create_payee(ledger, display_name="Amit", kind="person")
    food = await _category(ledger, "Food & Dining")

    result = await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=200_000,
        people=[
            {"payee_id": ravi["id"], "amount_paise": 400_000, "expected_date": date(2026, 12, 1)},
            {"payee_id": amit["id"], "amount_paise": 400_000},
        ],
        total_paise=1_000_000,
        description="Dinner",
        category_id=food,
    )

    assert result["amount_paise"] == 1_000_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_000_000
    assert await _spending(ledger) == 200_000
    owed = {row["display_name"]: row for row in await ledger.fetch("SELECT * FROM v_owed")}
    assert owed["Ravi"]["balance_paise"] == 400_000
    assert owed["Ravi"]["next_expected_date"] == date(2026, 12, 1)
    assert owed["Amit"]["balance_paise"] == 400_000


async def test_split_parts_must_add_up(ledger):
    bank = await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")
    with pytest.raises(InvalidLedgerError, match="add up"):
        await record_split(
            ledger,
            account_id=bank["id"],
            my_share_paise=100,
            people=[{"payee_id": ravi["id"], "amount_paise": 100}],
            total_paise=500,
            description="Bad split",
        )


async def test_lending_and_settlement_reduce_what_is_owed(ledger):
    bank = await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")

    await record_lending(
        ledger,
        account_id=bank["id"],
        payee_id=ravi["id"],
        amount_paise=500_000,
        expected_date=date(2026, 12, 1),
    )
    assert await _spending(ledger) == 0
    await record_settlement(
        ledger, account_id=bank["id"], payee_id=ravi["id"], amount_paise=200_000
    )
    assert await _spending(ledger) == 0
    assert (
        await ledger.fetchrow("SELECT balance_paise FROM v_owed WHERE payee_id = $1", ravi["id"])
    )["balance_paise"] == 300_000


async def test_loan_payment_splits_principal_and_interest(ledger):
    bank = await _bank(ledger)
    loan = await create_account(
        ledger, kind="loan", name="Home loan", opening_balance_paise=-5_000_000
    )
    interest = await _category(ledger, "Loan Interest")

    result = await record_loan_payment(
        ledger,
        account_id=bank["id"],
        loan_account_id=loan["id"],
        amount_paise=2_000_000,
        principal_paise=1_500_000,
        interest_paise=500_000,
        interest_category_id=interest,
    )

    assert result["amount_paise"] == 2_000_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 8_000_000
    assert (await get_account(ledger, loan["id"]))["balance_paise"] == -3_500_000
    assert await _spending(ledger) == 500_000


async def test_amount_correction_appends_reversal_and_corrected_movement(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    original = await record_expense(
        ledger, account_id=bank["id"], amount_paise=300_000, description="Dinner", category_id=food
    )

    corrected = await correct_movement(ledger, original["id"], changes={"amount_paise": 250_000})

    assert corrected["status"] == "corrected"
    assert corrected["reversal_id"] is not None
    assert corrected["movement"]["amount_paise"] == 250_000
    assert await _spending(ledger) == 250_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_750_000
    rows = (await list_movements(ledger, kind="expense"))["items"]
    assert [row["amount_paise"] for row in rows] == [250_000]

    undone = await undo_group(ledger, corrected["undo_id"])
    assert undone["status"] == "undone"
    assert await _spending(ledger) == 300_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_700_000


async def test_label_correction_does_not_touch_the_ledger(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    groceries = await _category(ledger, "Groceries")
    movement = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="BigBasket",
        category_id=food,
    )

    before = (await get_account(ledger, bank["id"]))["balance_paise"]
    await correct_movement(ledger, movement["id"], changes={"category_id": groceries})
    after = (await get_account(ledger, bank["id"]))["balance_paise"]

    assert before == after
    refreshed = await get_movement(ledger, movement["id"])
    assert refreshed["category_name"] == "Groceries"
    assert await _spending(ledger) == 100_000


async def test_date_correction_edits_the_movement_date_and_undoes(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    movement = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Dinner",
        category_id=food,
        date=date(2026, 10, 31),
    )

    corrected = await correct_movement(ledger, movement["id"], changes={"date": date(2026, 11, 1)})
    assert corrected["movement"]["date"] == "2026-11-01"
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_900_000

    await undo_group(ledger, corrected["undo_id"])
    assert (await get_movement(ledger, movement["id"]))["date"] == "2026-10-31"


async def test_idempotency_key_returns_the_same_movement(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    first = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Chai",
        category_id=food,
        idempotency_key="message-1:call-1",
    )
    second = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Chai",
        category_id=food,
        idempotency_key="message-1:call-1",
    )

    assert first["id"] == second["id"]
    assert (await ledger.fetchrow("SELECT count(*) AS n FROM movements WHERE kind = 'expense'"))[
        "n"
    ] == 1
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_900_000


async def test_delete_reverses_and_hides_the_movement(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Chai", category_id=food
    )

    deleted = await correct_movement(ledger, movement["id"], changes={"delete": True})

    assert deleted["status"] == "deleted"
    assert (await list_movements(ledger, kind="expense"))["items"] == []
    assert await _spending(ledger) == 0
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000


async def test_undo_of_undo_restores_the_movement(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Chai", category_id=food
    )

    first = await undo_group(ledger, movement["undo_id"])
    assert await _spending(ledger) == 0
    second = await undo_group(ledger, first["undo_undo_id"])
    assert second["status"] == "undone"
    assert await _spending(ledger) == 100_000
    assert (await list_movements(ledger, kind="expense"))["items"][0]["id"] == movement["id"]


async def test_a_later_write_to_the_same_row_blocks_undo(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    groceries = await _category(ledger, "Groceries")
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Chai", category_id=food
    )
    await correct_movement(ledger, movement["id"], changes={"category_id": groceries})

    with pytest.raises(ConflictLedgerError, match="later write"):
        await undo_group(ledger, movement["undo_id"])


async def test_payee_correction_relabels_past_rows_and_creates_a_user_rule(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    rent = await _category(ledger, "Rent")
    landlord = await create_payee(
        ledger, display_name="Ramesh", kind="person", upi_id="ramesh@okaxis"
    )

    first = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="March rent",
        payee_id=landlord["id"],
        category_id=food,
    )
    second = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="April rent",
        payee_id=landlord["id"],
        category_id=food,
    )

    corrected = await correct_movement(
        ledger,
        first["id"],
        changes={"payee_id": landlord["id"], "category_id": rent},
        scope="all",
    )

    assert corrected["rule"]["source"] == "user"
    assert corrected["rows_changed"] >= 2
    assert (await get_movement(ledger, first["id"]))["category_name"] == "Rent"
    assert (await get_movement(ledger, second["id"]))["category_name"] == "Rent"
    rule = await ledger.fetchrow(
        "SELECT source, category_id FROM category_rules WHERE merchant_pattern = 'ramesh'"
    )
    assert rule["source"] == "user"
    assert rule["category_id"] == rent

    await undo_group(ledger, corrected["undo_id"])
    assert (await get_movement(ledger, first["id"]))["category_name"] == "Food & Dining"
    assert (await get_movement(ledger, second["id"]))["category_name"] == "Food & Dining"
    assert (
        await ledger.fetchrow(
            "SELECT count(*) AS n FROM category_rules WHERE merchant_pattern = 'ramesh'"
        )
    )["n"] == 0


async def test_user_rule_beats_llm_rule(ledger):
    food = await _category(ledger, "Food & Dining")
    shopping = await _category(ledger, "Shopping")
    payee = await create_payee(ledger, display_name="Swiggy", kind="merchant")

    await upsert_rule(ledger, merchant_pattern="swiggy", category_id=shopping, source="llm")
    await upsert_rule(ledger, merchant_pattern="swiggy", category_id=food, source="user")

    resolved = await resolve_category(ledger, payee_id=payee["id"])
    assert resolved["category_id"] == food
    assert resolved["source"] == "rule"
    assert (
        await ledger.fetchrow("SELECT source FROM category_rules WHERE merchant_pattern = 'swiggy'")
    )["source"] == "user"


async def test_a_payee_default_beats_an_llm_rule(ledger):
    food = await _category(ledger, "Food & Dining")
    shopping = await _category(ledger, "Shopping")
    payee = await create_payee(
        ledger, display_name="Swiggy", kind="merchant", default_category_id=food
    )
    await upsert_rule(ledger, merchant_pattern="swiggy", category_id=shopping, source="llm")

    resolved = await resolve_category(ledger, payee_id=payee["id"])

    assert resolved["category_id"] == food
    assert resolved["rule_id"] is None


async def test_a_category_guess_is_saved_as_an_llm_rule_once(ledger):
    bank = await _bank(ledger)
    groceries = await _category(ledger, "Groceries")
    payee = await create_payee(ledger, display_name="BigBasket", kind="merchant")

    await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Groceries",
        payee_id=payee["id"],
        category_guess_id=groceries,
    )

    rule = await ledger.fetchrow(
        "SELECT source, category_id FROM category_rules WHERE merchant_pattern = 'bigbasket'"
    )
    assert rule["source"] == "llm"
    assert rule["category_id"] == groceries
    resolved = await resolve_category(ledger, payee_id=payee["id"])
    assert resolved["category_id"] == groceries


async def test_future_correction_creates_a_rule_without_relabelling_the_past(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    rent = await _category(ledger, "Rent")
    landlord = await create_payee(ledger, display_name="Ramesh", kind="person")

    first = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="March rent",
        payee_id=landlord["id"],
        category_id=food,
    )
    corrected = await correct_movement(
        ledger,
        first["id"],
        changes={"payee_id": landlord["id"], "category_id": rent},
        scope="future",
    )

    assert corrected["rows_changed"] == 1
    assert (await get_movement(ledger, first["id"]))["category_name"] == "Rent"
    assert (
        await ledger.fetchrow(
            "SELECT default_category_id FROM payees WHERE id = $1", landlord["id"]
        )
    )["default_category_id"] == rent
