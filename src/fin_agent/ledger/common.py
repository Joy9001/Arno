"""Shared ledger helpers: account lookup, balances, and payload shaping."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import psycopg

from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

SYSTEM_KINDS = ("spending", "income", "equity")
ACCOUNT_KINDS = (
    "bank",
    "card",
    "cash",
    "wallet",
    "loan",
    "asset",
    "goal",
    "person",
    "spending",
    "income",
    "equity",
)
LIQUID_KINDS = ("bank", "cash", "wallet")


def today() -> date:
    """Today in India, where the schedule and holiday rules live."""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def get_account_row(conn: psycopg.Connection, account_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM accounts WHERE id = %s", (account_id,)).fetchone()
    if row is None:
        raise NotFoundLedgerError(f"No account {account_id}")
    return row


def require_active_account(conn: psycopg.Connection, account_id: str) -> dict[str, Any]:
    row = get_account_row(conn, account_id)
    if not row["active"]:
        raise InvalidLedgerError(f"Account {row['name']} is archived")
    return row


def resolve_account(conn: psycopg.Connection, account_id: str | None) -> dict[str, Any]:
    if not account_id:
        raise InvalidLedgerError("An account is required")
    return require_active_account(conn, account_id)


def system_account(conn: psycopg.Connection, kind: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM accounts WHERE kind = %s AND active ORDER BY created_at LIMIT 1",
        (kind,),
    ).fetchone()
    if row is None:
        raise InvalidLedgerError(
            f"The {kind} system account is missing. Start the API or run the migration bootstrap."
        )
    return row


def ledger_balance(conn: psycopg.Connection, ledger_account_id: str) -> int:
    row = conn.execute(
        "SELECT coalesce(balance, 0)::bigint AS balance FROM pgledger_accounts WHERE id = %s",
        (ledger_account_id,),
    ).fetchone()
    return int(row["balance"]) if row else 0


def balance_as_of(conn: psycopg.Connection, ledger_account_id: str) -> date | None:
    row = conn.execute(
        """
        SELECT max(m.date) AS as_of
        FROM pgledger_transfers t
        LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE t.from_account_id = %s OR t.to_account_id = %s
        """,
        (ledger_account_id, ledger_account_id),
    ).fetchone()
    return row["as_of"] if row else None


def has_known_balance(account: dict[str, Any]) -> bool:
    """A balance is known only when an explicit stated or statement balance anchors it.

    The anchor is stored as ``statement_balance_paise`` plus its ``statement_balance_as_of``:
    an opening balance, a user-stated balance, or a committed statement all write both, including
    an explicit zero. A ledger movement changes a known balance but never establishes one, so an
    account whose first entry is spend or income remains unknown.
    """
    return (
        account["statement_balance_paise"] is not None
        and account["statement_balance_as_of"] is not None
    )


def account_payload(conn: psycopg.Connection, row: dict[str, Any]) -> dict[str, Any]:
    balance = ledger_balance(conn, row["ledger_account_id"])
    as_of = balance_as_of(conn, row["ledger_account_id"])
    statement_as_of = row["statement_balance_as_of"]
    payload: dict[str, Any] = {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "institution": row["institution"],
        "last4": row["last4"],
        "liquid": row["liquid"],
        "active": row["active"],
        "balance_paise": balance,
        "balance_as_of": as_of.isoformat() if as_of else None,
        "statement_balance_paise": row["statement_balance_paise"],
        "statement_balance_as_of": statement_as_of.isoformat() if statement_as_of else None,
        "statement_age_days": (
            (today() - statement_as_of).days if statement_as_of is not None else None
        ),
        "credit_limit_paise": row["credit_limit_paise"],
        "statement_day": row["statement_day"],
        "due_day": row["due_day"],
        "rate_bps": row["rate_bps"],
        "tenure_months": row["tenure_months"],
        "emi_schedule_id": row["emi_schedule_id"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    if row["kind"] == "card":
        # A ledger movement alone does not establish a card's outstanding debt, so an unknown
        # card must not look like a zero balance with the whole limit still available.
        owed = max(-balance, 0) if has_known_balance(row) else None
        limit = row["credit_limit_paise"]
        payload["card_outstanding_paise"] = owed
        payload["available_credit_paise"] = (
            max(limit - owed, 0) if owed is not None and limit is not None else None
        )
    if row["kind"] == "loan":
        payload["loan_outstanding_paise"] = max(-balance, 0)
    return payload


def list_account_rows(
    conn: psycopg.Connection, *, include_system: bool = False, include_inactive: bool = False
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    if not include_system:
        clauses.append("kind NOT IN ('spending', 'income', 'equity')")
    if not include_inactive:
        clauses.append("active")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return list(
        conn.execute(
            f"SELECT * FROM accounts {where} ORDER BY kind, name, created_at"  # noqa: S608
        ).fetchall()
    )
