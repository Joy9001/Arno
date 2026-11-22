"""Factories shared by the import tests: accounts, attachments, and a reviewed import."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from arno.importers.periods import ledger_opening as opening_balance
from arno.importers.pipeline import create_import
from arno.importers.reconcile import insert_lines, reconcile
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from tests.fixtures.import_review import review_statement

PERIOD = (date(2026, 4, 1), date(2026, 4, 30))


async def bank(
    conn,
    *,
    name: str = "SBI savings",
    institution: str = "SBI",
    last4: str = "6789",
    opening_paise: int = 100000,
) -> dict:
    created = await create_account(
        conn,
        kind="bank",
        name=name,
        institution=institution,
        last4=last4,
        opening_balance_paise=opening_paise,
        opening_balance_as_of=date(2026, 3, 31),
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def card(
    conn,
    *,
    last4: str = "4242",
    opening_paise: int | None = None,
    credit_limit_paise: int = 500000,
) -> dict:
    created = await create_account(
        conn,
        kind="card",
        name=f"Slice card {last4}",
        institution="Slice",
        last4=last4,
        opening_balance_paise=opening_paise,
        opening_balance_as_of=date(2026, 3, 31) if opening_paise is not None else None,
        credit_limit_paise=credit_limit_paise,
        statement_day=25,
        due_day=5,
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def attachment(
    conn, payload: bytes, *, name: str = "statement.csv", content_type: str = "text/csv"
) -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', $2, $3, $4, $5, $6)
        """,
        attachment_id,
        name,
        content_type,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


def row(
    description: str,
    *,
    debit: int = 0,
    credit: int = 0,
    day: str = "2026-04-01",
    **extra: Any,
) -> dict:
    return {
        "date": day,
        "description": description,
        "debit": debit,
        "credit": credit,
        "balance": None,
        **extra,
    }


async def review_import(
    conn,
    account: dict,
    rows: list[dict],
    *,
    opening: int | None = None,
    closing: int | None = None,
    period: tuple[date, date] = PERIOD,
) -> str:
    """One import in `review`, verified against the ledger's own opening, lines reconciled."""
    if opening is None:
        previous = await opening_balance(conn, account["id"], period[0])
        opening = int(previous["paise"]) if previous else 0
    return await review_statement(
        conn,
        account,
        rows,
        start=period[0],
        end=period[1],
        opening=opening,
        closing=closing,
        check={"opening_source": "manual", "opening_paise": opening, "closing_paise": closing},
        review={"manual_opening_paise": opening},
    )


async def queued_import(
    conn, account: dict, rows: list[dict], *, period: tuple[date, date] = PERIOD
) -> str:
    """An import in `review` whose lines reconcile against the ledger as it stands now."""
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             commit_groups)
        VALUES ($1, $2, 'review', $3, $4, $5, '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
        period[0],
        period[1],
    )
    await insert_lines(conn, import_id=import_id, account=account, parsed_account={"rows": rows})
    await reconcile(conn, import_id=import_id, account=account, period=period)
    return import_id


async def upload(
    conn, payload: bytes, *, account_id: str | None = None, name: str = "statement.csv"
) -> str:
    """Put a statement file in the database and open the import the parser would see."""
    attachment_id = await attachment(conn, payload, name=name)
    row = await create_import(conn, attachment_id=attachment_id, account_id=account_id)
    return row["id"]
