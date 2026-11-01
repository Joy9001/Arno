from __future__ import annotations

from datetime import date

import pytest

from fin_agent.ledger.accounts import (
    archive_account,
    create_account,
    get_account,
    list_accounts,
    set_statement_balance,
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
