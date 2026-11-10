"""Wave 3 ledger fixes: the money paths the end-to-end journeys contradicted.

Each test names the finding it closes. The two P0s are a double reversal and a card bill whose
amount was never established; the rest keep one quantity computed along one rule.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from fin_agent.jobs.reminders import send_reminders
from fin_agent.ledger.accounts import create_account, get_account, update_account
from fin_agent.ledger.audit import undo_group
from fin_agent.ledger.common import today
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError
from fin_agent.ledger.goals import create_goal, get_goal
from fin_agent.ledger.movements import (
    correct_movement,
    get_movement,
    loan_payment_parts,
    record_expense,
    record_split,
)
from fin_agent.ledger.payees import create_payee
from fin_agent.ledger.schedule_payments import link_payment
from fin_agent.ledger.schedules import create_schedule, get_occurrence, get_schedule, upcoming
from fin_agent.tools import write as write_tools
from tests.invariants import check_ledger


async def _bank(conn, *, name="Ledger3 bank", opening=10_000_000):
    return await create_account(conn, kind="bank", name=name, opening_balance_paise=opening)


async def _category(conn, name):
    return await conn.fetchval("SELECT id FROM categories WHERE name = $1 AND active", name)


def _clean(violations):
    assert violations == [], "\n".join(
        f"{violation.code}: {violation.detail} ids={violation.ids}" for violation in violations
    )


# --- P0: undo of a create group after a correction (e2 F-1) ------------------------------------


async def test_undoing_a_created_movement_after_a_correction_is_refused(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    created = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=45_000,
        description="Test Cafe",
        category_id=food,
    )
    corrected = await correct_movement(ledger, created["id"], changes={"amount_paise": 40_500})

    with pytest.raises(ConflictLedgerError, match="already corrected or removed"):
        await undo_group(ledger, created["undo_id"])

    # Nothing moved, and the expense still has exactly one reversal child.
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000 - 40_500
    children = await ledger.fetchval(
        "SELECT count(*) FROM movements WHERE reverses_id = $1", created["id"]
    )
    assert children == 1
    _clean(await check_ledger(ledger))

    # Undo the correction, then the create group works once and only once.
    await undo_group(ledger, corrected["undo_id"])
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000 - 45_000
    undone = await undo_group(ledger, created["undo_id"])
    assert undone["status"] == "undone"
    assert (await get_account(ledger, bank["id"]))["balance_paise"] == 10_000_000
    again = await undo_group(ledger, created["undo_id"])
    assert again["status"] == "already_undone"
    _clean(await check_ledger(ledger))


async def test_the_undo_tool_reports_the_guard_to_the_model(ledger):
    bank = await _bank(ledger)
    created = await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 450, "payee": "Cafe", "account": bank["name"]},
        message_id=None,
        conversation_id=None,
        not_duplicate=True,
    )
    await write_tools.correct_movement(
        ledger, movement_id=created["movement_id"], changes={"amount_rupees": 405}, message_id=None
    )

    with pytest.raises(write_tools.ToolInputError, match="already corrected or removed"):
        await write_tools.undo(
            ledger, undo_id=created["undo_id"], conversation_id=None, message_id=None
        )


# --- P0: an unknown card's bill amount (e1 F1) -------------------------------------------------


async def test_an_unknown_card_bill_has_no_certain_amount(ledger):
    food = await _category(ledger, "Food & Dining")
    unknown = await create_account(
        ledger,
        kind="card",
        name="Unknown card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    await record_expense(
        ledger,
        account_id=unknown["id"],
        amount_paise=300_000,
        description="Dinner",
        category_id=food,
    )

    events = (await upcoming(ledger, days=60, start=date(2026, 11, 1)))["events"]
    due = [event for event in events if event["kind"] == "card_due"]

    assert due[0]["date"] == "2026-11-25"
    assert all(event["amount_paise"] is None for event in due)
    assert all(event["remaining_paise"] is None for event in due)
    assert all("no statement balance" in event["note"] for event in due)
    assert all("unknown" in event["note"] for event in due)

    # The reminder says so too, instead of announcing a bill nobody established.
    notes = await send_reminders(ledger, as_of=date(2026, 11, 1), lead_days=30)
    bodies = [row["body"] for row in notes if row["data"]["name"] == "Unknown card bill due"]
    assert bodies == ["Due 2026-11-25. The amount is unknown."]


async def test_a_known_card_bill_still_carries_its_amount(ledger):
    food = await _category(ledger, "Food & Dining")
    known = await create_account(
        ledger,
        kind="card",
        name="Known card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
        opening_balance_paise=0,
    )
    await record_expense(
        ledger, account_id=known["id"], amount_paise=300_000, description="Dinner", category_id=food
    )

    events = (await upcoming(ledger, days=60, start=date(2026, 11, 1)))["events"]
    due = [event for event in events if event["kind"] == "card_due"]
    assert due[0]["amount_paise"] == 300_000
    assert due[0]["remaining_paise"] == 300_000
    assert due[0]["note"] is None


async def test_the_briefing_lists_an_unknown_bill_and_keeps_it_out_of_the_total(ledger):
    from fin_agent.jobs.briefing import compose_briefing

    food = await _category(ledger, "Food & Dining")
    unknown = await create_account(
        ledger,
        kind="card",
        name="Briefing card",
        credit_limit_paise=10_000_000,
        statement_day=25,
        due_day=5,
    )
    await record_expense(
        ledger,
        account_id=unknown["id"],
        amount_paise=300_000,
        description="Dinner",
        category_id=food,
    )

    payload = await compose_briefing(ledger, as_of=date(2026, 11, 1))
    bills = payload["numbers"]["bills"]

    assert bills["count"] == 1
    assert bills["unknown_count"] == 1
    assert bills["total_paise"] == 0
    assert bills["items"][0]["amount"] is None
    assert "1 with an unknown amount" in payload["template"]


# --- next_date is not a paid occurrence (e4 F3) -------------------------------------------------


async def test_next_date_skips_an_occurrence_that_is_already_paid(ledger):
    bank = await _bank(ledger)
    day = today()
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Monthly bill",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY",
        starts=day,
    )
    paid = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Bill paid"
    )
    await link_payment(ledger, schedule_id=schedule["id"], due_date=day, movement_id=paid["id"])
    assert (await get_occurrence(ledger, schedule["id"], day))["payment_status"] == "paid"

    payload = await get_schedule(ledger, schedule["id"])
    open_dates = [
        event["date"]
        for event in (await upcoming(ledger, days=70, start=day))["events"]
        if event["schedule_id"] == schedule["id"]
        and (event["remaining_paise"] is None or event["remaining_paise"] > 0)
    ]
    assert payload["next_date"] == open_dates[0]
    assert payload["next_date"] != day.isoformat()


# --- one month's interest per loan (e3 F3) ------------------------------------------------------


async def _loan(conn):
    bank = await _bank(conn, name="Loan bank", opening=20_000_000)
    loan = await create_account(
        conn,
        kind="loan",
        name="Rule loan",
        opening_balance_paise=-50_000_000,
        rate_bps=850,
        tenure_months=240,
    )
    return bank, loan


async def _pay_loan(conn, amount_rupees):
    return await write_tools.record_movement(
        conn,
        {
            "kind": "loan",
            "amount_rupees": amount_rupees,
            "loan": "Rule loan",
            "account": "Loan bank",
        },
        message_id=None,
        conversation_id=None,
    )


async def _interest_legs(conn, movement_id):
    rows = await conn.fetch(
        """
        SELECT t.amount::bigint AS amount_paise
        FROM pgledger_transfers t
        JOIN accounts a ON a.ledger_account_id = t.to_account_id
        WHERE t.metadata ->> 'movement_id' = $1 AND a.kind = 'spending'
        ORDER BY t.id
        """,
        movement_id,
    )
    return [int(row["amount_paise"]) for row in rows]


def _month_interest(outstanding_paise: int) -> int:
    monthly = Decimal(850) / Decimal(10000) / Decimal(12)
    return int((Decimal(outstanding_paise) * monthly).quantize(Decimal("1")))


async def test_only_the_first_loan_payment_in_a_month_books_interest(ledger):
    await _loan(ledger)
    first = await _pay_loan(ledger, 31_500)
    assert first["interest_paise"] == _month_interest(50_000_000)
    assert first["principal_paise"] == 3_150_000 - _month_interest(50_000_000)

    second = await _pay_loan(ledger, 10_000)
    assert second["interest_paise"] == 0
    assert second["principal_paise"] == 1_000_000
    assert await _interest_legs(ledger, second["movement_id"]) == []

    # The month's interest is booked once, so the category total is one month's worth.
    assert await ledger.fetchval(
        """
        SELECT coalesce(sum(t.amount), 0)::bigint FROM pgledger_transfers t
        JOIN accounts a ON a.ledger_account_id = t.to_account_id
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE a.kind = 'spending' AND m.kind = 'loan'
        """
    ) == _month_interest(50_000_000)

    # A payment in the next calendar month books that month's interest again.
    next_month = today().replace(day=1) + timedelta(days=35)
    loan_row = await ledger.fetchrow("SELECT * FROM accounts WHERE name = 'Rule loan'")
    principal, interest = await loan_payment_parts(ledger, loan_row, 1_000_000, on=next_month)
    assert interest > 0
    assert principal + interest == 1_000_000


async def test_corrections_use_the_same_once_a_month_rule(ledger):
    await _loan(ledger)
    first = await _pay_loan(ledger, 31_500)
    second = await _pay_loan(ledger, 10_000)
    assert await _interest_legs(ledger, second["movement_id"]) == []

    # Correcting the month's first payment still books the month's interest once, on the
    # outstanding the other live payment leaves.
    corrected = await write_tools.correct_movement(
        ledger, movement_id=first["movement_id"], changes={"amount_rupees": 41_500}, message_id=None
    )
    assert await _interest_legs(ledger, corrected["movement"]["id"]) == [
        _month_interest(50_000_000 - 1_000_000)
    ]

    # A correction of a later payment in the same month stays all principal.
    corrected_later = await write_tools.correct_movement(
        ledger,
        movement_id=second["movement_id"],
        changes={"amount_rupees": 15_000},
        message_id=None,
    )
    assert await _interest_legs(ledger, corrected_later["movement"]["id"]) == []
    _clean(await check_ledger(ledger))


# --- a date-only correction counts its row (e2 F-3) ---------------------------------------------


async def test_a_date_only_correction_reports_the_row_it_changed(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    movement = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=40_500,
        description="Last month",
        category_id=food,
        date=date(2026, 10, 31),
    )

    only_date = await correct_movement(ledger, movement["id"], changes={"date": date(2026, 11, 1)})
    assert only_date["rows_changed"] == 1
    assert only_date["movement"]["date"] == "2026-11-01"

    both = await correct_movement(
        ledger,
        movement["id"],
        changes={"date": date(2026, 11, 2), "note": "moved again"},
    )
    assert both["rows_changed"] == 2


# --- a split's payee is the vendor, never a person (e3 F11) --------------------------------------


async def test_a_split_reports_its_vendor_as_the_payee(ledger):
    bank = await _bank(ledger)
    vendor = await create_payee(ledger, display_name="Zomato split", kind="merchant")
    asha = await create_payee(ledger, display_name="Asha split", kind="person")
    kabir = await create_payee(ledger, display_name="Kabir split", kind="person")

    with_vendor = await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=33_400,
        total_paise=100_000,
        people=[
            {"payee_id": asha["id"], "amount_paise": 33_300},
            {"payee_id": kabir["id"], "amount_paise": 33_300},
        ],
        payee_id=vendor["id"],
        description="Dinner",
    )
    assert (await get_movement(ledger, with_vendor["id"]))["payee_name"] == "Zomato split"

    # With no vendor named, a person's leg must not read as "paid to Asha".
    without_vendor = await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=50_000,
        total_paise=100_000,
        people=[{"payee_id": asha["id"], "amount_paise": 50_000}],
        description="Lunch",
    )
    assert (await get_movement(ledger, without_vendor["id"]))["payee_name"] is None


# --- a category name is never a payee (e2 F-5) --------------------------------------------------


async def test_a_payee_named_after_a_category_is_not_stored(ledger):
    bank = await _bank(ledger)
    result = await write_tools.record_movement(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": 1500,
            "payee": "Groceries",
            "account": bank["name"],
            "category_guess": "Groceries",
            "description": "Weekly groceries",
        },
        message_id=None,
        conversation_id=None,
        not_duplicate=True,
    )
    assert result["status"] == "recorded"
    label = await ledger.fetchrow(
        "SELECT payee_id, category_id FROM transfer_labels WHERE movement_id = $1",
        result["movement_id"],
    )
    assert label["payee_id"] is None
    assert label["category_id"] == await _category(ledger, "Groceries")
    assert (
        await ledger.fetchval("SELECT count(*) FROM payees WHERE lower(display_name) = 'groceries'")
        == 0
    )

    # A real merchant still becomes a payee.
    await write_tools.record_movement(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": 50,
            "payee": "Chai Stall",
            "account": bank["name"],
        },
        message_id=None,
        conversation_id=None,
        not_duplicate=True,
    )
    assert (
        await ledger.fetchval("SELECT count(*) FROM payees WHERE display_name = 'Chai Stall'") == 1
    )


# --- a transfer into a goal is a contribution (e3 F12) ------------------------------------------


async def test_a_plain_transfer_into_a_goal_is_labelled_as_saving(ledger):
    bank = await _bank(ledger)
    goal = await create_goal(ledger, name="Ledger3 trip", target_paise=1_000_000)
    account_name = await ledger.fetchval(
        "SELECT name FROM accounts WHERE id = $1", goal["account_id"]
    )

    result = await write_tools.record_movement(
        ledger,
        {
            "kind": "transfer",
            "amount_rupees": 5000,
            "from_account": bank["name"],
            "to_account": account_name,
        },
        message_id=None,
        conversation_id=None,
    )

    label = await ledger.fetchrow(
        "SELECT category_id FROM transfer_labels WHERE movement_id = $1", result["movement_id"]
    )
    assert label["category_id"] == await _category(ledger, "Savings & Investments")
    assert result["summary"] == f"Contribution to {account_name}"
    assert (await get_goal(ledger, goal["id"]))["saved_paise"] == 500_000


# --- a card purchase names the bill it lands on (e6 F4) -----------------------------------------


async def test_a_card_purchase_carries_the_engines_bill_date(ledger):
    card = await create_account(
        ledger,
        kind="card",
        name="Bill card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    result = await write_tools.record_movement(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": 800,
            "payee": "Croma",
            "account": "Bill card",
            "category": "Shopping",
            "date": "2026-10-10",
        },
        message_id=None,
        conversation_id=None,
        not_duplicate=True,
    )

    # Spend on 10 Oct closes on 5 Nov and is due on 25 Nov.
    assert result["card_bill"] == {"statement_close": "2026-11-05", "due_date": "2026-11-25"}
    assert result["card"]["card_bill"] == result["card_bill"]
    assert "bill due 2026-11-25" in result["summary"]
    assert (await get_account(ledger, card["id"]))["balance_paise"] == -80_000


# --- a payment cannot cover an occurrence over a month ahead (e2 Q-3) ----------------------------


async def test_a_payment_more_than_31_days_early_is_refused_with_the_nearest_open_date(ledger):
    bank = await _bank(ledger)
    day = today()
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Daily plan",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=DAILY;COUNT=90",
        starts=day - timedelta(days=30),
    )
    movement = await record_expense(
        ledger, account_id=bank["id"], amount_paise=100_000, description="Paid today"
    )

    with pytest.raises(InvalidLedgerError, match=f"Nearest open occurrence: {day.isoformat()}"):
        await link_payment(
            ledger,
            schedule_id=schedule["id"],
            due_date=day + timedelta(days=35),
            movement_id=movement["id"],
        )
    assert await ledger.fetchval("SELECT count(*) FROM schedule_payments") == 0

    # The occurrence the payment can honestly cover still links.
    linked = await link_payment(
        ledger, schedule_id=schedule["id"], due_date=day, movement_id=movement["id"]
    )
    assert linked["status"] == "linked"


# --- an archived account says so (e1 F8) --------------------------------------------------------


async def test_writing_from_an_archived_account_says_it_is_archived(ledger):
    bank = await _bank(ledger, name="Archived bank")
    await update_account(ledger, bank["id"], active=False)

    with pytest.raises(write_tools.ToolInputError) as raised:
        await write_tools.record_movement(
            ledger,
            {"kind": "expense", "amount_rupees": 100, "payee": "Tea", "account": "Archived bank"},
            message_id=None,
            conversation_id=None,
            not_duplicate=True,
        )
    message = str(raised.value)
    assert "Archived bank is archived" in message
    assert "reactivate" in message
    assert "No account named" not in message


# --- the undo card carries the undo-of-undo id (e1 F9) -------------------------------------------


async def test_the_undo_card_carries_the_undo_of_undo_id(ledger):
    bank = await _bank(ledger)
    recorded = await write_tools.record_movement(
        ledger,
        {"kind": "expense", "amount_rupees": 100, "payee": "Tea", "account": bank["name"]},
        message_id=None,
        conversation_id=None,
        not_duplicate=True,
    )

    undone = await write_tools.undo(
        ledger, undo_id=recorded["undo_id"], conversation_id=None, message_id=None
    )
    assert undone["undo_undo_id"]
    assert undone["card"]["undo_undo_id"] == undone["undo_undo_id"]
