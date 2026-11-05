from __future__ import annotations

from datetime import date

import pytest

from fin_agent.ledger.accounts import (
    account_summary,
    archive_account,
    create_account,
    get_account,
    list_accounts,
    set_statement_balance,
    update_account,
)
from fin_agent.ledger.audit import undo_group
from fin_agent.ledger.errors import InvalidLedgerError


def test_system_accounts_are_single_and_not_in_the_panel(ledger):
    accounts = list_accounts(ledger, include_system=True)
    system = [
        account for account in accounts if account["kind"] in {"spending", "income", "equity"}
    ]

    assert {account["kind"] for account in system} == {"spending", "income", "equity"}
    assert len(system) == 3
    assert all(account["name"] in {"Spending", "Income", "Equity"} for account in system)
    assert list_accounts(ledger) == []


def test_bank_opening_balance_is_an_adjustment_not_spending(ledger):
    account = create_account(
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
    spending = ledger.execute(
        "SELECT coalesce(sum(amount_paise), 0) AS n FROM v_spending"
    ).fetchone()
    assert spending["n"] == 0


def test_card_cycle_limit_and_available_credit(ledger):
    bank = create_account(ledger, kind="bank", name="SBI", opening_balance_paise=10_000_000)
    card = create_account(
        ledger,
        kind="card",
        name="Slice card",
        last4="2207",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    assert card["credit_limit_paise"] == 10_000_000
    assert card["available_credit_paise"] == 10_000_000

    from fin_agent.ledger.movements import record_expense

    # Move a card purchase through the ledger; the card balance becomes a liability.
    category = ledger.execute("SELECT id FROM categories WHERE name = 'Shopping'").fetchone()["id"]
    record_expense(
        ledger,
        account_id=card["id"],
        amount_paise=3_000_000,
        description="Headphones",
        category_id=category,
    )
    refreshed = get_account(ledger, card["id"])
    assert refreshed["balance_paise"] == -3_000_000
    assert refreshed["card_outstanding_paise"] == 3_000_000
    assert refreshed["available_credit_paise"] == 7_000_000
    assert bank["balance_paise"] == 10_000_000


def test_loan_balance_is_outstanding_principal(ledger):
    loan = create_account(
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


def test_card_fields_are_required_and_only_for_a_card(ledger):
    with pytest.raises(InvalidLedgerError, match="credit limit"):
        create_account(ledger, kind="card", name="Broken card", statement_day=5, due_day=25)
    with pytest.raises(InvalidLedgerError, match="belong only to a card"):
        create_account(ledger, kind="bank", name="Odd", credit_limit_paise=1000)


def test_card_missing_fields_are_reported_together_and_write_nothing(ledger):
    with pytest.raises(InvalidLedgerError) as caught:
        create_account(ledger, kind="card", name="Slice")

    message = caught.value.message
    assert "credit limit" in message
    assert "statement day" in message
    assert "due day" in message
    assert list_accounts(ledger) == []
    assert (
        ledger.execute("SELECT count(*) AS n FROM accounts WHERE name = 'Slice'").fetchone()["n"]
        == 0
    )


def test_card_invalid_ranges_are_reported_together(ledger):
    with pytest.raises(InvalidLedgerError) as caught:
        create_account(
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
    assert list_accounts(ledger) == []


def test_card_partial_update_changes_only_the_given_field(ledger):
    card = create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )

    updated = update_account(ledger, card["id"], due_day=28)

    assert updated["due_day"] == 28
    assert updated["statement_day"] == 5
    assert updated["credit_limit_paise"] == 10_000_000

    with pytest.raises(InvalidLedgerError, match="due day"):
        update_account(ledger, card["id"], due_day=32)
    assert get_account(ledger, card["id"])["due_day"] == 28


def test_liquid_total_quality_separates_unknown_zero_and_empty(ledger):
    assert account_summary(ledger)["cash_quality"]["status"] == "no_accounts"

    unknown = create_account(ledger, kind="bank", name="SBI Savings")
    quality = account_summary(ledger)["cash_quality"]
    assert quality == {
        "status": "unknown",
        "known_paise": 0,
        "known_count": 0,
        "unknown_count": 1,
        "unknown_accounts": ["SBI Savings"],
    }

    set_statement_balance(ledger, unknown["id"], balance_paise=0)
    quality = account_summary(ledger)["cash_quality"]
    assert quality["status"] == "known"
    assert quality["known_paise"] == 0
    assert quality["known_count"] == 1
    assert quality["unknown_count"] == 0

    create_account(ledger, kind="bank", name="HDFC Savings", opening_balance_paise=2_500_000)
    quality = account_summary(ledger)["cash_quality"]
    assert quality["status"] == "known"
    assert quality["known_paise"] == 2_500_000
    assert quality["known_count"] == 2
    assert quality["unknown_accounts"] == []


@pytest.mark.parametrize("kind", ["expense", "income"])
def test_a_movement_alone_does_not_make_an_unknown_balance_known(ledger, kind):
    from fin_agent.ledger import movements

    account = create_account(ledger, kind="bank", name="SBI Savings")
    getattr(movements, f"record_{kind}")(
        ledger,
        account_id=account["id"],
        amount_paise=25_000,
        description="Only recorded activity",
    )

    quality = account_summary(ledger)["cash_quality"]
    assert quality["status"] == "unknown"
    assert quality["known_paise"] == 0
    assert quality["known_count"] == 0
    assert quality["unknown_count"] == 1
    assert quality["unknown_accounts"] == ["SBI Savings"]

    refreshed = get_account(ledger, account["id"])
    assert refreshed["statement_balance_paise"] is None
    assert refreshed["statement_balance_as_of"] is None
    assert refreshed["balance_paise"] == (-25_000 if kind == "expense" else 25_000)


def test_an_explicit_zero_opening_is_known_and_stays_zero(ledger):
    create_account(ledger, kind="bank", name="Zero bank", opening_balance_paise=0)

    quality = account_summary(ledger)["cash_quality"]
    assert quality == {
        "status": "known",
        "known_paise": 0,
        "known_count": 1,
        "unknown_count": 0,
        "unknown_accounts": [],
    }


def test_a_stated_balance_after_activity_becomes_known(ledger):
    from fin_agent.ledger.movements import record_expense

    account = create_account(ledger, kind="bank", name="SBI Savings")
    record_expense(ledger, account_id=account["id"], amount_paise=25_000, description="Lunch")
    assert account_summary(ledger)["cash_quality"]["status"] == "unknown"

    set_statement_balance(ledger, account["id"], balance_paise=5_000_000, as_of=date(2026, 11, 1))

    quality = account_summary(ledger)["cash_quality"]
    assert quality["status"] == "known"
    assert quality["known_paise"] == 5_000_000
    assert quality["known_count"] == 1


def test_undoing_the_only_stated_balance_returns_to_unknown(ledger):
    account = create_account(ledger, kind="bank", name="SBI Savings")
    result = set_statement_balance(ledger, account["id"], balance_paise=1_000_000)
    assert account_summary(ledger)["cash_quality"]["status"] == "known"

    undo_group(ledger, result["undo_id"])

    assert account_summary(ledger)["cash_quality"]["status"] == "unknown"
    refreshed = get_account(ledger, account["id"])
    assert refreshed["statement_balance_paise"] is None
    assert refreshed["statement_balance_as_of"] is None


def test_undoing_an_expense_keeps_a_surviving_anchor(ledger):
    from fin_agent.ledger.movements import record_expense

    account = create_account(
        ledger,
        kind="bank",
        name="SBI Savings",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=date(2026, 11, 1),
    )
    recorded = record_expense(
        ledger, account_id=account["id"], amount_paise=25_000, description="Lunch"
    )

    undo_group(ledger, recorded["undo_id"])

    quality = account_summary(ledger)["cash_quality"]
    assert quality["status"] == "known"
    assert quality["known_paise"] == 10_000_000
    refreshed = get_account(ledger, account["id"])
    assert refreshed["statement_balance_as_of"] == "2026-11-01"


def test_mixed_known_and_unknown_activity_reports_an_incomplete_total(ledger):
    from fin_agent.ledger.movements import record_expense

    create_account(ledger, kind="bank", name="Zero bank", opening_balance_paise=0)
    unknown = create_account(ledger, kind="bank", name="SBI Savings")
    record_expense(ledger, account_id=unknown["id"], amount_paise=25_000, description="Lunch")

    quality = account_summary(ledger)["cash_quality"]
    assert quality["status"] == "unknown"
    assert quality["known_paise"] == 0
    assert quality["known_count"] == 1
    assert quality["unknown_count"] == 1
    assert quality["unknown_accounts"] == ["SBI Savings"]


def test_statement_balance_adjustment_is_visible_and_never_spending(ledger):
    account = create_account(
        ledger, kind="bank", name="Cash account", opening_balance_paise=1_000_000
    )

    result = set_statement_balance(
        ledger, account["id"], balance_paise=1_250_000, as_of=date(2026, 11, 1)
    )

    assert result["diff_paise"] == 250_000
    assert result["account"]["balance_paise"] == 1_250_000
    assert result["account"]["statement_balance_paise"] == 1_250_000
    spending = ledger.execute(
        "SELECT coalesce(sum(amount_paise), 0) AS n FROM v_spending"
    ).fetchone()
    assert spending["n"] == 0
    movement = ledger.execute(
        "SELECT kind FROM movements WHERE id = %s", (result["movement_id"],)
    ).fetchone()
    assert movement["kind"] == "adjustment"


def test_archive_and_undo_restore_an_account(ledger):
    account = create_account(ledger, kind="cash", name="Wallet cash", opening_balance_paise=50_000)

    archive_account(ledger, account["id"])
    assert list_accounts(ledger) == []
    assert get_account(ledger, account["id"])["active"] is False

    archive_group = ledger.execute(
        "SELECT group_id FROM audit_log WHERE table_name = 'accounts' AND row_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (account["id"],),
    ).fetchone()["group_id"]
    undo_group(ledger, archive_group)
    assert get_account(ledger, account["id"])["active"] is True


def test_undo_account_creation_archives_it(ledger):
    account = create_account(ledger, kind="cash", name="Wallet cash", opening_balance_paise=50_000)
    create_group = ledger.execute(
        "SELECT group_id FROM audit_log WHERE table_name = 'accounts' AND row_id = %s "
        "AND before IS NULL ORDER BY created_at ASC, id ASC LIMIT 1",
        (account["id"],),
    ).fetchone()["group_id"]

    undo_group(ledger, create_group)

    assert get_account(ledger, account["id"])["active"] is False
    assert get_account(ledger, account["id"])["balance_paise"] == 0
