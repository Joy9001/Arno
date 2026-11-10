from __future__ import annotations

from datetime import date

import pytest

from arno.ledger.accounts import (
    account_summary,
    archive_account,
    create_account,
    get_account,
    list_accounts,
    set_statement_balance,
    update_account,
)
from arno.ledger.audit import undo_group
from arno.ledger.errors import InvalidLedgerError


async def _card(ledger, name="Slice card", **kwargs):
    return await create_account(
        ledger,
        kind="card",
        name=name,
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
        **kwargs,
    )


async def test_system_accounts_are_single_and_not_in_the_panel(ledger):
    accounts = await list_accounts(ledger, include_system=True)
    system = [
        account for account in accounts if account["kind"] in {"spending", "income", "equity"}
    ]

    assert {account["kind"] for account in system} == {"spending", "income", "equity"}
    assert len(system) == 3
    assert all(account["name"] in {"Spending", "Income", "Equity"} for account in system)
    assert await list_accounts(ledger) == []


async def test_bank_opening_balance_is_an_adjustment_not_spending(ledger):
    account = await create_account(
        ledger,
        kind="bank",
        name="HDFC Savings",
        institution="HDFC Bank",
        last4="4821",
        opening_balance_paise=18_500_000,
        opening_balance_as_of=date(2026, 11, 1),
    )

    assert account["balance_paise"] == 18_500_000
    assert account["statement_balance_paise"] == 18_500_000
    assert account["statement_balance_as_of"] == "2026-11-01"
    assert account["liquid"] is True
    spending = await ledger.fetchrow("SELECT coalesce(sum(amount_paise), 0) AS n FROM v_spending")
    assert spending["n"] == 0


async def test_card_cycle_limit_and_available_credit(ledger):
    bank = await create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    card = await _card(ledger, last4="2207", opening_balance_paise=0)
    assert card["credit_limit_paise"] == 10_000_000
    assert card["card_outstanding_paise"] == 0
    assert card["available_credit_paise"] == 10_000_000

    from arno.ledger.movements import record_expense

    # Move a card purchase through the ledger; the card balance becomes a liability.
    category = (await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Shopping'"))["id"]
    await record_expense(
        ledger,
        account_id=card["id"],
        amount_paise=3_000_000,
        description="Headphones",
        category_id=category,
    )
    refreshed = await get_account(ledger, card["id"])
    assert refreshed["balance_paise"] == -3_000_000
    assert refreshed["card_outstanding_paise"] == 3_000_000
    assert refreshed["available_credit_paise"] == 7_000_000
    assert bank["balance_paise"] == 10_000_000


async def test_unknown_card_does_not_claim_zero_debt_or_full_available_credit(ledger):
    card = await _card(ledger)

    assert card["balance_paise"] == 0
    assert card["statement_balance_paise"] is None
    assert card["card_outstanding_paise"] is None
    assert card["available_credit_paise"] is None
    assert card["credit_limit_paise"] == 10_000_000
    assert card["statement_day"] == 5
    assert card["due_day"] == 25
    assert (await account_summary(ledger))["card_owed_paise"] is None


async def test_a_card_movement_alone_leaves_the_outstanding_unknown(ledger):
    from arno.ledger.movements import record_expense

    card = await _card(ledger)
    await record_expense(
        ledger, account_id=card["id"], amount_paise=3_000_000, description="Headphones"
    )

    refreshed = await get_account(ledger, card["id"])
    assert refreshed["balance_paise"] == -3_000_000
    assert refreshed["card_outstanding_paise"] is None
    assert refreshed["available_credit_paise"] is None
    assert (await account_summary(ledger))["card_owed_paise"] is None


async def test_a_known_zero_card_keeps_numeric_zero_and_full_available_credit(ledger):
    card = await _card(ledger, opening_balance_paise=0)

    assert card["card_outstanding_paise"] == 0
    assert card["available_credit_paise"] == 10_000_000
    assert (await account_summary(ledger))["card_owed_paise"] == 0


async def test_a_known_card_debt_reduces_available_credit(ledger):
    card = await _card(ledger, opening_balance_paise=-3_000_000)

    assert card["card_outstanding_paise"] == 3_000_000
    assert card["available_credit_paise"] == 7_000_000
    assert (await account_summary(ledger))["card_owed_paise"] == 3_000_000


async def test_mixed_known_and_unknown_cards_make_the_headline_unknown(ledger):
    known = await _card(ledger, name="Known card", opening_balance_paise=0)
    await _card(ledger, name="Unknown card")

    summary = await account_summary(ledger)

    assert summary["card_owed_paise"] is None
    rows = {account["name"]: account for account in summary["accounts"]}
    assert rows["Known card"]["card_outstanding_paise"] == 0
    assert rows["Known card"]["available_credit_paise"] == known["credit_limit_paise"]
    assert rows["Unknown card"]["card_outstanding_paise"] is None
    assert rows["Unknown card"]["available_credit_paise"] is None


async def test_loan_balance_is_outstanding_principal(ledger):
    loan = await create_account(
        ledger,
        kind="loan",
        name="Home loan",
        opening_balance_paise=-350_000_000,
        rate_bps=850,
        tenure_months=240,
    )

    assert loan["balance_paise"] == -350_000_000
    assert loan["loan_outstanding_paise"] == 350_000_000
    assert loan["rate_bps"] == 850
    assert loan["tenure_months"] == 240


async def test_card_fields_are_required_and_only_for_a_card(ledger):
    with pytest.raises(InvalidLedgerError, match="credit limit"):
        await create_account(ledger, kind="card", name="Broken card", statement_day=5, due_day=25)
    with pytest.raises(InvalidLedgerError, match="belong only to a card"):
        await create_account(ledger, kind="bank", name="Odd", credit_limit_paise=1000)


async def test_card_missing_fields_are_reported_together_and_write_nothing(ledger):
    with pytest.raises(InvalidLedgerError) as caught:
        await create_account(ledger, kind="card", name="Slice")

    message = caught.value.message
    assert "credit limit" in message
    assert "statement day" in message
    assert "due day" in message
    assert await list_accounts(ledger) == []
    assert (await ledger.fetchrow("SELECT count(*) AS n FROM accounts WHERE name = 'Slice'"))[
        "n"
    ] == 0


async def test_card_invalid_ranges_are_reported_together(ledger):
    with pytest.raises(InvalidLedgerError) as caught:
        await create_account(
            ledger,
            kind="card",
            name="Slice",
            credit_limit_paise=0,
            statement_day=40,
            due_day=0,
        )

    message = caught.value.message
    assert "zero or negative" in message
    assert "not 40" in message
    assert "not 0" in message
    assert await list_accounts(ledger) == []


async def test_card_partial_update_changes_only_the_given_field(ledger):
    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )

    updated = await update_account(ledger, card["id"], due_day=28)

    assert updated["due_day"] == 28
    assert updated["statement_day"] == 5
    assert updated["credit_limit_paise"] == 10_000_000

    with pytest.raises(InvalidLedgerError, match="due day"):
        await update_account(ledger, card["id"], due_day=32)
    assert (await get_account(ledger, card["id"]))["due_day"] == 28


