"""Ledger audit: rupee conversion, every movement kind, splits, corrections, undo, defaults.

Every assertion is in exact paise. The tests here are the release gate for the double-entry ledger
and its write paths (architecture rules 1, 2, and 6).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from arno.ledger.accounts import (
    archive_account,
    create_account,
    get_account,
)
from arno.ledger.audit import undo_group
from arno.ledger.common import today
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError
from arno.ledger.goals import create_goal, get_goal
from arno.ledger.movements import (
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
from arno.ledger.payees import create_payee
from arno.ledger.schedule_payments import link_payment
from arno.ledger.schedules import create_schedule, get_occurrence
from arno.tools import write as write_tools


async def _category(conn, name: str) -> str:
    return (await conn.fetchrow("SELECT id FROM categories WHERE name = $1", name))["id"]


async def _bank(conn, name: str = "Audit bank", opening: int = 10_000_000) -> dict:
    return await create_account(conn, kind="bank", name=name, opening_balance_paise=opening)


async def _spending(conn) -> int:
    return int(
        (await conn.fetchrow("SELECT coalesce(sum(amount_paise), 0) AS n FROM v_spending"))["n"]
    )


async def _balances(conn) -> dict[str, int]:
    rows = await conn.fetch(
        "SELECT DISTINCT ON (account_id) account_id, balance_paise "
        "FROM v_balances ORDER BY account_id, as_of DESC"
    )
    return {str(row["account_id"]): int(row["balance_paise"]) for row in rows}


async def _owed(conn) -> dict[str, int]:
    rows = await conn.fetch("SELECT payee_id, balance_paise FROM v_owed")
    return {str(row["payee_id"]): int(row["balance_paise"]) for row in rows}


async def _category_totals(conn) -> dict[str, int]:
    rows = await conn.fetch(
        "SELECT coalesce(category_name, 'Uncategorized') AS name, "
        "sum(amount_paise)::bigint AS total FROM v_spending GROUP BY 1"
    )
    return {str(row["name"]): int(row["total"]) for row in rows}


async def _entry_sum(conn, movement_id: str) -> int:
    """Every movement's entries must net to zero: double-entry, always."""
    return int(
        await conn.fetchval(
            """
            SELECT coalesce(sum(e.amount), 0)::bigint
            FROM pgledger_entries e
            JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
            WHERE ml.movement_id = $1
            """,
            movement_id,
        )
    )


async def _cashflow(conn, movement_id: str) -> list[dict]:
    return await conn.fetch(
        """
        SELECT from_account_kind AS from_kind, to_account_kind AS to_kind, amount_paise
        FROM v_legs WHERE movement_id = $1 ORDER BY amount_paise
        """,
        movement_id,
    )


