"""Accounts: every place money can sit, one-to-one with a pgledger account.

`create_account` creates the pgledger account and the row, then records an opening balance as a
visible adjustment against the system Equity account. A stated balance later is the same kind of
adjustment, never spending.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fin_agent.ledger.audit import WriteGroup, new_id, write_group
from fin_agent.ledger.common import (
    ACCOUNT_KINDS,
    LIQUID_KINDS,
    SYSTEM_KINDS,
    account_payload,
    balance_as_of,
    get_account_row,
    has_known_balance,
    ledger_balance,
    list_account_rows,
    require_active_account,
    system_account,
    today,
)
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError
from fin_agent.ledger.postings import TransferSpec, create_movement_in_group

LAST4_RE = re.compile(r"^[0-9]{4}$")


def _base_currency(conn: psycopg.Connection) -> str:
    row = conn.execute("SELECT base_currency FROM settings WHERE id = 1").fetchone()
    return str(row["base_currency"]) if row else "INR"


def _validate_account_fields(
    *,
    kind: str,
    name: str,
    last4: str | None,
    liquid: bool,
    credit_limit_paise: int | None,
    statement_day: int | None,
    due_day: int | None,
    rate_bps: int | None,
    tenure_months: int | None,
) -> None:
    if kind not in ACCOUNT_KINDS:
        raise InvalidLedgerError(f"Unknown account kind {kind}")
    if not name.strip():
        raise InvalidLedgerError("An account needs a name")
    if last4 is not None and not LAST4_RE.match(last4):
        raise InvalidLedgerError("last4 must be four digits")
    if liquid and kind not in LIQUID_KINDS:
        raise InvalidLedgerError("Only bank, cash, and wallet accounts can be liquid")
    if kind == "card":
        # Report every missing field at once; one-at-a-time failures made the model retry
        # without learning that the next field was also required.
        problems: list[str] = []
        if credit_limit_paise is None:
            problems.append("a positive credit limit")
        elif credit_limit_paise <= 0:
            problems.append("a positive credit limit (it cannot be zero or negative)")
        if statement_day is None:
            problems.append("a statement day between 1 and 31")
        elif not 1 <= statement_day <= 31:
            problems.append(f"a statement day between 1 and 31, not {statement_day}")
        if due_day is None:
            problems.append("a due day between 1 and 31")
        elif not 1 <= due_day <= 31:
            problems.append(f"a due day between 1 and 31, not {due_day}")
        if len(problems) == 1:
            raise InvalidLedgerError(f"A card needs {problems[0]}")
        if problems:
            raise InvalidLedgerError(
                "A card needs " + ", ".join(problems[:-1]) + " and " + problems[-1]
            )
    elif any(value is not None for value in (credit_limit_paise, statement_day, due_day)):
        raise InvalidLedgerError("Credit limit and card days belong only to a card")
    if kind == "loan":
        if rate_bps is not None and rate_bps < 0:
            raise InvalidLedgerError("A loan rate cannot be negative")
        if tenure_months is not None and tenure_months <= 0:
            raise InvalidLedgerError("A loan tenure must be positive")
    elif any(value is not None for value in (rate_bps, tenure_months)):
        raise InvalidLedgerError("Rate and tenure belong only to a loan")


def ensure_system_accounts(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    """Create the one Spending, Income, and Equity account if they are missing."""
    accounts: dict[str, dict[str, Any]] = {}
    with write_group(conn, actor="system") as group:
        currency = _base_currency(conn)
        for kind, name in (
            ("spending", "Spending"),
            ("income", "Income"),
            ("equity", "Equity"),
        ):
            row = conn.execute(
                "SELECT * FROM accounts WHERE kind = %s ORDER BY created_at LIMIT 1", (kind,)
            ).fetchone()
            if row is None:
                ledger = conn.execute(
                    """
                    SELECT * FROM pgledger_create_account(%s, %s, true, true, %s)
                    """,
                    (name, currency, Jsonb({"system": kind})),
                ).fetchone()
                row = conn.execute(
                    """
                    INSERT INTO accounts (id, kind, ledger_account_id, name, liquid)
                    VALUES (%s, %s, %s, %s, false)
                    RETURNING *
                    """,
                    (new_id(conn, "acc"), kind, ledger["id"], name),
                ).fetchone()
                group.record_created("accounts", row)
            elif not row["active"]:
                after = conn.execute(
                    "UPDATE accounts SET active = true, updated_at = now() "
                    "WHERE id = %s RETURNING *",
                    (row["id"],),
                ).fetchone()
                group.record_updated("accounts", row["id"], row, after)
                row = after
            accounts[kind] = row
    return accounts


def _create_account_in_group(
    conn: psycopg.Connection,
    group: WriteGroup,
    *,
    kind: str,
    name: str,
    institution: str | None = None,
    last4: str | None = None,
    opening_balance_paise: int | None = None,
    opening_balance_as_of: date | None = None,
    liquid: bool | None = None,
    credit_limit_paise: int | None = None,
    statement_day: int | None = None,
    due_day: int | None = None,
    rate_bps: int | None = None,
    tenure_months: int | None = None,
    emi_schedule_id: str | None = None,
) -> dict[str, Any]:
    if kind in SYSTEM_KINDS:
        raise InvalidLedgerError("System accounts are created by the bootstrap")
    if liquid is None:
        liquid = kind in LIQUID_KINDS
    _validate_account_fields(
        kind=kind,
        name=name,
        last4=last4,
        liquid=liquid,
        credit_limit_paise=credit_limit_paise,
        statement_day=statement_day,
        due_day=due_day,
        rate_bps=rate_bps,
        tenure_months=tenure_months,
    )

    ledger = conn.execute(
        """
        SELECT * FROM pgledger_create_account(%s, %s, true, true, %s)
        """,
        (name.strip(), _base_currency(conn), Jsonb({"kind": kind})),
    ).fetchone()
    account_id = new_id(conn, "acc")
    stated_as_of = opening_balance_as_of or today()
    row = conn.execute(
        """
        INSERT INTO accounts (
            id, kind, ledger_account_id, name, institution, last4,
            statement_balance_paise, statement_balance_as_of, liquid,
            credit_limit_paise, statement_day, due_day, rate_bps, tenure_months,
            emi_schedule_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            account_id,
            kind,
            ledger["id"],
            name.strip(),
            institution,
            last4,
            opening_balance_paise,
            stated_as_of if opening_balance_paise is not None else None,
            liquid,
            credit_limit_paise,
            statement_day,
            due_day,
            rate_bps,
            tenure_months,
            emi_schedule_id,
        ),
    ).fetchone()
    group.record_created("accounts", row)

    if opening_balance_paise:
        equity = system_account(conn, "equity")
        if opening_balance_paise > 0:
            transfer = TransferSpec(
                from_account_id=equity["id"],
                to_account_id=account_id,
                amount_paise=opening_balance_paise,
                note="Opening balance",
            )
        else:
            transfer = TransferSpec(
                from_account_id=account_id,
                to_account_id=equity["id"],
                amount_paise=-opening_balance_paise,
                note="Opening balance",
            )
        record = create_movement_in_group(
            conn,
            group,
            date=stated_as_of,
            description=f"Opening balance for {name.strip()}",
            kind="adjustment",
            transfers=[transfer],
        )
        del record

    return row


