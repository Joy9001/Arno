"""Accounts: every place money can sit, one-to-one with a pgledger account.

`create_account` creates the pgledger account and the row, then records an opening balance as a
visible adjustment against the system Equity account. A stated balance later is the same kind of
adjustment, never spending.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from arno.db.conn import Connection, quote_ident
from arno.ledger.audit import WriteGroup, new_id, write_group
from arno.ledger.common import (
    ACCOUNT_KINDS,
    LIQUID_KINDS,
    SYSTEM_KINDS,
    account_payload,
    account_payloads,
    balance_as_of,
    get_account_row,
    has_known_balance,
    ledger_balance,
    list_account_rows,
    system_account,
    today,
)
from arno.ledger.errors import InvalidLedgerError, NotFoundLedgerError
from arno.ledger.postings import TransferSpec, create_movement_in_group

LAST4_RE = re.compile(r"^[0-9]{4}$")


async def _base_currency(conn: Connection) -> str:
    row = await conn.fetchrow("SELECT base_currency FROM settings WHERE id = 1")
    return str(row["base_currency"]) if row else "INR"


def _validate_balance_date(value: date | None) -> None:
    """A balance is a fact about a moment that has happened; a future one is a guess."""
    if value is not None and value > today():
        raise InvalidLedgerError(
            f"A balance cannot be dated in the future ({value.isoformat()}); "
            f"use {today().isoformat()} or earlier"
        )


def _validate_account_fields(
    *,
    kind: str,
    name: str | None,
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
    if kind not in ("person", "goal") and (name is None or not name.strip()):
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


async def ensure_system_accounts(conn: Connection) -> dict[str, dict[str, Any]]:
    """Create the one Spending, Income, and Equity account if they are missing."""
    accounts: dict[str, dict[str, Any]] = {}
    async with write_group(conn, actor="system") as group:
        currency = await _base_currency(conn)
        for kind, name in (
            ("spending", "Spending"),
            ("income", "Income"),
            ("equity", "Equity"),
        ):
            row = await conn.fetchrow(
                "SELECT * FROM accounts WHERE kind = $1 ORDER BY created_at LIMIT 1", kind
            )
            if row is None:
                account_id = await new_id(conn, "acc")
                ledger = await conn.fetchrow_required(
                    """
                    SELECT * FROM pgledger_create_account($1, $2, true, true, $3)
                    """,
                    account_id,
                    currency,
                    {"system": kind},
                )
                row = await conn.fetchrow_required(
                    """
                    INSERT INTO accounts (id, kind, ledger_account_id, name, liquid)
                    VALUES ($1, $2, $3, $4, false)
                    RETURNING *
                    """,
                    account_id,
                    kind,
                    ledger["id"],
                    name,
                )
                group.record_created("accounts", row)
            elif not row["active"]:
                after = await conn.fetchrow_required(
                    "UPDATE accounts SET active = true, updated_at = now() "
                    "WHERE id = $1 RETURNING *",
                    row["id"],
                )
                group.record_updated("accounts", row["id"], row, after)
                row = after
            accounts[kind] = row
    return accounts


async def _create_account_in_group(
    conn: Connection,
    group: WriteGroup,
    *,
    kind: str,
    name: str | None = None,
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
    _validate_balance_date(opening_balance_as_of)
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

    account_id = await new_id(conn, "acc")
    ledger = await conn.fetchrow_required(
        """
        SELECT * FROM pgledger_create_account($1, $2, true, true, $3)
        """,
        account_id,
        await _base_currency(conn),
        {"kind": kind},
    )
    stored_name = None if kind in ("person", "goal") else (name or "").strip()
    stated_as_of = opening_balance_as_of or today()
    row = await conn.fetchrow_required(
        """
        INSERT INTO accounts (
            id, kind, ledger_account_id, name, institution, last4,
            statement_balance_paise, statement_balance_as_of, liquid,
            credit_limit_paise, statement_day, due_day, rate_bps, tenure_months,
            emi_schedule_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        RETURNING *
        """,
        account_id,
        kind,
        ledger["id"],
        stored_name,
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
    )
    group.record_created("accounts", row)

    if opening_balance_paise:
        equity = await system_account(conn, "equity")
        label = stored_name or (name.strip() if name else kind)
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
        record = await create_movement_in_group(
            conn,
            group,
            date=stated_as_of,
            description=f"Opening balance for {label}",
            kind="adjustment",
            transfers=[transfer],
        )
        del record

    return row


async def create_account(
    conn: Connection,
    *,
    kind: str,
    name: str | None = None,
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
    await ensure_system_accounts(conn)
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        row = await _create_account_in_group(
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
    return await account_payload(conn, row) | {"undo_id": group.group_id}


async def list_accounts(
    conn: Connection, *, include_system: bool = False, include_inactive: bool = False
) -> list[dict[str, Any]]:
    rows = await list_account_rows(
        conn, include_system=include_system, include_inactive=include_inactive
    )
    return await account_payloads(conn, rows)


async def get_account(conn: Connection, account_id: str) -> dict[str, Any]:
    return await account_payload(conn, await get_account_row(conn, account_id))


async def update_account(
    conn: Connection,
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
    before = await get_account_row(conn, account_id)
    if before["kind"] in SYSTEM_KINDS:
        raise InvalidLedgerError("The system accounts are fixed")
    if emi_schedule_id is not None:
        row = await conn.fetchrow("SELECT 1 FROM schedules WHERE id = $1", emi_schedule_id)
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
        return await account_payload(conn, before) | {"undo_id": None}

    owner_name: str | None = None
    if "name" in updates:
        cleaned = str(updates["name"]).strip()
        if not cleaned:
            raise InvalidLedgerError("An account needs a name")
        if before["kind"] == "person":
            owner_name = cleaned
            del updates["name"]
        elif before["kind"] == "goal":
            if cleaned.lower().endswith(" goal"):
                cleaned = cleaned[:-5].strip()
            if not cleaned:
                raise InvalidLedgerError("An account needs a name")
            owner_name = cleaned
            del updates["name"]
        else:
            updates["name"] = cleaned

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

    if not updates and owner_name is None:
        return await account_payload(conn, before) | {"undo_id": None}

    async with write_group(conn, actor=actor, message_id=message_id) as group:
        if owner_name is not None and before["kind"] == "person":
            payee_before = await conn.fetchrow(
                "SELECT * FROM payees WHERE person_account_id = $1", account_id
            )
            if payee_before is not None and payee_before["display_name"] != owner_name:
                payee_after = await conn.fetchrow_required(
                    "UPDATE payees SET display_name = $1, updated_at = now() "
                    "WHERE id = $2 RETURNING *",
                    owner_name,
                    payee_before["id"],
                )
                group.record_updated("payees", payee_before["id"], payee_before, payee_after)
        elif owner_name is not None and before["kind"] == "goal":
            goal_before = await conn.fetchrow(
                "SELECT * FROM goals WHERE account_id = $1", account_id
            )
            if goal_before is not None and goal_before["name"] != owner_name:
                goal_after = await conn.fetchrow_required(
                    "UPDATE goals SET name = $1, updated_at = now() WHERE id = $2 RETURNING *",
                    owner_name,
                    goal_before["id"],
                )
                group.record_updated("goals", goal_before["id"], goal_before, goal_after)

        if updates:
            before_base = {key: value for key, value in before.items() if key != "balance_paise"}
            if before["kind"] in ("person", "goal"):
                before_base["name"] = None
            columns = list(updates)
            assignments = [
                f"{quote_ident(column)} = ${index}" for index, column in enumerate(columns, 1)
            ]
            after = await conn.fetchrow_required(
                f"""
                UPDATE accounts SET {", ".join(assignments)}, updated_at = now()
                WHERE id = ${len(columns) + 1} RETURNING *
                """,
                *[updates[column] for column in columns],
                account_id,
            )
            group.record_updated("accounts", account_id, before_base, after)

        if not group.entries:
            return await account_payload(conn, before) | {"undo_id": None}

    return await account_payload(conn, await get_account_row(conn, account_id)) | {
        "undo_id": group.group_id
    }


async def archive_account(
    conn: Connection,
    account_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return await update_account(conn, account_id, active=False, actor=actor, message_id=message_id)


async def set_statement_balance(
    conn: Connection,
    account_id: str,
    *,
    balance_paise: int,
    as_of: date | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Set the last stated balance and record the difference against Equity, visibly."""
    account = await get_account_row(conn, account_id)
    if not account["active"]:
        raise InvalidLedgerError(f"Account {account['name']} is archived")
    if account["kind"] in SYSTEM_KINDS:
        raise InvalidLedgerError("The system accounts have no statement balance")
    _validate_balance_date(as_of)
    await ensure_system_accounts(conn)
    as_of = as_of or today()
    current = int(account["balance_paise"])
    difference = int(balance_paise) - current

    movement_id = None
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        if difference:
            equity = await system_account(conn, "equity")
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
            record = await create_movement_in_group(
                conn,
                group,
                date=as_of,
                description=f"Balance adjustment for {account['name']}",
                kind="adjustment",
                transfers=[transfer],
            )
            movement_id = record.id

        before_base = {key: value for key, value in account.items() if key != "balance_paise"}
        if account["kind"] in ("person", "goal"):
            before_base["name"] = None
        after = await conn.fetchrow_required(
            """
            UPDATE accounts
            SET statement_balance_paise = $1,
                statement_balance_as_of = $2,
                updated_at = now()
            WHERE id = $3 RETURNING *
            """,
            balance_paise,
            as_of,
            account_id,
        )
        group.record_updated("accounts", account_id, before_base, after)

    payload_row = (
        {**after, "name": account["name"]} if account["kind"] in ("person", "goal") else after
    )
    return {
        "account": await account_payload(conn, payload_row),
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


async def account_summary(conn: Connection) -> dict[str, Any]:
    accounts = await list_accounts(conn)
    liquid_total = sum(
        account["balance_paise"] for account in accounts if account["liquid"] and account["active"]
    )
    active_cards = [
        account for account in accounts if account["kind"] == "card" and account["active"]
    ]
    known_cards = [
        account for account in active_cards if account["card_outstanding_paise"] is not None
    ]
    # One unknown card makes the headline incomplete rather than silently zero.
    card_owed = (
        None
        if len(known_cards) != len(active_cards)
        else sum(int(account["card_outstanding_paise"]) for account in known_cards)
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


async def loan_terms(conn: Connection, name: str) -> dict[str, Any]:
    """The terms `amortize` uses for a named loan: today's outstanding balance, its rate, its
    tenure as the cap, and the EMI its schedule really charges. The eval grader reads the same
    terms, so an expected value can't drift from what the tool computes."""
    row = await conn.fetchrow(
        "SELECT * FROM v_accounts WHERE lower(name) = lower($1) AND kind = 'loan' LIMIT 1",
        name,
    )
    if row is None:
        raise LoanTermsError(f"No loan named {name!r}")
    # A loan is a liability: its ledger balance is negative, as in `account_payload`. Reading it
    # unsigned made every named loan look paid off, so scripts re-derived the terms by hand.
    principal = max(-(await ledger_balance(conn, row["ledger_account_id"])), 0)
    if principal <= 0:
        raise LoanTermsError("That loan has no outstanding balance")
    if not row["tenure_months"]:
        raise LoanTermsError("That loan has no tenure; pass the terms instead")
    emi = None
    if row["emi_schedule_id"]:
        schedule = await conn.fetchrow(
            "SELECT amount_paise FROM schedules WHERE id = $1", row["emi_schedule_id"]
        )
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
