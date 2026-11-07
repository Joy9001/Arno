"""Statement imports: rows, accounts, and the step that moves an import to review.

The parser subagent reads the file and submits a script; this module owns the import row,
resolving it to an account, and writing the reviewed rows. Nothing reaches the ledger before a
commit.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fin_agent.db.conn import Connection, connect
from fin_agent.importers.checks import opening_is_evidenced
from fin_agent.importers.identifiers import scrub_account_number
from fin_agent.importers.passwords import PASSWORDS
from fin_agent.importers.readers import Doc
from fin_agent.importers.reconcile import credit_decision_required, insert_lines, reconcile
from fin_agent.ledger.audit import new_id

logger = logging.getLogger("fin_agent.importers")


async def _account_for_row(conn: Connection, import_row: dict[str, Any]) -> dict[str, Any] | None:
    if not import_row.get("account_id"):
        return None
    row = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", import_row["account_id"])
    return row


async def _real_accounts(conn: Connection) -> list[dict[str, Any]]:
    return await conn.fetch(
        """
        SELECT * FROM accounts
        WHERE active AND kind IN ('bank', 'card', 'wallet')
        ORDER BY kind, name
        """
    )


async def _infer_account(conn: Connection, doc: Doc) -> dict[str, Any] | None:
    accounts = await _real_accounts(conn)
    if len(accounts) == 1:
        return accounts[0]
    text = ""
    try:
        if doc.kind == "pdf":
            from fin_agent.importers.layout_detect import line_text, text_lines

            for page in range(min(doc.page_count(), 1)):
                text += "\n".join(line_text(line) for line in text_lines(doc.words(page)))
        elif doc.kind in ("xlsx", "xls", "xlsb", "ods", "text"):
            text = "\n".join(
                " ".join(str(cell) for cell in row if cell is not None) for row in doc.cells(0)[:15]
            )
    except Exception:  # noqa: BLE001 - inference is best effort
        return None
    lowered = text.lower()
    for account in accounts:
        if account["last4"] and account["last4"] in text:
            return account
    for account in accounts:
        for needle in (account["institution"], account["name"]):
            if needle and str(needle).lower() in lowered:
                return account
    return None


async def _resolve_parsed_account(
    conn: Connection,
    *,
    parsed_account: dict[str, Any],
    current: dict[str, Any] | None,
    doc: Doc,
) -> dict[str, Any] | None:
    if current is not None:
        return current
    accounts = await _real_accounts(conn)
    last4 = scrub_account_number(parsed_account.get("account_number"))
    if last4:
        for account in accounts:
            if account["last4"] == last4:
                return account
    haystack = (
        f"{parsed_account.get('name') or ''} {parsed_account.get('institution') or ''}".lower()
    )
    for account in accounts:
        for needle in (account["institution"], account["name"], account["last4"]):
            if needle and str(needle).lower() in haystack:
                return account
    return await _infer_account(conn, doc)


async def create_import(
    conn: Connection,
    *,
    attachment_id: str,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Create the import row for an upload, or return the one already importing that file."""
    attachment = await conn.fetchrow("SELECT * FROM attachments WHERE id = $1", attachment_id)
    if attachment is None:
        raise ValueError("No such attachment")
    existing = await conn.fetchrow(
        """
        SELECT i.*
        FROM imports i
        JOIN attachments a ON a.id = i.attachment_id
        WHERE a.sha256 = $1
          AND i.status IN ('parsing', 'needs_password', 'needs_input', 'review', 'committed')
        ORDER BY i.created_at DESC
        LIMIT 1
        """,
        attachment["sha256"],
    )
    if existing is not None:
        return dict(existing)
    async with conn.transaction():
        import_id = await new_id(conn, "imp")
        row = await conn.fetchrow(
            """
            INSERT INTO imports
                (id, file_hash, status, attachment_id, account_id, parser, commit_groups)
            VALUES ($1, $2, 'parsing', $3, $4, NULL, '[]'::jsonb)
            RETURNING *
            """,
            import_id,
            attachment["sha256"],
            attachment_id,
            account_id,
        )
        await conn.execute(
            "UPDATE attachments SET import_id = $1 WHERE id = $2",
            import_id,
            attachment_id,
        )
    return dict(row)


