"""Shared ledger helpers: account lookup, balances, and payload shaping."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from arno.db.conn import Connection
from arno.ledger.errors import InvalidLedgerError, NotFoundLedgerError

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


async def get_account_row(conn: Connection, account_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM v_accounts WHERE id = $1", account_id)
    if row is None:
        raise NotFoundLedgerError(f"No account {account_id}")
    return row


async def require_active_account(conn: Connection, account_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
    if row is None:
        raise NotFoundLedgerError(f"No account {account_id}")
    if not row["active"]:
        name = row["name"]
        if name is None:
            resolved = await get_account_row(conn, account_id)
            name = resolved["name"]
        raise InvalidLedgerError(f"Account {name} is archived")
    return row


async def resolve_account(conn: Connection, account_id: str | None) -> dict[str, Any]:
    if not account_id:
        raise InvalidLedgerError("An account is required")
    return await require_active_account(conn, account_id)


async def system_account(conn: Connection, kind: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        "SELECT * FROM accounts WHERE kind = $1 AND active ORDER BY created_at LIMIT 1",
        kind,
    )
    if row is None:
        raise InvalidLedgerError(
            f"The {kind} system account is missing. Start the API or run the migration bootstrap."
        )
    return row


async def ledger_balances(conn: Connection, ledger_account_ids: list[str]) -> dict[str, int]:
    if not ledger_account_ids:
        return {}
    # Routing `unnest($1::text[])` through a subquery creates an InitPlan so Postgres caches a
    # generic plan while still using the primary-key index.
    rows = await conn.fetch(
        """
        SELECT id, coalesce(balance, 0)::bigint AS balance
        FROM pgledger_accounts
        WHERE id = ANY(ARRAY(SELECT unnest($1::text[])))
        """,
        ledger_account_ids,
    )
    by_id = {str(row["id"]): int(row["balance"]) for row in rows}
    return {account_id: by_id.get(account_id, 0) for account_id in ledger_account_ids}


async def ledger_balance(conn: Connection, ledger_account_id: str) -> int:
    return (await ledger_balances(conn, [ledger_account_id]))[ledger_account_id]


async def balance_as_of_many(
    conn: Connection, ledger_account_ids: list[str]
) -> dict[str, date | None]:
    if not ledger_account_ids:
        return {}
    # Same `ARRAY(SELECT unnest(...))` generic-plan idiom as `ledger_balances`.
    rows = await conn.fetch(
        """
        SELECT e.account_id, max(m.date) AS as_of
        FROM pgledger_entries e
        JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        WHERE e.account_id = ANY(ARRAY(SELECT unnest($1::text[])))
        GROUP BY e.account_id
        """,
        ledger_account_ids,
    )
    by_id = {str(row["account_id"]): row["as_of"] for row in rows}
    return {account_id: by_id.get(account_id) for account_id in ledger_account_ids}


async def balance_as_of(conn: Connection, ledger_account_id: str) -> date | None:
    return (await balance_as_of_many(conn, [ledger_account_id]))[ledger_account_id]


def has_known_balance(account: dict[str, Any]) -> bool:
    """A balance is known only when an explicit stated or statement balance anchors it.

    The anchor is stored as ``statement_balance_paise`` plus its ``statement_balance_as_of``:
    an opening balance, a user-stated balance, or a committed statement all write both, including
    an explicit zero. A ledger movement changes a known balance but never establishes one, so an
    account whose first entry is spend or income remains unknown.
    """
    if account["kind"] in {"goal", "person"}:
        # A fenced goal bucket and a person's account have no outside statement: both open at zero
        # and only the ledger moves them, so their ledger balance is the whole truth.
        return True
    return (
        account["statement_balance_paise"] is not None
        and account["statement_balance_as_of"] is not None
    )


def balance_anchor(account: dict[str, Any], last_movement: date | None) -> date | None:
    """The date a known balance is exact as of: the later of its last movement and its stated one.

    Rows carry two separate pieces of evidence: ``statement_balance_as_of`` (an opening balance, a
    user-stated balance, or a committed statement) and the last movement's date. A fresh movement
    makes a stale statement anchor irrelevant, so the age of the balance is measured from whichever
    is later. ``None`` when the balance is not known at all, and a `goal` account with no movement
    yet is exact at zero as of ``None``.
    """
    if not has_known_balance(account):
        return None
    evidence = [
        day for day in (last_movement, account["statement_balance_as_of"]) if day is not None
    ]
    return max(evidence) if evidence else None


def _assemble_account_payload(
    row: dict[str, Any], *, balance: int, as_of: date | None, today_date: date
) -> dict[str, Any]:
    statement_as_of = row["statement_balance_as_of"]
    anchor = balance_anchor(row, as_of)
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
        "balance_age_days": (today_date - anchor).days if anchor else None,
        "statement_age_days": (
            (today_date - statement_as_of).days if statement_as_of is not None else None
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
        known = has_known_balance(row)
        limit = row["credit_limit_paise"]
        payload["card_outstanding_paise"] = max(-balance, 0) if known else None
        # A credit balance (an overpayment) is usable, so the available credit is the limit plus
        # the balance, which is negative when money is owed.
        payload["available_credit_paise"] = (
            max(limit + balance, 0) if known and limit is not None else None
        )
    if row["kind"] == "loan":
        payload["loan_outstanding_paise"] = max(-balance, 0)
    return payload


async def account_payloads(conn: Connection, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    ledger_ids = [str(row["ledger_account_id"]) for row in rows]
    missing_balance_ids = [
        str(row["ledger_account_id"]) for row in rows if "balance_paise" not in row
    ]
    fetched_balances = (
        await ledger_balances(conn, missing_balance_ids) if missing_balance_ids else {}
    )
    as_of_by_id = await balance_as_of_many(conn, ledger_ids)
    today_date = today()
    return [
        _assemble_account_payload(
            row,
            balance=(
                int(row["balance_paise"])
                if "balance_paise" in row
                else fetched_balances[str(row["ledger_account_id"])]
            ),
            as_of=as_of_by_id[str(row["ledger_account_id"])],
            today_date=today_date,
        )
        for row in rows
    ]


async def account_payload(conn: Connection, row: dict[str, Any]) -> dict[str, Any]:
    return (await account_payloads(conn, [row]))[0]


async def list_account_rows(
    conn: Connection, *, include_system: bool = False, include_inactive: bool = False
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    if not include_system:
        clauses.append("kind NOT IN ('spending', 'income', 'equity')")
    if not include_inactive:
        clauses.append("active")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return await conn.fetch(f"SELECT * FROM v_accounts {where} ORDER BY kind, name, created_at")