async def _message(conn, suffix: str) -> str:
    conversation_id = f"cnv_audit_{suffix}"
    await conn.execute(
        "INSERT INTO conversations (id, title) VALUES ($1, 'ledger audit') "
        "ON CONFLICT (id) DO NOTHING",
        conversation_id,
    )
    row = await conn.fetchrow(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES ($1, $2, 'user', '{}'::jsonb, 'audit') RETURNING id
        """,
        conversation_id,
        f"cli_audit_{suffix}",
    )
    return str(row["id"])


async def _tool_record(conn, movement: dict, *, suffix: str = "one", **kwargs):
    message_id = await _message(conn, suffix)
    return await write_tools.record_movement(
        conn,
        movement,
        message_id=message_id,
        conversation_id=f"cnv_audit_{suffix}",
        **kwargs,
    )


# 1. Rupee to paise -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "paise"),
    [
        (450, 45000),
        (19.99, 1999),
        ("12.345", 1235),
        (Decimal("0.005"), 1),
        (450.155, 45016),
        (0.1 + 0.2, 30),
        (2.675, 268),
    ],
)
def test_rupees_rounds_half_up_to_whole_paise(value, paise):
    assert write_tools._rupees(value) == paise


@pytest.mark.parametrize(
    "value",
    [None, 0, -1, "0", "", "abc", float("nan"), float("inf"), float("-inf"), 10**20],
)
def test_rupees_rejects_unusable_amounts(value):
    with pytest.raises(write_tools.ToolInputError):
        write_tools._rupees(value)


async def test_a_sub_paise_rupee_amount_is_rounded_by_the_tool_not_truncated(ledger):
    bank = await _bank(ledger)
    result = await _tool_record(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": 450.155,
            "payee": "Rounding cafe",
            "account": "Audit bank",
            "category": "Food & Dining",
        },
    )
    assert result["status"] == "recorded"
    assert result["transfers"][0]["amount_paise"] == 45016
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000 - 45016


# 2. Every movement kind ------------------------------------------------------------------------


async def test_expense_refund_and_card_repayment_land_in_the_views_once(ledger):
    bank = await _bank(ledger)
    card = await create_account(
        ledger,
        kind="card",
        name="Audit card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
        opening_balance_paise=0,
    )
    food = await _category(ledger, "Food & Dining")

    purchase = await record_expense(
        ledger, account_id=bank["id"], amount_paise=45015, description="Dinner", category_id=food
    )
    refund = await record_refund(
        ledger,
        account_id=bank["id"],
        amount_paise=15015,
        description="Partial refund",
        category_id=food,
    )
    card_spend = await record_expense(
        ledger,
        account_id=card["id"],
        amount_paise=30000,
        description="Headphones",
        category_id=food,
    )
    repayment = await record_transfer(
        ledger, from_account_id=bank["id"], to_account_id=card["id"], amount_paise=30000
    )

    assert await _entry_sum(ledger, purchase["id"]) == 0
    assert await _entry_sum(ledger, refund["id"]) == 0
    assert await _entry_sum(ledger, card_spend["id"]) == 0
    assert await _entry_sum(ledger, repayment["id"]) == 0

    assert await _spending(ledger) == 45015 - 15015 + 30000
    assert (await get_account(ledger, bank["id"]))[
        "balance_paise"
    ] == 10_000_000 - 45015 + 15015 - 30000
    assert (await get_account(ledger, card["id"]))["balance_paise"] == 0

    balances = await _balances(ledger)
    assert balances[bank["id"]] == 10_000_000 - 45015 + 15015 - 30000
    assert balances[card["id"]] == 0

    refund_flow = await _cashflow(ledger, refund["id"])
    assert [(row["from_kind"], row["to_kind"], row["amount_paise"]) for row in refund_flow] == [
        ("spending", "bank", 15015)
    ]
    assert await _owed(ledger) == {}


async def test_income_and_opening_balance_are_never_consumption(ledger):
    bank = await create_account(
        ledger, kind="bank", name="Audit bank", opening_balance_paise=1_000_000
    )
    employer = await create_payee(ledger, display_name="Audit employer", kind="organization")

    income = await record_income(
        ledger, account_id=bank["id"], amount_paise=5_000_000, payee_id=employer["id"]
    )

    assert await _entry_sum(ledger, income["id"]) == 0
    assert await _spending(ledger) == 0
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 6_000_000
    assert (await _balances(ledger))[bank["id"]] == 6_000_000
    assert await _owed(ledger) == {}


async def test_income_into_a_system_account_is_rejected(ledger):
    spending = await ledger.fetchrow("SELECT * FROM accounts WHERE kind = 'spending'")

    with pytest.raises(InvalidLedgerError):
        await record_income(ledger, account_id=spending["id"], amount_paise=100_000)

    assert await _spending(ledger) == 0


async def test_loan_payment_through_the_tool_uses_the_real_outstanding_balance(ledger):
    await _bank(ledger)
    loan = await create_account(
        ledger,
        kind="loan",
        name="Audit loan",
        opening_balance_paise=-5_000_000,
        rate_bps=1200,
        tenure_months=12,
    )

    result = await _tool_record(
        ledger,
        {
            "kind": "loan",
            "amount_rupees": 10_000,
            "loan": "Audit loan",
            "account": "Audit bank",
        },
        suffix="loan",
    )

    # 12% a year on ₹50,000 is ₹500 of interest this month; the rest is principal.
    assert result["status"] == "recorded"
    assert await _spending(ledger) == 50_000
    assert (await get_account(ledger, loan["id"]))["balance_paise"] == -4_050_000
    assert await _entry_sum(ledger, result["movement_id"]) == 0


async def test_loan_payment_with_explicit_principal_and_interest_is_exact(ledger):
    bank = await _bank(ledger)
    loan = await create_account(
        ledger, kind="loan", name="Audit loan", opening_balance_paise=-5_000_000
    )
    interest = await _category(ledger, "Loan Interest")

    result = await record_loan_payment(
        ledger,
        account_id=bank["id"],
        loan_account_id=loan["id"],
        amount_paise=1_000_000,
        principal_paise=800_000,
        interest_paise=200_000,
        interest_category_id=interest,
    )

    assert result["amount_paise"] == 1_000_000
    assert await _spending(ledger) == 200_000
    assert (await get_account(ledger, loan["id"]))["balance_paise"] == -4_200_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_000_000


# 3. Splits ------------------------------------------------------------------------------------


async def test_a_hundred_split_three_ways_is_exact_and_only_my_share_is_consumption(ledger):
    bank = await _bank(ledger)
    people = [
        await create_payee(ledger, display_name="Ravi", kind="person"),
        await create_payee(ledger, display_name="Amit", kind="person"),
    ]
    food = await _category(ledger, "Food & Dining")

    result = await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=3334,
        people=[
            {"payee_id": people[0]["id"], "amount_paise": 3333},
            {"payee_id": people[1]["id"], "amount_paise": 3333},
        ],
        total_paise=10_000,
        description="Dinner",
        category_id=food,
    )

    assert result["amount_paise"] == 10_000
    assert await _spending(ledger) == 3334
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000 - 10_000
    owed = await _owed(ledger)
    assert owed == {people[0]["id"]: 3333, people[1]["id"]: 3333}
    for transfer in result["transfers"]:
        assert transfer["amount_paise"] in {3334, 3333}


async def test_a_one_paise_split_owes_the_whole_penny(ledger):
    bank = await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")

    result = await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=0,
        people=[{"payee_id": ravi["id"], "amount_paise": 1}],
        description="A penny for Ravi",
    )

    assert result["amount_paise"] == 1
    assert await _spending(ledger) == 0
    assert await _owed(ledger) == {ravi["id"]: 1}
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000 - 1


async def test_uneven_split_with_a_zero_share_part_is_rejected(ledger):
    bank = await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")

    with pytest.raises(InvalidLedgerError, match="positive"):
        await record_split(
            ledger,
            account_id=bank["id"],
            my_share_paise=1,
            people=[{"payee_id": ravi["id"], "amount_paise": 0}],
            description="Zero share",
        )


async def test_lending_and_borrowing_repay_in_both_directions(ledger):
    bank = await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")

    # I lend 500, they pay 200, then overpay by 100.
    await record_lending(ledger, account_id=bank["id"], payee_id=ravi["id"], amount_paise=500_000)
    await record_settlement(
        ledger, account_id=bank["id"], payee_id=ravi["id"], amount_paise=200_000
    )
    assert (await _owed(ledger))[ravi["id"]] == 300_000
    await record_settlement(
        ledger, account_id=bank["id"], payee_id=ravi["id"], amount_paise=400_000
    )
    assert (await _owed(ledger))[ravi["id"]] == -100_000
    row = await ledger.fetchrow("SELECT direction FROM v_owed WHERE payee_id = $1", ravi["id"])
    assert row["direction"] == "you_owe"
    assert await _spending(ledger) == 0

    # I borrow 500 while I am 100 in credit: I now owe 600, and paying 600 settles it.
    await record_lending(
        ledger,
        account_id=bank["id"],
        payee_id=ravi["id"],
        amount_paise=500_000,
        direction="i_borrowed",
    )
    assert (await _owed(ledger))[ravi["id"]] == -600_000
    await record_settlement(
        ledger,
        account_id=bank["id"],
        payee_id=ravi["id"],
        amount_paise=600_000,
        direction="i_paid",
    )
    assert (await _owed(ledger))[ravi["id"]] == 0
    assert (await ledger.fetchrow("SELECT direction FROM v_owed WHERE payee_id = $1", ravi["id"]))[
        "direction"
    ] == "settled"


# 4. Corrections -------------------------------------------------------------------------------


async def test_correcting_amount_account_and_date_matches_recording_it_right_once(ledger):
    first = await _bank(ledger, name="First bank")
    second = await _bank(ledger, name="Second bank")
    food = await _category(ledger, "Food & Dining")
    groceries = await _category(ledger, "Groceries")

    movement = await record_expense(
        ledger,
        account_id=first["id"],
        amount_paise=300_000,
        description="Dinner",
        category_id=food,
    )

    corrected = await correct_movement(
        ledger,
        movement["id"],
        changes={
            "amount_paise": 250_000,
            "account_id": second["id"],
            "category_id": groceries,
            "date": date(2026, 10, 31),
        },
    )

    assert corrected["status"] == "corrected"
    assert (await get_account(ledger, first["id"]))["balance_paise"] == 10_000_000
    assert (await get_account(ledger, second["id"]))["balance_paise"] == 9_750_000
    assert await _spending(ledger) == 250_000
    effective = (await list_movements(ledger, kind="expense"))["items"]
    assert len(effective) == 1
    assert effective[0]["amount_paise"] == 250_000
    assert effective[0]["date"] == "2026-10-31"
    assert effective[0]["category_name"] == "Groceries"

    # Audit history: the original and its reversal are still there, hidden from the effective list.
    history = (await list_movements(ledger, kind="expense", include_reversed=True))["items"]
    assert sorted(row["id"] for row in history) == sorted(
        [movement["id"], corrected["movement_id"]]
    )
    assert (await get_movement(ledger, movement["id"]))["reversed"] is True
    assert (await get_movement(ledger, corrected["movement_id"]))["reversed"] is False
    assert await _entry_sum(ledger, movement["id"]) == 0


async def test_a_correction_cannot_move_a_movement_into_the_future(ledger):
    bank = await _bank(ledger)
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Lunch"
    )

    with pytest.raises(InvalidLedgerError, match="schedule"):
        await correct_movement(
            ledger, movement["id"], changes={"date": today() + timedelta(days=5)}
        )

    assert (await get_movement(ledger, movement["id"]))["date"] == today().isoformat()


async def test_a_noop_correction_advertises_no_undo_id(ledger):
    bank = await _bank(ledger)
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Lunch"
    )

    result = await write_tools.correct_movement(
        ledger, movement_id=movement["id"], changes={}, message_id=None
    )

    assert result["undo_id"] is None
    assert result["card"]["undo_id"] is None


# 5. Undo --------------------------------------------------------------------------------------


async def test_double_undo_reports_already_undone_and_undo_of_undo_restores(ledger):
    bank = await _bank(ledger)
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Lunch"
    )

    first = await undo_group(ledger, movement["undo_id"])
    assert first["status"] == "undone"
    assert await _spending(ledger) == 0
    second = await undo_group(ledger, movement["undo_id"])
    assert second["status"] == "already_undone"
    assert await _spending(ledger) == 0
    await undo_group(ledger, first["undo_undo_id"])
    assert await _spending(ledger) == 100_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_900_000


async def test_undo_and_undo_of_undo_keep_spending_inside_its_category(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=10_000, description="Chai", category_id=food
    )

    first = await undo_group(ledger, movement["undo_id"])

    assert await _category_totals(ledger) == {"Food & Dining": 0}
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM movement_legs WHERE movement_id = $1", movement["id"]
        )
        == 1
    )

    await undo_group(ledger, first["undo_undo_id"])

    assert await _category_totals(ledger) == {"Food & Dining": 10_000}


async def test_undoing_a_remembered_memory_deactivates_it(ledger):
    remembered = await write_tools.preference(
        ledger,
        action="remember",
        text="Use UPI first",
        preference_id=None,
        message_id=None,
    )

    result = await write_tools.undo(
        ledger, undo_id=remembered["undo_id"], conversation_id=None, message_id=None
    )

    assert result["status"] == "undone"
    assert (
        await ledger.fetchval("SELECT active FROM preferences WHERE id = $1", remembered["id"])
    ) is False


async def test_undoing_a_goal_contribution_restores_the_goal_and_both_balances(ledger):
    bank = await _bank(ledger)
    goal = await create_goal(ledger, name="Audit trip", target_paise=1_000_000)
    contribution = await record_transfer(
        ledger, from_account_id=bank["id"], to_account_id=goal["account_id"], amount_paise=250_000
    )

    await undo_group(ledger, contribution["undo_id"])

    assert (await get_goal(ledger, goal["id"]))["saved_paise"] == 0
    assert (await get_account(ledger, goal["account_id"]))["balance_paise"] == 0
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000


async def test_undoing_a_split_restores_what_is_owed_and_the_account(ledger):
    bank = await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")
    split = await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=30_000,
        people=[{"payee_id": ravi["id"], "amount_paise": 60_000}],
        description="Dinner",
    )
    assert (await _owed(ledger))[ravi["id"]] == 60_000
    assert await _spending(ledger) == 30_000

    await undo_group(ledger, split["undo_id"])

    assert (await _owed(ledger))[ravi["id"]] == 0
    assert await _spending(ledger) == 0
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000


async def test_undoing_a_setup_change_restores_the_exact_previous_row(ledger):
    card = await create_account(
        ledger,
        kind="card",
        name="Audit card",
        credit_limit_paise=1_000_000,
        statement_day=5,
        due_day=25,
    )

    await write_tools.update_setup_item(
        ledger,
        entity="account",
        action="update",
        data={"name": "Audit card", "credit_limit_rupees": 2_500, "due_day": 28},
        message_id=None,
    )
    updated = await get_account(ledger, card["id"])
    assert updated["credit_limit_paise"] == 250_000
    assert updated["due_day"] == 28

    undo_id = await ledger.fetchval(
        "SELECT group_id FROM audit_log WHERE table_name = 'accounts' AND row_id = $1 "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        card["id"],
    )
    await undo_group(ledger, str(undo_id))

    restored = await get_account(ledger, card["id"])
    assert restored["credit_limit_paise"] == 1_000_000
    assert restored["due_day"] == 25


# 6. Duplicate detection and idempotency --------------------------------------------------------


async def test_a_retried_call_with_the_same_key_writes_once(ledger):
    await _bank(ledger)
    movement = {
        "kind": "expense",
        "amount_rupees": 450,
        "payee": "Swiggy",
        "account": "Audit bank",
        "category": "Food & Dining",
        "tool_call_id": "call-retry",
    }
    message_id = await _message(ledger, "retry")

    first = await write_tools.record_movement(
        ledger, dict(movement), message_id=message_id, conversation_id="cnv_audit_retry"
    )
    second = await write_tools.record_movement(
        ledger, dict(movement), message_id=message_id, conversation_id="cnv_audit_retry"
    )

    assert first["movement_id"] == second["movement_id"]
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 1
    assert (
        await get_account(
            ledger,
            (await ledger.fetchrow("SELECT id FROM accounts WHERE name = 'Audit bank'"))["id"],
        )
    )["balance_paise"] == 10_000_000 - 45_000


async def test_a_retry_without_a_message_id_still_uses_the_tool_call_id(ledger):
    await _bank(ledger)
    movement = {
        "kind": "expense",
        "amount_rupees": 450,
        "payee": "Swiggy",
        "account": "Audit bank",
        "tool_call_id": "call-woken-turn",
    }

    first = await write_tools.record_movement(
        ledger, dict(movement), message_id=None, conversation_id="cnv_woken"
    )
    second = await write_tools.record_movement(
        ledger, dict(movement), message_id=None, conversation_id="cnv_woken"
    )

    assert first["movement_id"] == second["movement_id"]
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 1
    assert (await _spending(ledger)) == 45_000


async def test_a_same_amount_purchase_before_the_retry_asks_instead_of_double_writing(ledger):
    await _bank(ledger)
    movement = {
        "kind": "expense",
        "amount_rupees": 450,
        "payee": "Swiggy",
        "account": "Audit bank",
        "category": "Food & Dining",
    }

    await _tool_record(ledger, dict(movement), suffix="first")
    duplicate = await _tool_record(ledger, dict(movement), suffix="second")

    assert duplicate["status"] == "possible_duplicate"
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 1


async def test_a_genuinely_distinct_same_amount_purchase_is_recordable(ledger):
    bank = await _bank(ledger)
    movement = {
        "kind": "expense",
        "amount_rupees": 450,
        "payee": "Swiggy",
        "account": "Audit bank",
        "category": "Food & Dining",
    }

    await _tool_record(ledger, dict(movement), suffix="distinct-one")
    confirmed = await _tool_record(
        ledger, dict(movement), suffix="distinct-two", not_duplicate=True
    )

    assert confirmed["status"] == "recorded"
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 2
    assert await _spending(ledger) == 90_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000 - 90_000


async def test_concurrent_retries_with_the_same_key_write_one_movement(ledger):
    import asyncio

    from arno.db.conn import connect

    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")

    async def record() -> dict:
        async with connect() as conn:
            return await record_expense(
                conn,
                account_id=bank["id"],
                amount_paise=45_000,
                description="Chai",
                category_id=food,
                idempotency_key="audit-concurrent-retry",
            )

    results = await asyncio.gather(record(), record(), return_exceptions=True)

    assert not [result for result in results if isinstance(result, Exception)]
    rows = [result for result in results if isinstance(result, dict)]
    assert len({row["id"] for row in rows}) == 1
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 1
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000 - 45_000


# 7. Default account choice ---------------------------------------------------------------------


async def test_the_usual_account_never_picks_an_archived_account(ledger):
    old = await _bank(ledger, name="Old bank")
    new = await _bank(ledger, name="New bank")
    payee = await create_payee(ledger, display_name="Audit cafe", kind="merchant")
    await record_expense(
        ledger,
        account_id=old["id"],
        amount_paise=10_000,
        description="Coffee",
        payee_id=payee["id"],
    )
    await archive_account(ledger, old["id"])

    result = await _tool_record(
        ledger,
        {"kind": "expense", "amount_rupees": 450, "payee": "Audit cafe"},
        suffix="archived",
    )

    assert result["status"] == "recorded"
    assert result["transfers"][0]["from_account_id"] == new["id"]
    assert (await get_account(ledger, new["id"]))["balance_paise"] == 10_000_000 - 45_000
    assert (await get_account(ledger, old["id"]))["balance_paise"] == 9_990_000


async def test_without_history_the_busiest_liquid_account_is_used(ledger):
    await _bank(ledger, name="First bank")
    second = await _bank(ledger, name="Second bank")
    for _ in range(2):
        await record_expense(ledger, account_id=second["id"], amount_paise=1000, description="Busy")

    result = await _tool_record(
        ledger, {"kind": "expense", "amount_rupees": 10, "payee": "New shop"}, suffix="everyday"
    )

    assert result["transfers"][0]["from_account_id"] == second["id"]
    assert result["account_source"] == "usual"


# 8. Schedule payments through the tool ---------------------------------------------------------


async def _schedule(conn, bank, amount=100_000, rule="FREQ=DAILY;COUNT=90"):
    return await create_schedule(
        conn,
        kind="obligation",
        name="Audit rent",
        amount_paise=amount,
        account_id=bank["id"],
        rule=rule,
        starts=today() - timedelta(days=30),
    )


async def test_schedule_payment_partial_over_wrong_date_repeat_and_undo(ledger):
    bank = await _bank(ledger)
    schedule = await _schedule(ledger, bank)
    due = today() + timedelta(days=10)
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Rent paid"
    )

    partial = await write_tools.schedule_payment(
        ledger,
        schedule=schedule["id"],
        due_date=due,
        movement_id=movement["id"],
        amount_rupees="600.00",
        message_id=None,
    )
    assert partial["occurrence"]["remaining_paise"] == 40_000
    assert partial["occurrence"]["payment_status"] == "partial"
    assert partial["card"]["undo_id"] == partial["undo_id"]

    with pytest.raises(ConflictLedgerError, match="Unlink the existing allocation"):
        await write_tools.schedule_payment(
            ledger,
            schedule=schedule["id"],
            due_date=due,
            movement_id=movement["id"],
            amount_rupees="700.00",
            message_id=None,
        )
    second = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Rent top-up"
    )
    with pytest.raises(InvalidLedgerError, match="unassigned"):
        await write_tools.schedule_payment(
            ledger,
            schedule=schedule["id"],
            due_date=due,
            movement_id=second["id"],
            amount_rupees="1200.00",
            message_id=None,
        )
    with pytest.raises(InvalidLedgerError, match="occurrence"):
        await write_tools.schedule_payment(
            ledger,
            schedule=schedule["id"],
            due_date=due + timedelta(days=400),
            movement_id=movement["id"],
            message_id=None,
        )
    repeated = await write_tools.schedule_payment(
        ledger,
        schedule=schedule["id"],
        due_date=due,
        movement_id=movement["id"],
        message_id=None,
    )
    assert repeated["status"] == "already_linked"
    assert repeated["undo_id"] is None

    await undo_group(ledger, partial["undo_id"])
    assert (await get_occurrence(ledger, schedule["id"], due))["paid_paise"] == 0
    assert (await get_occurrence(ledger, schedule["id"], due))["occurrence_id"] is None
    assert await ledger.fetchval("SELECT count(*) FROM schedule_payments") == 0


async def test_undoing_a_link_restores_the_occurrence_status_exactly(ledger):
    bank = await _bank(ledger)
    schedule = await _schedule(ledger, bank)
    due = today() - timedelta(days=2)
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=60_000, description="Rent part"
    )

    linked = await link_payment(
        ledger, schedule_id=schedule["id"], due_date=due, movement_id=movement["id"]
    )
    assert (await get_occurrence(ledger, schedule["id"], due))["payment_status"] == "partial"

    await undo_group(ledger, linked["undo_id"])
    row = await get_occurrence(ledger, schedule["id"], due)
    assert row["paid_paise"] == 0
    assert row["payment_status"] == "unmatched"


# 9. Setup writes --------------------------------------------------------------------------------


async def test_card_day_31_works_in_february_without_crashing(ledger):
    await create_account(
        ledger,
        kind="card",
        name="Day 31 card",
        credit_limit_paise=1_000_000,
        statement_day=31,
        due_day=31,
        opening_balance_paise=-50_000,
    )

    from arno.ledger.schedules import upcoming

    events = (await upcoming(ledger, days=28, start=date(2026, 2, 1)))["events"]
    due = [event for event in events if event["kind"] == "card_due"]
    assert [event["date"] for event in due] == ["2026-02-28"]


async def test_update_setup_card_days_are_validated(ledger):
    with pytest.raises(InvalidLedgerError, match="statement day"):
        await write_tools.update_setup_item(
            ledger,
            entity="account",
            action="add",
            data={
                "name": "Bad card",
                "kind": "card",
                "credit_limit_rupees": 1000,
                "statement_day": 32,
                "due_day": 25,
            },
            message_id=None,
        )


async def test_update_setup_cannot_silently_ignore_an_opening_balance_change(ledger):
    account = await _bank(ledger)

    with pytest.raises(write_tools.ToolInputError, match="balance"):
        await write_tools.update_setup_item(
            ledger,
            entity="account",
            action="update",
            data={"name": "Audit bank", "opening_balance_rupees": 5_000},
            message_id=None,
        )

    assert (await get_account(ledger, account["id"]))["balance_paise"] == 10_000_000


async def test_update_setup_add_without_a_name_is_a_tool_input_error(ledger):
    with pytest.raises(write_tools.ToolInputError):
        await write_tools.update_setup_item(
            ledger, entity="account", action="add", data={}, message_id=None
        )


async def test_restating_a_balance_is_an_adjustment_and_undoes(ledger):
    account = await _bank(ledger)

    result = await write_tools.update_setup_item(
        ledger,
        entity="balance",
        action="update",
        data={"account": "Audit bank", "balance_rupees": "1250.75"},
        message_id=None,
    )

    assert result["summary"] == "Set Audit bank balance to ₹1,250.75"
    assert (await get_account(ledger, account["id"]))["balance_paise"] == 125_075
    assert await _spending(ledger) == 0
    await undo_group(ledger, result["undo_id"])
    assert (await get_account(ledger, account["id"]))["balance_paise"] == 10_000_000


async def test_stating_a_card_balance_records_what_is_owed_not_an_asset(ledger):
    card = await create_account(
        ledger,
        kind="card",
        name="Audit card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
        opening_balance_paise=0,
    )

    result = await write_tools.update_setup_item(
        ledger,
        entity="balance",
        action="update",
        data={"account": "Audit card", "balance_rupees": 5000},
        message_id=None,
    )

    assert result["summary"] == "Set Audit card balance to ₹5,000 due"
    refreshed = await get_account(ledger, card["id"])
    assert refreshed["balance_paise"] == -500_000
    assert refreshed["card_outstanding_paise"] == 500_000
    assert refreshed["available_credit_paise"] == 9_500_000


async def test_opening_balance_through_the_tool_is_an_adjustment_with_exact_paise(ledger):
    result = await write_tools.update_setup_item(
        ledger,
        entity="account",
        action="add",
        data={"name": "Opening bank", "kind": "bank", "opening_balance_rupees": "1234.565"},
        message_id=None,
    )

    account = await get_account(ledger, result["result"]["id"])
    assert account["balance_paise"] == 123_457
    assert account["statement_balance_paise"] == 123_457
    assert await _spending(ledger) == 0


async def test_goal_setup_add_update_archive_and_undo(ledger):
    added = await write_tools.update_setup_item(
        ledger,
        entity="goal",
        action="add",
        data={"name": "Audit trip", "target_rupees": 50_000, "priority": 2},
        message_id=None,
    )
    goal_id = added["result"]["id"]
    assert added["result"]["target_paise"] == 5_000_000

    updated = await write_tools.update_setup_item(
        ledger,
        entity="goal",
        action="update",
        data={"id": goal_id, "target_rupees": 60_000, "deadline": "2027-03-31"},
        message_id=None,
    )
    assert updated["result"]["target_paise"] == 6_000_000
    await undo_group(ledger, updated["undo_id"])
    assert (await get_goal(ledger, goal_id))["target_paise"] == 5_000_000

    archived = await write_tools.update_setup_item(
        ledger,
        entity="goal",
        action="archive",
        data={"id": goal_id},
        message_id=None,
    )
    assert archived["result"]["status"] == "abandoned"
    await undo_group(ledger, archived["undo_id"])
    assert (await get_goal(ledger, goal_id))["status"] == "active"


async def test_a_goal_contribution_is_savings_not_consumption(ledger):
    from arno.ledger.movements import record_goal_contribution

    bank = await _bank(ledger)
    goal = await create_goal(ledger, name="Audit trip", target_paise=1_000_000)

    contribution = await record_goal_contribution(
        ledger, account_id=bank["id"], goal_account_id=goal["account_id"], amount_paise=250_000
    )

    assert await _spending(ledger) == 0
    assert (await get_goal(ledger, goal["id"]))["saved_paise"] == 250_000
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 9_750_000
    label = await ledger.fetchrow(
        "SELECT category_id FROM movement_legs WHERE movement_id = $1", contribution["id"]
    )
    assert label["category_id"] == await _category(ledger, "Savings & Investments")


async def test_forgetting_a_memory_is_undoable(ledger):
    remembered = await write_tools.preference(
        ledger, action="remember", text="Use UPI first", preference_id=None, message_id=None
    )
    forgotten = await write_tools.preference(
        ledger,
        action="forget",
        text=None,
        preference_id=remembered["id"],
        message_id=None,
    )
    assert forgotten["status"] == "forgotten"

    await undo_group(ledger, forgotten["undo_id"])

    assert (
        await ledger.fetchval("SELECT active FROM preferences WHERE id = $1", remembered["id"])
    ) is True


async def test_a_split_names_its_vendor_as_a_merchant_not_a_person(ledger):
    await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")

    await _tool_record(
        ledger,
        {
            "kind": "split",
            "total_rupees": 900,
            "my_share_rupees": 300,
            "payee": "Swiggy",
            "people": [{"name": ravi["display_name"], "amount_rupees": 600}],
        },
        suffix="split-payee",
    )

    kind = await ledger.fetchval("SELECT kind FROM payees WHERE display_name = 'Swiggy'")
    assert kind == "merchant"
    assert (await _owed(ledger))[ravi["id"]] == 60_000


async def test_a_keyless_capture_still_sees_a_keyed_duplicate(ledger):
    await _bank(ledger)
    movement = {
        "kind": "expense",
        "amount_rupees": 450,
        "payee": "Swiggy",
        "account": "Audit bank",
        "category": "Food & Dining",
    }
    keyed_message = await _message(ledger, "keyed")
    await write_tools.record_movement(
        ledger,
        dict(movement),
        message_id=keyed_message,
        conversation_id="cnv_audit_keyed",
        not_duplicate=True,
    )

    duplicate = await write_tools.record_movement(
        ledger,
        dict(movement),
        message_id=None,
        conversation_id="cnv_audit_keyless",
        capture_ref="photo-1",
    )

    assert duplicate["status"] == "possible_duplicate"
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 1


async def test_a_future_expense_is_rejected_with_a_schedule_hint(ledger):
    await _bank(ledger)

    with pytest.raises(InvalidLedgerError, match="schedule"):
        await _tool_record(
            ledger,
            {
                "kind": "expense",
                "amount_rupees": 450,
                "payee": "Swiggy",
                "account": "Audit bank",
                "date": (today() + timedelta(days=3)).isoformat(),
            },
            suffix="future",
        )

    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 0


async def test_a_flagged_thread_marks_a_new_payee_name_from_outside_text(ledger):
    await _bank(ledger)

    result = await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 100, "payee": "Unseen vendor"},
        message_id=None,
        conversation_id="cnv_audit_flagged",
        flagged=True,
        user_texts=("I paid for lunch",),
        not_duplicate=True,
    )

    assert result["status"] == "recorded"
    assert (
        await ledger.fetchval(
            "SELECT outside_text FROM payees WHERE display_name = 'Unseen vendor'"
        )
    ) is True
    assert result["undo_id"]


async def test_a_flagged_thread_keeps_a_name_the_user_typed_trusted(ledger):
    await _bank(ledger)

    await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 100, "payee": "Unseen vendor"},
        message_id=None,
        conversation_id="cnv_audit_typed",
        flagged=True,
        user_texts=("I paid Unseen vendor for lunch",),
        not_duplicate=True,
    )

    assert (
        await ledger.fetchval(
            "SELECT outside_text FROM payees WHERE display_name = 'Unseen vendor'"
        )
    ) is False


async def test_a_split_can_have_a_zero_share_of_mine(ledger):
    await _bank(ledger)
    ravi = await create_payee(ledger, display_name="Ravi", kind="person")

    result = await _tool_record(
        ledger,
        {
            "kind": "split",
            "total_rupees": 0.01,
            "my_share_rupees": 0,
            "people": [{"name": "Ravi", "amount_rupees": 0.01}],
        },
        suffix="zero-share",
    )

    assert result["status"] == "recorded"
    assert await _spending(ledger) == 0
    assert (await _owed(ledger))[ravi["id"]] == 1


async def test_adding_a_card_through_the_tool_records_what_is_owed(ledger):
    result = await write_tools.update_setup_item(
        ledger,
        entity="account",
        action="add",
        data={
            "name": "Owed card",
            "kind": "card",
            "credit_limit_rupees": 10_000,
            "statement_day": 5,
            "due_day": 25,
            "opening_balance_rupees": 5_000,
        },
        message_id=None,
    )

    added = await get_account(ledger, result["result"]["id"])
    assert added["balance_paise"] == -500_000
    assert added["card_outstanding_paise"] == 500_000
    assert added["available_credit_paise"] == 500_000
    assert await _spending(ledger) == 0


async def test_adding_a_loan_through_the_tool_records_the_outstanding_principal(ledger):
    result = await write_tools.update_setup_item(
        ledger,
        entity="account",
        action="add",
        data={
            "name": "Owed loan",
            "kind": "loan",
            "opening_balance_rupees": 350_000,
            "rate_percent": "8.505",
            "tenure_months": 240,
        },
        message_id=None,
    )

    added = await get_account(ledger, result["result"]["id"])
    assert added["balance_paise"] == -35_000_000
    assert added["loan_outstanding_paise"] == 35_000_000
    assert added["rate_bps"] == 851


async def test_a_zero_floor_is_a_valid_setting(ledger):
    result = await write_tools.update_setup_item(
        ledger,
        entity="settings",
        action="update",
        data={"floor_rupees": 0},
        message_id=None,
    )

    assert result["summary"] == "Set the floor to ₹0"
    assert (await ledger.fetchrow("SELECT floor_paise FROM settings WHERE id = 1"))[
        "floor_paise"
    ] == 0
    await undo_group(ledger, result["undo_id"])
    assert (await ledger.fetchrow("SELECT floor_paise FROM settings WHERE id = 1"))[
        "floor_paise"
    ] is None


async def test_update_setup_cannot_silently_change_an_account_kind(ledger):
    await _bank(ledger)

    with pytest.raises(write_tools.ToolInputError, match="kind"):
        await write_tools.update_setup_item(
            ledger,
            entity="account",
            action="update",
            data={"name": "Audit bank", "kind": "card"},
            message_id=None,
        )
    # Echoing the existing kind is harmless and the rest of the update still applies.
    updated = await write_tools.update_setup_item(
        ledger,
        entity="account",
        action="update",
        data={"name": "Audit bank", "kind": "bank", "last4": "4821"},
        message_id=None,
    )
    assert updated["result"]["last4"] == "4821"


async def test_update_setup_cannot_silently_change_a_schedule_kind(ledger):
    bank = await _bank(ledger)
    schedule = await _schedule(ledger, bank)

    with pytest.raises(write_tools.ToolInputError, match="kind"):
        await write_tools.update_setup_item(
            ledger,
            entity="schedule",
            action="update",
            data={"id": schedule["id"], "kind": "income_fixed"},
            message_id=None,
        )

    updated = await write_tools.update_setup_item(
        ledger,
        entity="schedule",
        action="update",
        data={"id": schedule["id"], "kind": "obligation", "amount_rupees": 1500},
        message_id=None,
    )
    assert updated["result"]["amount_paise"] == 150_000


async def test_update_setup_finds_a_payee_by_its_display_name(ledger):
    payee = await create_payee(ledger, display_name="Audit shop", kind="merchant")

    renamed = await write_tools.update_setup_item(
        ledger,
        entity="payee",
        action="update",
        data={"name": "Audit shop", "new_name": "Audit store"},
        message_id=None,
    )
    assert renamed["result"]["id"] == payee["id"]
    assert renamed["result"]["display_name"] == "Audit store"

    archived = await write_tools.update_setup_item(
        ledger,
        entity="payee",
        action="archive",
        data={"name": "Audit store"},
        message_id=None,
    )
    assert archived["result"]["active"] is False


async def test_an_ambiguous_account_name_is_rejected_not_guessed(ledger):
    first = await _bank(ledger, name="Twin bank")
    second = await _bank(ledger, name="Twin bank")

    with pytest.raises(write_tools.ToolInputError, match="more than one"):
        await _tool_record(
            ledger,
            {"kind": "expense", "amount_rupees": 100, "payee": "Shop", "account": "Twin bank"},
            suffix="twin-name",
        )

    by_id = await _tool_record(
        ledger,
        {"kind": "expense", "amount_rupees": 100, "payee": "Shop", "account": second["id"]},
        suffix="twin-id",
    )
    assert by_id["transfers"][0]["from_account_id"] == second["id"]
    assert (await get_account(ledger, first["id"]))["balance_paise"] == 10_000_000


async def test_undoing_an_added_baseline_removes_it(ledger):
    category = "Food & Dining"
    added = await write_tools.update_setup_item(
        ledger,
        entity="baseline",
        action="add",
        data={"category": category, "amount_per_day_rupees": 250},
        message_id=None,
    )
    assert await ledger.fetchval("SELECT amount_per_day_paise FROM baselines") == 25_000

    await undo_group(ledger, added["undo_id"])

    assert await ledger.fetchval("SELECT count(*) FROM baselines") == 0


async def test_undoing_an_added_scheduled_question_archives_it(ledger):
    added = await write_tools.update_setup_item(
        ledger,
        entity="scheduled_question",
        action="add",
        data={"prompt": "Did I overspend on food?", "rule": "FREQ=DAILY"},
        message_id=None,
    )
    question_id = await ledger.fetchval(
        "SELECT id FROM scheduled_questions WHERE prompt = 'Did I overspend on food?'"
    )
    assert await ledger.fetchval(
        "SELECT active FROM scheduled_questions WHERE id = $1", question_id
    )

    await undo_group(ledger, added["undo_id"])

    assert (
        await ledger.fetchval("SELECT active FROM scheduled_questions WHERE id = $1", question_id)
    ) is False
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM job_schedules WHERE active AND scheduled_question_id = $1",
            question_id,
        )
        == 0
    )


async def test_undo_latest_never_reverts_the_system_bootstrap(db):
    from arno.ledger.accounts import ensure_system_accounts
    from arno.ledger.audit import undo_latest
    from arno.ledger.errors import NotFoundLedgerError

    await ensure_system_accounts(db)
    with pytest.raises(NotFoundLedgerError):
        await undo_latest(db)
    assert (
        await db.fetchval(
            "SELECT count(*) FROM accounts WHERE kind IN ('spending','income','equity') AND active"
        )
        == 3
    )

    bank = await create_account(db, kind="bank", name="After bootstrap", opening_balance_paise=1000)
    undone = await undo_latest(db)

    assert undone["status"] == "undone"
    assert (await get_account(db, bank["id"]))["active"] is False
    assert (
        await db.fetchval(
            "SELECT count(*) FROM accounts WHERE kind IN ('spending','income','equity') AND active"
        )
        == 3
    )