async def retry_with_password(conn: Connection, *, import_id: str, password: str) -> None:
    """Put the card's password in the in-memory vault; the waiting parser picks it up."""
    PASSWORDS.put(import_id, password)
    await conn.execute("UPDATE imports SET updated_at = now() WHERE id = $1", import_id)


async def _set_status(
    conn: Connection,
    import_id: str,
    status: str,
    *,
    reason: str | None = None,
    **fields: Any,
) -> None:
    assignments = ["status = $1", "reason = $2"]
    params: list[Any] = [status, reason]
    for key, value in fields.items():
        assignments.append(f"{key} = ${len(params) + 1}")
        params.append(value)
    params.append(import_id)
    await conn.execute(
        f"UPDATE imports SET {', '.join(assignments)} WHERE id = ${len(params)}",  # noqa: S608 - fixed keys
        *params,
    )


async def _finalize(
    conn: Connection,
    *,
    import_id: str,
    account: dict[str, Any],
    parsed_account: dict[str, Any],
    check: dict[str, Any],
    script: str,
    helper_version: str | None,
    fingerprint: str | None,
    period: dict[str, Any] | None,
) -> dict[str, Any]:
    """Write the reviewed rows and move the import to `review`."""
    async with conn.transaction():
        await conn.execute("DELETE FROM statement_lines WHERE import_id = $1", import_id)
        await conn.execute(
            """
            UPDATE imports
            SET account_id = $1, period_start = $2, period_end = $3,
                opening_balance_paise = $4, closing_balance_paise = $5,
                check_result = $6, parser = $7, helper_version = $8,
                fingerprint = $9, institution = $10, status = 'review', reason = NULL
            WHERE id = $11
            """,
            account["id"],
            date.fromisoformat(period["start"]) if period else None,
            date.fromisoformat(period["end"]) if period else None,
            check.get("opening_paise"),
            check.get("closing_paise"),
            check,
            script,
            helper_version,
            fingerprint,
            account["institution"] or account["name"] or parsed_account.get("institution"),
            import_id,
        )
        await insert_lines(
            conn,
            import_id=import_id,
            account=account,
            parsed_account=parsed_account,
        )
        period_dates = None
        if period:
            period_dates = (date.fromisoformat(period["start"]), date.fromisoformat(period["end"]))
        review = await reconcile(
            conn,
            import_id=import_id,
            account=account,
            period=period_dates,
        )
    return review


async def _store_unlocked(attachment_id: str, data: bytes, docs: list[Doc]) -> None:
    """Keep the unlocked copy once the password opened it, so a replay needs no password."""
    from fin_agent.importers.readers import sniff

    if not docs or sniff(data) == "zip":
        return
    try:
        unlocked = docs[0].readable_bytes()
    except Exception:  # noqa: BLE001 - the original stays readable with the password
        logger.warning("could not store an unlocked copy for attachment %s", attachment_id)
        return
    if unlocked == data:
        return
    async with connect() as conn:
        await conn.execute(
            """
            UPDATE attachments SET data = $1, unlocked_at = now() WHERE id = $2
            """,
            unlocked,
            attachment_id,
        )


def _account_fingerprint(account: dict[str, Any] | None) -> str | None:
    """A card statement has no column headers; key its layout by the card instead."""
    if not account or not account.get("last4"):
        return None
    return f"{account['kind']}|{account.get('institution') or account['name']}|{account['last4']}"


def _fingerprint_from_headers(parsed: dict[str, Any]) -> str | None:
    from fin_agent.importers.layout import normalize_headers

    headers = parsed.get("headers")
    return normalize_headers(headers) if headers else None


