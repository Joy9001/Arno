"""Recoverable ingestion: replayed uploads and failed refreshes."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from arno.importers.commit import commit_import
from arno.importers.extract import finalize_extracted
from arno.importers.pipeline import create_import
from arno.ledger.accounts import create_account, set_statement_balance
from arno.ledger.audit import new_id
from arno.ledger.errors import InvalidLedgerError
from tests.fixtures import import_helpers as helpers
from tests.import_fixtures import sbi_csv, statement_from_csv


async def _attachment(
    conn,
    payload: bytes,
    *,
    name: str = "sbi.csv",
    content_type: str = "text/csv",
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


async def _account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


async def _upload(client, payload: bytes, name: str = "sbi.csv") -> dict:
    response = client.post(
        "/api/attachments",
        files={"file": (name, payload, "application/pdf" if name.endswith(".pdf") else "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- C19 box 1: the same bytes never book twice -------------------------------------------


async def test_replaying_the_same_statement_bytes_books_one_import_and_one_set_of_movements(
    session_client, ledger
):
    account = await _account(ledger)
    first_upload = await _upload(session_client, sbi_csv())
    second_upload = await _upload(session_client, sbi_csv())
    assert first_upload["sha256"] == second_upload["sha256"]

    first = await create_import(ledger, attachment_id=first_upload["id"], account_id=account["id"])
    replay = await create_import(
        ledger, attachment_id=second_upload["id"], account_id=account["id"]
    )
    assert replay["id"] == first["id"]
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 1

    assert await helpers.read_csv(ledger, first["id"]) == "review"
    await commit_import(ledger, first["id"])
    booked = await ledger.fetchval("SELECT count(*) FROM movements")

    again = await create_import(
        ledger,
        attachment_id=(await _upload(session_client, sbi_csv()))["id"],
        account_id=account["id"],
    )
    assert again["id"] == first["id"]
    assert again["status"] == "committed"
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 1
    assert await ledger.fetchval("SELECT count(*) FROM movements") == booked


# --- C19 box 3: a failed refresh keeps the old date and reconciles nothing ----------------


async def test_a_failed_refresh_keeps_the_old_statement_date_visible_and_reconciles_nothing(
    session_client, ledger
):
    account = await _account(ledger)
    await set_statement_balance(
        ledger, account["id"], balance_paise=30_000_000, as_of=date(2026, 5, 31)
    )
    attachment_id = await _attachment(ledger, sbi_csv())
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]

    data = sbi_csv()
    extracted = statement_from_csv(data, **helpers.PROFILES["sbi"])
    extracted.closing = 1
    booked = await ledger.fetchval("SELECT count(*) FROM movements")
    status = await finalize_extracted(ledger, import_id, extracted, text=data.decode())
    assert status == "failed"
    with pytest.raises(InvalidLedgerError):
        await commit_import(ledger, import_id)

    row = await ledger.fetchrow(
        "SELECT statement_balance_paise, statement_balance_as_of FROM accounts WHERE id = $1",
        account["id"],
    )
    assert row["statement_balance_paise"] == 30_000_000
    assert row["statement_balance_as_of"] == date(2026, 5, 31)
    assert await ledger.fetchval("SELECT count(*) FROM movements") == booked
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM statement_lines WHERE import_id = $1", import_id
        )
        == 0
    )

    shown = session_client.get("/api/accounts").json()["accounts"]
    [account_payload] = [item for item in shown if item["id"] == account["id"]]
    assert account_payload["statement_balance_as_of"] == "2026-05-31"
    assert account_payload["statement_balance_paise"] == 30_000_000
    assert account_payload["statement_age_days"] is not None