async def test_liquid_total_quality_separates_unknown_zero_and_empty(ledger):
    assert (await account_summary(ledger))["cash_quality"]["status"] == "no_accounts"

    unknown = await create_account(ledger, kind="bank", name="SBI Savings")
    quality = (await account_summary(ledger))["cash_quality"]
    assert quality == {
        "status": "unknown",
        "known_paise": 0,
        "known_count": 0,
        "unknown_count": 1,
        "unknown_accounts": ["SBI Savings"],
    }

    await set_statement_balance(ledger, unknown["id"], balance_paise=0)
    quality = (await account_summary(ledger))["cash_quality"]
    assert quality["status"] == "known"
    assert quality["known_paise"] == 0
    assert quality["known_count"] == 1
    assert quality["unknown_count"] == 0

    await create_account(ledger, kind="bank", name="HDFC Savings", opening_balance_paise=2_500_000)
    quality = (await account_summary(ledger))["cash_quality"]
    assert quality["status"] == "known"
    assert quality["known_paise"] == 2_500_000
    assert quality["known_count"] == 2
    assert quality["unknown_accounts"] == []


@pytest.mark.parametrize("kind", ["expense", "income"])
async def test_a_movement_alone_does_not_make_an_unknown_balance_known(ledger, kind):
    from arno.ledger import movements

    account = await create_account(ledger, kind="bank", name="SBI Savings")
    await getattr(movements, f"record_{kind}")(
        ledger,
        account_id=account["id"],
        amount_paise=25_000,
        description="Only recorded activity",
    )

    quality = (await account_summary(ledger))["cash_quality"]
    assert quality["status"] == "unknown"
    assert quality["known_paise"] == 0
    assert quality["known_count"] == 0
    assert quality["unknown_count"] == 1
    assert quality["unknown_accounts"] == ["SBI Savings"]

    refreshed = await get_account(ledger, account["id"])
    assert refreshed["statement_balance_paise"] is None
    assert refreshed["statement_balance_as_of"] is None
    assert refreshed["balance_paise"] == (-25_000 if kind == "expense" else 25_000)


async def test_an_explicit_zero_opening_is_known_and_stays_zero(ledger):
    await create_account(ledger, kind="bank", name="Zero bank", opening_balance_paise=0)

    quality = (await account_summary(ledger))["cash_quality"]
    assert quality == {
        "status": "known",
        "known_paise": 0,
        "known_count": 1,
        "unknown_count": 0,
        "unknown_accounts": [],
    }


