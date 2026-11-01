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


def _category(conn, name: str) -> str:
    return conn.execute("SELECT id FROM categories WHERE name = %s", (name,)).fetchone()["id"]


def _spending(conn) -> int:
    return conn.execute("SELECT coalesce(sum(amount_paise), 0) AS n FROM v_spending").fetchone()[
        "n"
    ]


def _bank(conn, opening: int = 10_000_000):
    return create_account(conn, kind="bank", name="Test bank", opening_balance_paise=opening)


def test_expense_and_refund_net_spending(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")

    record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=3_000_000,
        description="Dinner",
        category_id=food,
    )
    record_refund(
        ledger,
        account_id=bank["id"],
        amount_paise=1_000_000,
        description="Refund",
        category_id=food,
    )

    assert get_account(ledger, bank["id"])["balance_paise"] == 8_000_000
    assert _spending(ledger) == 2_000_000


def test_card_bill_and_sip_are_not_spending(ledger):
    bank = _bank(ledger)
    card = create_account(
        ledger,
        kind="card",
        name="Card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    fund = create_account(ledger, kind="asset", name="Mutual funds")

    record_transfer(
        ledger, from_account_id=bank["id"], to_account_id=card["id"], amount_paise=300_000
    )
    record_transfer(
        ledger, from_account_id=bank["id"], to_account_id=fund["id"], amount_paise=500_000
    )

    assert _spending(ledger) == 0
    assert get_account(ledger, card["id"])["balance_paise"] == 300_000
    assert get_account(ledger, fund["id"])["balance_paise"] == 500_000
    assert get_account(ledger, bank["id"])["balance_paise"] == 9_200_000


def test_income_comes_from_the_income_account(ledger):
    bank = _bank(ledger, opening=0)
    employer = create_payee(ledger, display_name="Acme", kind="organization")

    result = record_income(
        ledger,
        account_id=bank["id"],
        amount_paise=12_000_000,
        description="Salary",
        payee_id=employer["id"],
    )

    assert result["amount_paise"] == 12_000_000
    assert get_account(ledger, bank["id"])["balance_paise"] == 12_000_000
    assert _spending(ledger) == 0
    assert (
        ledger.execute(
            """
            SELECT balance::bigint AS b
            FROM pgledger_accounts a
            JOIN accounts x ON x.ledger_account_id = a.id
            WHERE x.kind = 'income'
            """
        ).fetchone()["b"]
        == -12_000_000
    )


def test_split_moves_each_persons_share_to_their_ledger_account(ledger):
    bank = _bank(ledger)
    ravi = create_payee(ledger, display_name="Ravi", kind="person")
    amit = create_payee(ledger, display_name="Amit", kind="person")
    food = _category(ledger, "Food & Dining")

    result = record_split(
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
    assert get_account(ledger, bank["id"])["balance_paise"] == 9_000_000
    assert _spending(ledger) == 200_000
    owed = {row["display_name"]: row for row in ledger.execute("SELECT * FROM v_owed").fetchall()}
    assert owed["Ravi"]["balance_paise"] == 400_000
    assert owed["Ravi"]["next_expected_date"] == date(2026, 12, 1)
    assert owed["Amit"]["balance_paise"] == 400_000


def test_split_parts_must_add_up(ledger):
    bank = _bank(ledger)
    ravi = create_payee(ledger, display_name="Ravi", kind="person")
    with pytest.raises(InvalidLedgerError, match="add up"):
        record_split(
            ledger,
            account_id=bank["id"],
            my_share_paise=100,
            people=[{"payee_id": ravi["id"], "amount_paise": 100}],
            total_paise=500,
            description="Bad split",
        )


def test_lending_and_settlement_reduce_what_is_owed(ledger):
    bank = _bank(ledger)
    ravi = create_payee(ledger, display_name="Ravi", kind="person")

    record_lending(
        ledger,
        account_id=bank["id"],
        payee_id=ravi["id"],
        amount_paise=500_000,
        expected_date=date(2026, 12, 1),
    )
    assert _spending(ledger) == 0
    record_settlement(ledger, account_id=bank["id"], payee_id=ravi["id"], amount_paise=200_000)
    assert _spending(ledger) == 0
    assert (
        ledger.execute(
            "SELECT balance_paise FROM v_owed WHERE payee_id = %s", (ravi["id"],)
        ).fetchone()["balance_paise"]
        == 300_000
    )


def test_loan_payment_splits_principal_and_interest(ledger):
    bank = _bank(ledger)
    loan = create_account(ledger, kind="loan", name="Home loan", opening_balance_paise=-5_000_000)
    interest = _category(ledger, "Loan Interest")

    result = record_loan_payment(
        ledger,
        account_id=bank["id"],
        loan_account_id=loan["id"],
        amount_paise=2_000_000,
        principal_paise=1_500_000,
        interest_paise=500_000,
        interest_category_id=interest,
    )

    assert result["amount_paise"] == 2_000_000
    assert get_account(ledger, bank["id"])["balance_paise"] == 8_000_000
    assert get_account(ledger, loan["id"])["balance_paise"] == -3_500_000
    assert _spending(ledger) == 500_000


def test_amount_correction_appends_reversal_and_corrected_movement(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    original = record_expense(
        ledger, account_id=bank["id"], amount_paise=300_000, description="Dinner", category_id=food
    )

    corrected = correct_movement(ledger, original["id"], changes={"amount_paise": 250_000})

    assert corrected["status"] == "corrected"
    assert corrected["reversal_id"] is not None
    assert corrected["movement"]["amount_paise"] == 250_000
    assert _spending(ledger) == 250_000
    assert get_account(ledger, bank["id"])["balance_paise"] == 9_750_000
    rows = list_movements(ledger, kind="expense")["items"]
    assert [row["amount_paise"] for row in rows] == [250_000]

    undone = undo_group(ledger, corrected["undo_id"])
    assert undone["status"] == "undone"
    assert _spending(ledger) == 300_000
    assert get_account(ledger, bank["id"])["balance_paise"] == 9_700_000


def test_label_correction_does_not_touch_the_ledger(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    groceries = _category(ledger, "Groceries")
    movement = record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="BigBasket",
        category_id=food,
    )

    before = get_account(ledger, bank["id"])["balance_paise"]
    correct_movement(ledger, movement["id"], changes={"category_id": groceries})
    after = get_account(ledger, bank["id"])["balance_paise"]

    assert before == after
    refreshed = get_movement(ledger, movement["id"])
    assert refreshed["category_name"] == "Groceries"
    assert _spending(ledger) == 100_000


def test_date_correction_edits_the_movement_date_and_undoes(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    movement = record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Dinner",
        category_id=food,
        date=date(2026, 10, 31),
    )

    corrected = correct_movement(ledger, movement["id"], changes={"date": date(2026, 11, 1)})
    assert corrected["movement"]["date"] == "2026-11-01"
    assert get_account(ledger, bank["id"])["balance_paise"] == 9_900_000

    undo_group(ledger, corrected["undo_id"])
    assert get_movement(ledger, movement["id"])["date"] == "2026-10-31"


def test_idempotency_key_returns_the_same_movement(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    first = record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Chai",
        category_id=food,
        idempotency_key="message-1:call-1",
    )
    second = record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Chai",
        category_id=food,
        idempotency_key="message-1:call-1",
    )

    assert first["id"] == second["id"]
    assert (
        ledger.execute("SELECT count(*) AS n FROM movements WHERE kind = 'expense'").fetchone()["n"]
        == 1
    )
    assert get_account(ledger, bank["id"])["balance_paise"] == 9_900_000


def test_delete_reverses_and_hides_the_movement(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    movement = record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Chai", category_id=food
    )

    deleted = correct_movement(ledger, movement["id"], changes={"delete": True})

    assert deleted["status"] == "deleted"
    assert list_movements(ledger, kind="expense")["items"] == []
    assert _spending(ledger) == 0
    assert get_account(ledger, bank["id"])["balance_paise"] == 10_000_000


def test_undo_of_undo_restores_the_movement(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    movement = record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Chai", category_id=food
    )

    first = undo_group(ledger, movement["undo_id"])
    assert _spending(ledger) == 0
    second = undo_group(ledger, first["undo_undo_id"])
    assert second["status"] == "undone"
    assert _spending(ledger) == 100_000
    assert list_movements(ledger, kind="expense")["items"][0]["id"] == movement["id"]


def test_a_later_write_to_the_same_row_blocks_undo(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    groceries = _category(ledger, "Groceries")
    movement = record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Chai", category_id=food
    )
    correct_movement(ledger, movement["id"], changes={"category_id": groceries})

    with pytest.raises(ConflictLedgerError, match="later write"):
        undo_group(ledger, movement["undo_id"])


def test_payee_correction_relabels_past_rows_and_creates_a_user_rule(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    rent = _category(ledger, "Rent")
    landlord = create_payee(ledger, display_name="Ramesh", kind="person", upi_id="ramesh@okaxis")

    first = record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="March rent",
        payee_id=landlord["id"],
        category_id=food,
    )
    second = record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="April rent",
        payee_id=landlord["id"],
        category_id=food,
    )

    corrected = correct_movement(
        ledger,
        first["id"],
        changes={"payee_id": landlord["id"], "category_id": rent},
        scope="all",
    )

    assert corrected["rule"]["source"] == "user"
    assert corrected["rows_changed"] >= 2
    assert get_movement(ledger, first["id"])["category_name"] == "Rent"
    assert get_movement(ledger, second["id"])["category_name"] == "Rent"
    rule = ledger.execute(
        "SELECT source, category_id FROM category_rules WHERE merchant_pattern = 'ramesh'"
    ).fetchone()
    assert rule["source"] == "user"
    assert rule["category_id"] == rent

    undo_group(ledger, corrected["undo_id"])
    assert get_movement(ledger, first["id"])["category_name"] == "Food & Dining"
    assert get_movement(ledger, second["id"])["category_name"] == "Food & Dining"
    assert (
        ledger.execute(
            "SELECT count(*) AS n FROM category_rules WHERE merchant_pattern = 'ramesh'"
        ).fetchone()["n"]
        == 0
    )


def test_user_rule_beats_llm_rule(ledger):
    food = _category(ledger, "Food & Dining")
    shopping = _category(ledger, "Shopping")
    payee = create_payee(ledger, display_name="Swiggy", kind="merchant")

    upsert_rule(ledger, merchant_pattern="swiggy", category_id=shopping, source="llm")
    upsert_rule(ledger, merchant_pattern="swiggy", category_id=food, source="user")

    resolved = resolve_category(ledger, payee_id=payee["id"])
    assert resolved["category_id"] == food
    assert resolved["source"] == "rule"
    assert (
        ledger.execute(
            "SELECT source FROM category_rules WHERE merchant_pattern = 'swiggy'"
        ).fetchone()["source"]
        == "user"
    )


def test_a_payee_default_beats_an_llm_rule(ledger):
    food = _category(ledger, "Food & Dining")
    shopping = _category(ledger, "Shopping")
    payee = create_payee(ledger, display_name="Swiggy", kind="merchant", default_category_id=food)
    upsert_rule(ledger, merchant_pattern="swiggy", category_id=shopping, source="llm")

    resolved = resolve_category(ledger, payee_id=payee["id"])

    assert resolved["category_id"] == food
    assert resolved["rule_id"] is None


def test_a_category_guess_is_saved_as_an_llm_rule_once(ledger):
    bank = _bank(ledger)
    groceries = _category(ledger, "Groceries")
    payee = create_payee(ledger, display_name="BigBasket", kind="merchant")

    record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Groceries",
        payee_id=payee["id"],
        category_guess_id=groceries,
    )

    rule = ledger.execute(
        "SELECT source, category_id FROM category_rules WHERE merchant_pattern = 'bigbasket'"
    ).fetchone()
    assert rule["source"] == "llm"
    assert rule["category_id"] == groceries
    resolved = resolve_category(ledger, payee_id=payee["id"])
    assert resolved["category_id"] == groceries


def test_future_correction_creates_a_rule_without_relabelling_the_past(ledger):
    bank = _bank(ledger)
    food = _category(ledger, "Food & Dining")
    rent = _category(ledger, "Rent")
    landlord = create_payee(ledger, display_name="Ramesh", kind="person")

    first = record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="March rent",
        payee_id=landlord["id"],
        category_id=food,
    )
    corrected = correct_movement(
        ledger,
        first["id"],
        changes={"payee_id": landlord["id"], "category_id": rent},
        scope="future",
    )

    assert corrected["rows_changed"] == 1
    assert get_movement(ledger, first["id"])["category_name"] == "Rent"
    assert (
        ledger.execute(
            "SELECT default_category_id FROM payees WHERE id = %s", (landlord["id"],)
        ).fetchone()["default_category_id"]
        == rent
    )
