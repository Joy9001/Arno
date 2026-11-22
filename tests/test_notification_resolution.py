"""Payment evidence resolves its warning, and undo restores the prior state (C18 box 2)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from arno.jobs.patterns import run_pattern_detection
from arno.jobs.reminders import send_reminders
from arno.ledger.accounts import create_account
from arno.ledger.audit import undo_group
from arno.ledger.common import today as ledger_today
from arno.ledger.errors import InvalidLedgerError
from arno.ledger.movements import record_expense, record_transfer
from arno.ledger.schedule_payments import link_payment
from arno.ledger.schedules import create_schedule
from tests.invariants import check_ledger


async def _schedule(conn, *, due, name="Rent", amount_paise=2_500_000):
    bank = await create_account(
        conn, kind="bank", name=f"{name} bank", opening_balance_paise=10_000_000
    )
    schedule = await create_schedule(
        conn,
        kind="obligation",
        subtype="rent",
        name=name,
        amount_paise=amount_paise,
        account_id=bank["id"],
        rule="FREQ=DAILY",
        starts=due,
        ends=due,
    )
    return bank, schedule


async def _reminder(conn, schedule, *, due):
    rows = await send_reminders(conn, as_of=ledger_today(), lead_days=3)
    return next(
        row
        for row in rows
        if row["kind"] == "reminder"
        and row["data"]["source_id"] == schedule["id"]
        and row["data"]["date"] == due.isoformat()
    )


async def _missed(conn, schedule, *, as_of):
    rows = await run_pattern_detection(conn, as_of=as_of)
    return next(
        row
        for row in rows
        if row["kind"] == "missed_recurring" and row["data"]["schedule_id"] == schedule["id"]
    )


async def _state(conn, notification_id):
    return await conn.fetchrow(
        "SELECT resolved_at, resolved_by FROM notifications WHERE id = $1", notification_id
    )


async def test_a_linked_payment_resolves_its_reminder_and_undoing_the_link_reopens_it(ledger):
    due = ledger_today() + timedelta(days=2)
    bank, schedule = await _schedule(ledger, due=due)
    reminder = await _reminder(ledger, schedule, due=due)
    assert reminder["resolved_at"] is None

    paid = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=2_500_000,
        description="Rent",
        date=ledger_today(),
    )
    linked = await link_payment(
        ledger, schedule_id=schedule["id"], due_date=due, movement_id=paid["id"]
    )

    resolved = await _state(ledger, reminder["id"])
    assert resolved["resolved_at"] is not None
    assert resolved["resolved_by"] == paid["id"]
    # The resolution is one entry in the link's audited write group, so the existing undo has
    # to restore it.
    assert (
        await ledger.fetchval(
            "SELECT group_id FROM audit_log WHERE table_name = 'notifications' AND row_id = $1",
            reminder["id"],
        )
        == linked["undo_id"]
    )

    undone = await undo_group(ledger, linked["undo_id"])

    restored = await _state(ledger, reminder["id"])
    assert undone["status"] == "undone"
    assert restored["resolved_at"] is None
    assert restored["resolved_by"] is None
    assert await check_ledger(ledger) == []


async def test_undoing_the_movement_itself_reopens_the_warning_it_resolved(ledger):
    due = ledger_today() - timedelta(days=10)
    bank, schedule = await _schedule(ledger, due=due)
    missed = await _missed(ledger, schedule, as_of=ledger_today())
    assert missed["resolved_at"] is None

    paid = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=2_500_000,
        description="Rent",
        date=due,
    )
    await link_payment(ledger, schedule_id=schedule["id"], due_date=due, movement_id=paid["id"])

    resolved = await _state(ledger, missed["id"])
    assert resolved["resolved_at"] is not None
    assert resolved["resolved_by"] == paid["id"]

    # Undo the movement's own group, not the link's: the reversal has to restore the warning.
    undone = await undo_group(ledger, paid["undo_id"])

    restored = await _state(ledger, missed["id"])
    assert restored["resolved_at"] is None
    assert restored["resolved_by"] is None

    # Undo of that undo makes the original payment effective again, so the warning clears again.
    await undo_group(ledger, undone["undo_undo_id"])

    redone = await _state(ledger, missed["id"])
    assert redone["resolved_at"] is not None
    assert redone["resolved_by"] == paid["id"]
    assert await check_ledger(ledger) == []


async def test_a_payment_the_schedule_does_not_match_leaves_the_reminder_open(ledger):
    due = ledger_today() + timedelta(days=2)
    bank, schedule = await _schedule(ledger, due=due)
    reminder = await _reminder(ledger, schedule, due=due)

    other = await create_account(
        ledger, kind="bank", name="Another bank", opening_balance_paise=10_000_000
    )
    wrong_account = await record_expense(
        ledger,
        account_id=other["id"],
        amount_paise=2_500_000,
        description="Rent from the wrong account",
        date=ledger_today(),
    )
    with pytest.raises(InvalidLedgerError):
        await link_payment(
            ledger,
            schedule_id=schedule["id"],
            due_date=due,
            movement_id=wrong_account["id"],
        )

    assert (await _state(ledger, reminder["id"]))["resolved_at"] is None

    # A real but partial payment is matching evidence, yet it does not cover the reminder's amount.
    partial = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=1_000_000,
        description="Part of the rent",
        date=ledger_today(),
    )
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=due,
        movement_id=partial["id"],
        amount_paise=1_000_000,
    )

    assert (await _state(ledger, reminder["id"]))["resolved_at"] is None
    assert await check_ledger(ledger) == []


async def test_an_elapsed_due_date_alone_never_resolves_an_alert(ledger):
    due = ledger_today() + timedelta(days=2)
    _bank, schedule = await _schedule(ledger, due=due)
    reminder = await _reminder(ledger, schedule, due=due)

    later = due + timedelta(days=5)
    await send_reminders(ledger, as_of=later, lead_days=3)
    missed = await _missed(ledger, schedule, as_of=later)

    assert (await _state(ledger, reminder["id"]))["resolved_at"] is None
    assert (await _state(ledger, missed["id"]))["resolved_at"] is None
    assert await ledger.fetchval("SELECT count(*) FROM notifications") == 2
    assert await check_ledger(ledger) == []


# --- A card bill has no schedule: its reminder is keyed by the card's account id -----------


async def _card(conn, *, owed_paise=5_000_000):
    due = ledger_today() + timedelta(days=2)
    return await create_account(
        conn,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=1,
        due_day=due.day,
        opening_balance_paise=-owed_paise,
    )


async def _paying_bank(conn):
    return await create_account(
        conn, kind="bank", name="Paying bank", opening_balance_paise=10_000_000
    )


async def _card_reminder(conn, card):
    rows = await send_reminders(conn, as_of=ledger_today(), lead_days=7)
    return next(
        row
        for row in rows
        if row["kind"] == "reminder"
        and row["data"]["source_id"] == card["id"]
        and row["data"]["kind"] == "card_due"
    )


async def test_paying_the_card_bill_resolves_its_reminder_and_undoing_the_payment_reopens_it(
    ledger,
):
    card = await _card(ledger)
    bank = await _paying_bank(ledger)
    reminder = await _card_reminder(ledger, card)
    assert reminder["resolved_at"] is None

    paid = await record_transfer(
        ledger,
        from_account_id=bank["id"],
        to_account_id=card["id"],
        amount_paise=5_000_000,
        description="Card bill",
        date=ledger_today(),
    )

    resolved = await _state(ledger, reminder["id"])
    assert resolved["resolved_at"] is not None
    assert resolved["resolved_by"] == paid["id"]
    # The resolution shares the payment's write group, so its own undo restores it.
    assert (
        await ledger.fetchval(
            "SELECT group_id FROM audit_log WHERE table_name = 'notifications' AND row_id = $1",
            reminder["id"],
        )
        == paid["undo_id"]
    )
    assert await check_ledger(ledger) == []

    undone = await undo_group(ledger, paid["undo_id"])

    restored = await _state(ledger, reminder["id"])
    assert undone["status"] == "undone"
    assert restored["resolved_at"] is None
    assert restored["resolved_by"] is None
    assert await check_ledger(ledger) == []


async def test_a_partial_card_payment_leaves_the_bill_reminder_open(ledger):
    card = await _card(ledger)
    bank = await _paying_bank(ledger)
    reminder = await _card_reminder(ledger, card)

    await record_transfer(
        ledger,
        from_account_id=bank["id"],
        to_account_id=card["id"],
        amount_paise=2_000_000,
        description="Part of the card bill",
        date=ledger_today(),
    )

    assert (await _state(ledger, reminder["id"]))["resolved_at"] is None
    assert await check_ledger(ledger) == []


async def test_an_elapsed_card_due_date_alone_never_resolves_the_bill(ledger):
    card = await _card(ledger)
    reminder = await _card_reminder(ledger, card)

    await send_reminders(ledger, as_of=ledger_today() + timedelta(days=10), lead_days=7)

    assert (await _state(ledger, reminder["id"]))["resolved_at"] is None
    assert await check_ledger(ledger) == []


async def test_the_notifications_api_hides_resolved_items_and_reports_their_evidence(
    session_client, ledger
):
    due = ledger_today() + timedelta(days=2)
    bank, schedule = await _schedule(ledger, due=due)
    reminder = await _reminder(ledger, schedule, due=due)

    page = session_client.get("/api/notifications")
    assert page.status_code == 200
    open_item = next(row for row in page.json()["notifications"] if row["id"] == reminder["id"])
    assert open_item["resolved_at"] is None
    assert open_item["resolved_by"] is None
    assert session_client.get("/api/home").json()["needs_you"][0]["id"] == reminder["id"]

    paid = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=2_500_000,
        description="Rent",
        date=ledger_today(),
    )
    linked = await link_payment(
        ledger, schedule_id=schedule["id"], due_date=due, movement_id=paid["id"]
    )

    open_page = session_client.get("/api/notifications").json()
    assert all(row["id"] != reminder["id"] for row in open_page["notifications"])
    assert open_page["unread_count"] == 0
    assert session_client.get("/api/home").json()["needs_you"] == []

    history = session_client.get("/api/notifications?include_resolved=true").json()
    resolved_item = next(row for row in history["notifications"] if row["id"] == reminder["id"])
    assert resolved_item["resolved_at"] is not None
    assert resolved_item["resolved_by"] == paid["id"]

    undone = session_client.post(f"/api/undo/{linked['undo_id']}")
    assert undone.status_code == 200, undone.text
    reopened = next(
        row
        for row in session_client.get("/api/notifications").json()["notifications"]
        if row["id"] == reminder["id"]
    )
    assert reopened["resolved_at"] is None
    assert reopened["resolved_by"] is None