async def test_a_stated_balance_after_activity_becomes_known(ledger):
    from arno.ledger.movements import record_expense

    account = await create_account(ledger, kind="bank", name="SBI Savings")
    await record_expense(ledger, account_id=account["id"], amount_paise=25_000, description="Lunch")
    assert (await account_summary(ledger))["cash_quality"]["status"] == "unknown"

    await set_statement_balance(
        ledger, account["id"], balance_paise=5_000_000, as_of=date(2026, 11, 1)
    )

    quality = (await account_summary(ledger))["cash_quality"]
    assert quality["status"] == "known"
    assert quality["known_paise"] == 5_000_000
    assert quality["known_count"] == 1


async def test_undoing_the_only_stated_balance_returns_to_unknown(ledger):
    account = await create_account(ledger, kind="bank", name="SBI Savings")
    result = await set_statement_balance(ledger, account["id"], balance_paise=1_000_000)
    assert (await account_summary(ledger))["cash_quality"]["status"] == "known"

    await undo_group(ledger, result["undo_id"])

    assert (await account_summary(ledger))["cash_quality"]["status"] == "unknown"
    refreshed = await get_account(ledger, account["id"])
    assert refreshed["statement_balance_paise"] is None
    assert refreshed["statement_balance_as_of"] is None


async def test_undoing_an_expense_keeps_a_surviving_anchor(ledger):
    from arno.ledger.movements import record_expense

    account = await create_account(
        ledger,
        kind="bank",
        name="SBI Savings",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=date(2026, 11, 1),
    )
    recorded = await record_expense(
        ledger, account_id=account["id"], amount_paise=25_000, description="Lunch"
    )

    await undo_group(ledger, recorded["undo_id"])

    quality = (await account_summary(ledger))["cash_quality"]
    assert quality["status"] == "known"
    assert quality["known_paise"] == 10_000_000
    refreshed = await get_account(ledger, account["id"])
    assert refreshed["statement_balance_as_of"] == "2026-11-01"


async def test_mixed_known_and_unknown_activity_reports_an_incomplete_total(ledger):
    from arno.ledger.movements import record_expense

    await create_account(ledger, kind="bank", name="Zero bank", opening_balance_paise=0)
    unknown = await create_account(ledger, kind="bank", name="SBI Savings")
    await record_expense(ledger, account_id=unknown["id"], amount_paise=25_000, description="Lunch")

    quality = (await account_summary(ledger))["cash_quality"]
    assert quality["status"] == "unknown"
    assert quality["known_paise"] == 0
    assert quality["known_count"] == 1
    assert quality["unknown_count"] == 1
    assert quality["unknown_accounts"] == ["SBI Savings"]


async def test_statement_balance_adjustment_is_visible_and_never_spending(ledger):
    account = await create_account(
        ledger, kind="bank", name="Cash account", opening_balance_paise=1_000_000
    )

    result = await set_statement_balance(
        ledger, account["id"], balance_paise=1_250_000, as_of=date(2026, 11, 1)
    )

    assert result["diff_paise"] == 250_000
    assert result["account"]["balance_paise"] == 1_250_000
    assert result["account"]["statement_balance_paise"] == 1_250_000
    spending = await ledger.fetchrow("SELECT coalesce(sum(amount_paise), 0) AS n FROM v_spending")
    assert spending["n"] == 0
    movement = await ledger.fetchrow(
        "SELECT kind FROM movements WHERE id = $1", result["movement_id"]
    )
    assert movement["kind"] == "adjustment"


async def test_archive_and_undo_restore_an_account(ledger):
    account = await create_account(
        ledger, kind="cash", name="Wallet cash", opening_balance_paise=50_000
    )

    await archive_account(ledger, account["id"])
    assert await list_accounts(ledger) == []
    assert (await get_account(ledger, account["id"]))["active"] is False

    archive_group = (
        await ledger.fetchrow(
            "SELECT group_id FROM audit_log WHERE table_name = 'accounts' AND row_id = $1 "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            account["id"],
        )
    )["group_id"]
    await undo_group(ledger, archive_group)
    assert (await get_account(ledger, account["id"]))["active"] is True


async def test_undo_account_creation_archives_it(ledger):
    account = await create_account(
        ledger, kind="cash", name="Wallet cash", opening_balance_paise=50_000
    )
    create_group = (
        await ledger.fetchrow(
            "SELECT group_id FROM audit_log WHERE table_name = 'accounts' AND row_id = $1 "
            "AND before IS NULL ORDER BY created_at ASC, id ASC LIMIT 1",
            account["id"],
        )
    )["group_id"]

    await undo_group(ledger, create_group)

    assert (await get_account(ledger, account["id"]))["active"] is False
    assert (await get_account(ledger, account["id"]))["balance_paise"] == 0
