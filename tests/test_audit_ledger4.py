"""Audit wave 4 (ledger4): the facts the read and write paths must state in code.

A person's balance is known by construction like a goal's, a payee that names nobody is never
created, the dates the agent quotes carry their weekday, the context lists open imports, and a
card stated "in credit" is one write with the right sign.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import pytest

from arno.engine.money import money
from arno.ledger import payees as payees_mod
from arno.ledger.accounts import create_account, get_account
from arno.ledger.common import has_known_balance
from arno.ledger.common import today as ledger_today
from arno.ledger.movements import record_split
from arno.ledger.schedules import create_schedule, expand_schedule
from arno.tools import read as read_tools
from arno.tools import write as write_tools


def _context_account(context: dict, account_id: str) -> dict:
    return next(item for item in context["accounts"] if item["id"] == account_id)


async def _bank(conn, name: str = "Audit bank", opening: int = 10_000_000) -> dict:
    return await create_account(conn, kind="bank", name=name, opening_balance_paise=opening)


# 1. A person's balance is the ledger's own receivable -------------------------------------------


async def test_a_person_accounts_balance_is_known_by_construction(ledger):
    person = await payees_mod.create_payee(ledger, display_name="Asha", kind="person")
    row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", person["person_account_id"])
    assert has_known_balance(row) is True

    context = await read_tools.get_financial_context(ledger)
    shown = _context_account(context, person["person_account_id"])
    assert shown["known"] is True
    assert shown["balance"] == money(0)
    assert shown["outstanding"] is None
    assert "an account has no stated balance" not in context["setup_gaps"]
    assert not any("no stated balance" in warning for warning in context["warnings"])

    # What she owes you is the ledger's number, and it is exact as of the day it moved.
    bank = await _bank(ledger)
    await record_split(
        ledger,
        account_id=bank["id"],
        my_share_paise=20_000,
        people=[{"payee_id": person["id"], "amount_paise": 40_000}],
        total_paise=60_000,
        description="Dinner at Barbeque Nation",
        date=ledger_today() - timedelta(days=3),
    )
    context = await read_tools.get_financial_context(ledger)
    shown = _context_account(context, person["person_account_id"])
    assert shown["known"] is True
    assert shown["balance"] == money(40_000)
    assert shown["as_of"] == (ledger_today() - timedelta(days=3)).isoformat()
    assert shown["age_days"] == 3


async def test_the_seeded_persona_has_no_false_missing_balance_gap(demo_ledger):
    """r2 F1: the demo persona's people made "no stated balance" stand for five accounts that are
    anchored at zero. Only the card, whose debt really is unknown, may still be."""
    unknown_people = await demo_ledger.fetchval(
        "SELECT count(*) FROM accounts "
        "WHERE kind = 'person' AND active AND statement_balance_paise IS NULL"
    )
    assert unknown_people > 0

    context = await read_tools.get_financial_context(demo_ledger)

    people = [item for item in context["accounts"] if item["kind"] == "person"]
    assert people
    for person in people:
        assert person["known"] is True
        assert person["balance"] is not None
        assert f"{person['name']} has no stated balance" not in context["warnings"]
    unknown = [item["name"] for item in context["accounts"] if not item["known"]]
    assert unknown == ["Slice card"]


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
        await ledger.fetchval("SELECT count(*) FROM transfer_labels WHERE payee_id IS NOT NULL")
        == 0
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
        "SELECT p.display_name FROM transfer_labels tl JOIN payees p ON p.id = tl.payee_id"
    )
    assert [row["display_name"] for row in labels] == ["Blue Tokai"]


# 3. The weekday a date falls on comes from the tool, never from the model -----------------------


async def test_schedule_and_upcoming_dates_carry_their_weekday(ledger):
    bank = await _bank(ledger)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Flat rent",
        amount_paise=2_800_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2025, 1, 5),
        holiday_adjust="none",
    )

    # A date this project has already had to explain: 5 Nov 2026 is a Thursday.
    row = await ledger.fetchrow("SELECT * FROM schedules WHERE id = $1", schedule["id"])
    events = await expand_schedule(ledger, row, date(2026, 11, 1), date(2026, 11, 30))
    assert [(event["date"], event["weekday"]) for event in events] == [("2026-11-05", "Thursday")]

    context = await read_tools.get_financial_context(ledger)
    shown = next(item for item in context["schedules"] if item["id"] == schedule["id"])
    dates = shown["next_dates"]
    assert len(dates) == 4
    for entry in dates:
        assert set(entry) == {"date", "weekday"}
        assert entry["weekday"] == calendar.day_name[date.fromisoformat(entry["date"]).weekday()]
    assert all(entry["date"].endswith("-05") for entry in dates)

    upcoming = [event for event in context["upcoming"] if event["schedule_id"] == schedule["id"]]
    assert upcoming
    for event in upcoming:
        assert event["weekday"] == calendar.day_name[date.fromisoformat(event["date"]).weekday()]


# 4. The context names every open import, so the agent can read before it says -------------------


async def test_the_context_lists_open_imports_and_nothing_else(ledger):
    bank = await _bank(ledger)
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             check_result, counts)
        VALUES ('imp_review', 'h1', 'review', $1, '2026-10-01', '2026-10-31',
                '{"status": "VERIFIED"}'::jsonb, '{"new": 2, "certain": 1}'::jsonb)
        """,
        bank["id"],
    )
    await ledger.execute(
        "INSERT INTO imports (id, file_hash, status, reason) "
        "VALUES ('imp_failed', 'h2', 'failed', 'could not open the file: corrupt')"
    )
    for status in ("committed", "rejected"):
        await ledger.execute(
            "INSERT INTO imports (id, file_hash, status) VALUES ($1, $2, $3)",
            f"imp_{status}",
            f"h-{status}",
            status,
        )
    for line_id in ("stl_a", "stl_b", "stl_c"):
        await ledger.execute(
            """
            INSERT INTO statement_lines (id, import_id, date, description_raw, amount_paise,
                                         content_hash)
            VALUES ($1, 'imp_review', '2026-10-02', 'UPI/DR/SHOP', -1000, $1)
            """,
            line_id,
        )

    context = await read_tools.get_financial_context(ledger)
    items = {item["id"]: item for item in context["imports"]}

    assert set(items) == {"imp_review", "imp_failed"}
    review = items["imp_review"]
    assert review["status"] == "review"
    assert review["account"] == bank["name"]
    assert review["period"] == {"start": "2026-10-01", "end": "2026-10-31"}
    assert review["lines"] == 3
    assert review["counts"] == {"new": 2, "certain": 1}
    assert review["check"] == "VERIFIED"
    failed = items["imp_failed"]
    assert failed["status"] == "failed"
    assert failed["account"] is None
    assert failed["period"] is None
    assert failed["lines"] == 0


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
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM audit_log WHERE group_id = $1", result["undo_id"]
        )
        > 0
    )


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