def create_account(
    conn: psycopg.Connection,
    *,
    kind: str,
    name: str,
    institution: str | None = None,
    last4: str | None = None,
    opening_balance_paise: int | None = None,
    opening_balance_as_of: date | None = None,
    liquid: bool | None = None,
    credit_limit_paise: int | None = None,
    statement_day: int | None = None,
    due_day: int | None = None,
    rate_bps: int | None = None,
    tenure_months: int | None = None,
    emi_schedule_id: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    ensure_system_accounts(conn)
    with write_group(conn, actor=actor, message_id=message_id) as group:
        row = _create_account_in_group(
            conn,
            group,
            kind=kind,
            name=name,
            institution=institution,
            last4=last4,
            opening_balance_paise=opening_balance_paise,
            opening_balance_as_of=opening_balance_as_of,
            liquid=liquid,
            credit_limit_paise=credit_limit_paise,
            statement_day=statement_day,
            due_day=due_day,
            rate_bps=rate_bps,
            tenure_months=tenure_months,
            emi_schedule_id=emi_schedule_id,
        )
    return account_payload(conn, row) | {"undo_id": group.group_id}


def list_accounts(
    conn: psycopg.Connection, *, include_system: bool = False, include_inactive: bool = False
) -> list[dict[str, Any]]:
    return [
        account_payload(conn, row)
        for row in list_account_rows(
            conn, include_system=include_system, include_inactive=include_inactive
        )
    ]


def get_account(conn: psycopg.Connection, account_id: str) -> dict[str, Any]:
    return account_payload(conn, get_account_row(conn, account_id))


def update_account(
    conn: psycopg.Connection,
    account_id: str,
    *,
    name: str | None = None,
    institution: str | None = None,
    last4: str | None = None,
    liquid: bool | None = None,
    credit_limit_paise: int | None = None,
    statement_day: int | None = None,
    due_day: int | None = None,
    rate_bps: int | None = None,
    tenure_months: int | None = None,
    emi_schedule_id: str | None = None,
    active: bool | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    before = get_account_row(conn, account_id)
    if before["kind"] in SYSTEM_KINDS:
        raise InvalidLedgerError("The system accounts are fixed")
    if emi_schedule_id is not None:
        row = conn.execute("SELECT 1 FROM schedules WHERE id = %s", (emi_schedule_id,)).fetchone()
        if row is None:
            raise NotFoundLedgerError(f"No schedule {emi_schedule_id}")
    updates: dict[str, Any] = {}
    for key, value in {
        "name": name,
        "institution": institution,
        "last4": last4,
        "liquid": liquid,
        "credit_limit_paise": credit_limit_paise,
        "statement_day": statement_day,
        "due_day": due_day,
        "rate_bps": rate_bps,
        "tenure_months": tenure_months,
        "emi_schedule_id": emi_schedule_id,
        "active": active,
    }.items():
        if value is not None:
            updates[key] = value
    if not updates:
        return account_payload(conn, before) | {"undo_id": None}

    merged = {**before, **updates}
    _validate_account_fields(
        kind=merged["kind"],
        name=merged["name"],
        last4=merged["last4"],
        liquid=merged["liquid"],
        credit_limit_paise=merged["credit_limit_paise"],
        statement_day=merged["statement_day"],
        due_day=merged["due_day"],
        rate_bps=merged["rate_bps"],
        tenure_months=merged["tenure_months"],
    )

    columns = list(updates)
    assignments = [f"{column} = %s" for column in columns]
    with write_group(conn, actor=actor, message_id=message_id) as group:
        after = conn.execute(
            f"""
            UPDATE accounts SET {", ".join(assignments)}, updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (*[updates[column] for column in columns], account_id),
        ).fetchone()
        group.record_updated("accounts", account_id, before, after)
    return account_payload(conn, after) | {"undo_id": group.group_id}


def archive_account(
    conn: psycopg.Connection,
    account_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return update_account(conn, account_id, active=False, actor=actor, message_id=message_id)


def set_statement_balance(
    conn: psycopg.Connection,
    account_id: str,
    *,
    balance_paise: int,
    as_of: date | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Set the last stated balance and record the difference against Equity, visibly."""
    account = require_active_account(conn, account_id)
    if account["kind"] in SYSTEM_KINDS:
        raise InvalidLedgerError("The system accounts have no statement balance")
    ensure_system_accounts(conn)
    as_of = as_of or today()
    current = ledger_balance(conn, account["ledger_account_id"])
    difference = int(balance_paise) - current

    movement_id = None
    with write_group(conn, actor=actor, message_id=message_id) as group:
        if difference:
            equity = system_account(conn, "equity")
            if difference > 0:
                transfer = TransferSpec(
                    from_account_id=equity["id"],
                    to_account_id=account_id,
                    amount_paise=difference,
                    note="Balance adjustment",
                )
            else:
                transfer = TransferSpec(
                    from_account_id=account_id,
                    to_account_id=equity["id"],
                    amount_paise=-difference,
                    note="Balance adjustment",
                )
            record = create_movement_in_group(
                conn,
                group,
                date=as_of,
                description=f"Balance adjustment for {account['name']}",
                kind="adjustment",
                transfers=[transfer],
            )
            movement_id = record.id

        after = conn.execute(
            """
            UPDATE accounts
            SET statement_balance_paise = %s,
                statement_balance_as_of = %s,
                updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (balance_paise, as_of, account_id),
        ).fetchone()
        group.record_updated("accounts", account_id, account, after)

    return {
        "account": account_payload(conn, after),
        "diff_paise": difference,
        "movement_id": movement_id,
        "undo_id": group.group_id,
    }


def cash_quality(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether the liquid total is complete, with the accounts that make it incomplete."""
    liquid = [account for account in accounts if account["liquid"] and account["active"]]
    unknown = [account for account in liquid if not has_known_balance(account)]
    known = [account for account in liquid if has_known_balance(account)]
    if not liquid:
        status = "no_accounts"
    elif unknown:
        status = "unknown"
    else:
        status = "known"
    return {
        "status": status,
        "known_paise": sum(int(account["balance_paise"]) for account in known),
        "known_count": len(known),
        "unknown_count": len(unknown),
        "unknown_accounts": [account["name"] for account in unknown],
    }


def account_summary(conn: psycopg.Connection) -> dict[str, Any]:
    accounts = list_accounts(conn)
    liquid_total = sum(
        account["balance_paise"] for account in accounts if account["liquid"] and account["active"]
    )
    card_owed = sum(
        account.get("card_outstanding_paise", 0)
        for account in accounts
        if account["kind"] == "card" and account["active"]
    )
    loan_outstanding = sum(
        account.get("loan_outstanding_paise", 0)
        for account in accounts
        if account["kind"] == "loan" and account["active"]
    )
    return {
        "accounts": accounts,
        "liquid_total_paise": liquid_total,
        "card_owed_paise": card_owed,
        "loan_outstanding_paise": loan_outstanding,
        "cash_quality": cash_quality(accounts),
        "as_of": today().isoformat(),
    }


class LoanTermsError(InvalidLedgerError):
    """A named loan can't be amortized as it stands."""


def loan_terms(conn: psycopg.Connection, name: str) -> dict[str, Any]:
    """The terms `amortize` uses for a named loan: today's outstanding balance, its rate, its
    tenure as the cap, and the EMI its schedule really charges. The eval grader reads the same
    terms, so an expected value can't drift from what the tool computes."""
    row = conn.execute(
        "SELECT * FROM accounts WHERE lower(name) = lower(%s) AND kind = 'loan' LIMIT 1",
        (name,),
    ).fetchone()
    if row is None:
        raise LoanTermsError(f"No loan named {name!r}")
    # A loan is a liability: its ledger balance is negative, as in `account_payload`. Reading it
    # unsigned made every named loan look paid off, so scripts re-derived the terms by hand.
    principal = max(-ledger_balance(conn, row["ledger_account_id"]), 0)
    if principal <= 0:
        raise LoanTermsError("That loan has no outstanding balance")
    if not row["tenure_months"]:
        raise LoanTermsError("That loan has no tenure; pass the terms instead")
    emi = None
    if row["emi_schedule_id"]:
        schedule = conn.execute(
            "SELECT amount_paise FROM schedules WHERE id = %s", (row["emi_schedule_id"],)
        ).fetchone()
        emi = int(schedule["amount_paise"]) if schedule else None
    return {
        "principal_paise": principal,
        "annual_rate": (row["rate_bps"] or 0) / 100,
        "months": int(row["tenure_months"]),
        "emi_paise": emi,
    }


__all__ = [
    "LoanTermsError",
    "archive_account",
    "balance_as_of",
    "cash_quality",
    "create_account",
    "ensure_system_accounts",
    "get_account",
    "list_accounts",
    "loan_terms",
    "account_summary",
    "set_statement_balance",
    "update_account",
]