async def import_payload(conn: Connection, import_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    if row is None:
        raise ValueError("No such import")
    account = None
    account_row = None
    if row["account_id"]:
        fetched = await conn.fetchrow(
            """
            SELECT id, name, kind, institution, last4,
                   statement_balance_paise, statement_balance_as_of
            FROM accounts WHERE id = $1
            """,
            row["account_id"],
        )
        if fetched is not None:
            account_row = dict(fetched)
            account = {
                key: account_row[key] for key in ("id", "name", "kind", "institution", "last4")
            }
    lines = await conn.fetch(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
        import_id,
    )
    attachment = await conn.fetchrow(
        """
        SELECT id, filename, content_type, size_bytes, sha256, purged_at, unlocked_at
        FROM attachments WHERE import_id = $1 ORDER BY created_at DESC LIMIT 1
        """,
        import_id,
    )
    attachment_payload = None
    if attachment is not None:
        attachment_payload = dict(attachment)
        for key in ("purged_at", "unlocked_at"):
            value = attachment_payload[key]
            attachment_payload[key] = value.isoformat() if value else None
    check = row["check_result"] or {}
    opening_evidenced = opening_is_evidenced(
        check=check,
        review=row["review"] or {},
        account=account_row,
        period_start=row["period_start"],
    )
    opening_required = (
        row["status"] in ("review", "needs_input", "failed")
        and not opening_evidenced
        and (bool(lines) or "opening" in str(row["reason"] or "").lower())
    )
    return {
        "import_id": row["id"],
        "status": row["status"],
        "attachment": attachment_payload,
        "account": account,
        "period": {
            "start": row["period_start"].isoformat() if row["period_start"] else None,
            "end": row["period_end"].isoformat() if row["period_end"] else None,
        }
        if row["period_start"] or row["period_end"]
        else None,
        "opening_paise": row["opening_balance_paise"],
        "closing_paise": row["closing_balance_paise"],
        "check": check.get("status") if check else None,
        "check_detail": check or None,
        "opening_source": check.get("opening_source"),
        "opening_evidenced": opening_evidenced,
        "opening_required": opening_required,
        "counts": row["counts"],
        "review": row["review"],
        "reason": row["reason"],
        "parser_saved": row["layout_id"] is not None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "committed_at": row["committed_at"].isoformat() if row["committed_at"] else None,
        "rejected_at": row["rejected_at"].isoformat() if row["rejected_at"] else None,
        "undone_at": row["undone_at"].isoformat() if row["undone_at"] else None,
        "lines": [
            {
                "id": line["id"],
                "date": line["date"].isoformat(),
                "description": line["description_raw"],
                "amount_paise": line["amount_paise"],
                "debit_paise": line["debit_paise"],
                "credit_paise": line["credit_paise"],
                "balance_paise": line["balance_paise"],
                "upi_ref": line["upi_ref"],
                "match_state": line["match_state"],
                "movement_id": line["movement_id"],
                "counterparty": (line["match_detail"] or {}).get("counterparty"),
                "suggested_category": (line["match_detail"] or {}).get("suggested_category"),
                "suggested_transfer_account_id": (line["match_detail"] or {}).get(
                    "suggested_transfer_account_id"
                ),
                "transfer_confirmed": bool((line["match_detail"] or {}).get("transfer_confirmed")),
                "credit_kind": (line["match_detail"] or {}).get("credit_kind"),
                "credit_source_account_id": (line["match_detail"] or {}).get(
                    "credit_source_account_id"
                ),
                "credit_decision_required": credit_decision_required(
                    line, account["kind"] if account else None
                ),
                "candidates": (line["match_detail"] or {}).get("candidates", [])
                if line["match_state"] == "ambiguous"
                else [],
            }
            for line in lines
        ],
    }


async def list_imports(conn: Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT i.*, a.filename, acc.name AS account_name
        FROM imports i
        LEFT JOIN attachments a ON a.import_id = i.id
        LEFT JOIN accounts acc ON acc.id = i.account_id
        ORDER BY i.created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [
        {
            "import_id": row["id"],
            "status": row["status"],
            "filename": row["filename"],
            "account": row["account_name"],
            "period": {
                "start": row["period_start"].isoformat() if row["period_start"] else None,
                "end": row["period_end"].isoformat() if row["period_end"] else None,
            }
            if row["period_start"] or row["period_end"]
            else None,
            "check": (row["check_result"] or {}).get("status"),
            "counts": row["counts"],
            "reason": row["reason"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "committed_at": row["committed_at"].isoformat() if row["committed_at"] else None,
            "undone_at": row["undone_at"].isoformat() if row["undone_at"] else None,
        }
        for row in rows
    ]


__all__ = [
    "create_import",
    "import_payload",
    "list_imports",
    "retry_with_password",
]
