"""Statement imports: rows, accounts, and the step that moves an import to review.

Extraction (`agent/extractor.py`) reads the file; this module owns the import row, resolving it
to an account, and writing the reviewed rows. Nothing reaches the ledger before a commit.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from arno.db.conn import Connection
from arno.importers.balance_plan import (
    closing_adjustment_reason,
    closing_source_reason,
    plan_balances,
    stated_balance_reason,
)
from arno.importers.checks import (
    check_statement,
    opening_is_evidenced,
    stated_opening_to_ledger,
)
from arno.importers.identifiers import scrub_account_number
from arno.importers.reconcile import credit_decision_required, insert_lines, reconcile
from arno.ledger.audit import new_id

logger = logging.getLogger("arno.importers")


async def _account_for_row(conn: Connection, import_row: dict[str, Any]) -> dict[str, Any] | None:
    if not import_row.get("account_id"):
        return None
    row = await conn.fetchrow("SELECT * FROM v_accounts WHERE id = $1", import_row["account_id"])
    return row


async def _real_accounts(conn: Connection) -> list[dict[str, Any]]:
    """Every account a statement can be filed against.

    Cash is a first-class account kind, so a statement the user keeps for it can be filed.
    Loan and asset accounts stay out: their statements are not a list of movements.
    """
    return await conn.fetch(
        """
        SELECT * FROM v_accounts
        WHERE active AND kind IN ('bank', 'card', 'wallet', 'cash')
        ORDER BY kind, name
        """
    )


async def _infer_account(conn: Connection, text: str) -> dict[str, Any] | None:
    accounts = await _real_accounts(conn)
    if len(accounts) == 1:
        return accounts[0]
    lowered = text.lower()
    # Score the clues instead of taking the first hit. A statement's own bank is usually in its
    # header block, while a row can name another account ("TRANSFER TO SAVINGS 4821" is the other
    # account's last four, and a wallet's "CASHBACK" contains an account named "Cash"). Filing a
    # statement against the wrong account moves money in the wrong place, so the header weighs
    # most and every clue adds up.
    head = "\n".join(lowered.splitlines()[:4])

    def named(needle: str, haystack: str) -> bool:
        """A whole-word match: "SBI" must not match "subsidy"."""
        pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
        return re.search(pattern, haystack) is not None

    best: dict[str, Any] | None = None
    best_score = 0
    for account in accounts:
        institution = str(account["institution"] or "").lower()
        name = str(account["name"] or "").lower()
        last4 = str(account["last4"] or "")
        score = 0
        if institution:
            score += 8 if named(institution, head) else (4 if named(institution, lowered) else 0)
        if last4:
            score += 3 if last4 in text[:400] else (2 if last4 in text else 0)
        if name:
            score += 2 if named(name, head) else (1 if named(name, lowered) else 0)
        if score > best_score:
            best, best_score = account, score
    return best


async def _resolve_parsed_account(
    conn: Connection,
    *,
    parsed_account: dict[str, Any],
    current: dict[str, Any] | None,
    text: str,
) -> dict[str, Any] | None:
    if current is not None:
        # An account already chosen — inferred at open, or answered on the import card — is the
        # user's; the extraction's own naming cannot overrule it.
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
    return await _infer_account(conn, text)


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
    own = await conn.fetchrow(
        """
        SELECT * FROM imports
        WHERE attachment_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        attachment_id,
    )
    if own is not None:
        # One attachment is one import, whatever its status, so a file the user rejected never
        # becomes a second import.
        return dict(own)
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
                (id, file_hash, status, attachment_id, account_id, commit_groups)
            VALUES ($1, $2, 'parsing', $3, $4, '[]'::jsonb)
            ON CONFLICT (attachment_id) WHERE attachment_id IS NOT NULL DO NOTHING
            RETURNING *
            """,
            import_id,
            attachment["sha256"],
            attachment_id,
            account_id,
        )
        if row is None:
            row = await conn.fetchrow_required(
                "SELECT * FROM imports WHERE attachment_id = $1",
                attachment_id,
            )
    return dict(row)


async def set_progress(conn: Connection, import_id: str, progress: str | None) -> None:
    """Write the card's live line. Only host-made text belongs here, never a model's."""
    await conn.execute(
        """
        UPDATE imports SET progress = $1
        WHERE id = $2 AND status NOT IN ('rejected', 'committed')
        """,
        progress,
        import_id,
    )


async def _set_status(
    conn: Connection,
    import_id: str,
    status: str,
    *,
    reason: str | None = None,
    check: dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    assignments = ["status = $1", "reason = $2", "progress = NULL"]
    params: list[Any] = [status, reason]
    if check is not None:
        fields["check_result"] = {key: value for key, value in check.items() if key != "status"}
        fields["check_status"] = check.get("status")
    for key, value in fields.items():
        assignments.append(f"{key} = ${len(params) + 1}")
        params.append(value)
    params.append(import_id)
    # A rejected import is the user's decision. An extraction that is still running must not move it
    # back to parsing or review with a late write.
    await conn.execute(
        f"UPDATE imports SET {', '.join(assignments)} "  # noqa: S608 - fixed keys
        f"WHERE id = ${len(params)} AND status NOT IN ('rejected', 'committed')",
        *params,
    )


async def _finalize(
    conn: Connection,
    *,
    import_id: str,
    account: dict[str, Any],
    parsed_account: dict[str, Any],
    check: dict[str, Any],
    period: dict[str, Any] | None,
    status: str = "review",
    reason: str | None = None,
) -> bool:
    """Write the reviewed rows and move the import to `status`.

    Returns False when the import was rejected (or committed) while extraction was still
    working: the user's decision stands and the rows are not written.
    """
    async with conn.transaction():
        finalized = await conn.fetchval(
            """
            UPDATE imports
            SET account_id = $1, period_start = $2, period_end = $3,
                opening_balance_paise = $4, closing_balance_paise = $5,
                check_result = $6::jsonb - 'status', check_status = $6::jsonb ->> 'status',
                institution = $7, status = $9, reason = $10, progress = NULL
            WHERE id = $8 AND status NOT IN ('rejected', 'committed')
            RETURNING id
            """,
            account["id"],
            date.fromisoformat(period["start"]) if period else None,
            date.fromisoformat(period["end"]) if period else None,
            check.get("opening_paise"),
            check.get("closing_paise"),
            check,
            account["institution"] or account["name"] or parsed_account.get("institution"),
            import_id,
            status,
            reason,
        )
        if finalized is None:
            return False
        await conn.execute("DELETE FROM statement_lines WHERE import_id = $1", import_id)
        await insert_lines(
            conn,
            import_id=import_id,
            account=account,
            parsed_account=parsed_account,
        )
        period_dates = None
        if period:
            period_dates = (date.fromisoformat(period["start"]), date.fromisoformat(period["end"]))
        await reconcile(
            conn,
            import_id=import_id,
            account=account,
            period=period_dates,
        )
    return True


async def categorize_state(conn: Connection, import_id: str) -> dict[str, Any] | None:
    """Where this import's categorization stands, from `imports.categorize`."""
    stored = await conn.fetchval("SELECT categorize FROM imports WHERE id = $1", import_id)
    if not stored:
        return None
    return {
        "labeled": None,
        "uncategorized": None,
        "failed_payees": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
        **stored,
    }


async def import_payload(conn: Connection, import_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    if row is None:
        raise ValueError("No such import")
    account = None
    account_row = None
    if row["account_id"]:
        fetched = await conn.fetchrow(
            """
            SELECT id, name, kind, institution, last4, ledger_account_id,
                   statement_balance_paise, statement_balance_as_of
            FROM v_accounts WHERE id = $1
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
        SELECT id, filename, content_type, size_bytes, sha256, purged_at, unlocked_at,
               (data IS NOT NULL) AS has_data
        FROM attachments WHERE id = $1
        """,
        row["attachment_id"],
    )
    attachment_payload = None
    file_present = False
    if attachment is not None:
        attachment_payload = dict(attachment)
        file_present = (
            bool(attachment_payload.pop("has_data")) and attachment_payload["purged_at"] is None
        )
        for key in ("purged_at", "unlocked_at"):
            value = attachment_payload[key]
            attachment_payload[key] = value.isoformat() if value else None
    check = row["check_result"] or {}
    opening_evidenced = opening_is_evidenced(
        check=check,
        check_status=row["check_status"],
        review=row["review"] or {},
        account=account_row,
        period_start=row["period_start"],
    )
    # A merge edits a logged movement to the statement's own date. The review shows the date it
    # is about to replace, so a one-day mismatch is evidence, not a silent edit.
    movement_dates = {
        str(found["id"]): found["date"]
        for found in await conn.fetch(
            "SELECT id, date FROM movements WHERE id = ANY($1::text[])",
            [str(line["movement_id"]) for line in lines if line["movement_id"]],
        )
    }
    opening_required = (
        row["status"] in ("review", "needs_input", "failed")
        and not opening_evidenced
        and (bool(lines) or "opening" in str(row["reason"] or "").lower())
    )
    categorize = None
    if row["status"] == "committed":
        categorize = await categorize_state(conn, import_id)
    closing_adjustment = 0
    closing_adjustment_note = None
    stated_balance = None
    closing_source_note = None
    if (
        row["status"] in ("review", "needs_input")
        and account_row is not None
        and row["check_status"] == "VERIFIED"
    ):
        plan = await plan_balances(conn, account=account_row, row=row, lines=lines)
        closing_adjustment = plan.closing_adjustment_paise
        if closing_adjustment and plan.period_end is not None:
            closing_adjustment_note = closing_adjustment_reason(closing_adjustment, plan.period_end)
        # A stated balance the statement's own rows disagree with is a confirmation, not a
        # silent replacement: the payload says both figures before anything is committed.
        stated_balance = plan.stated_balance
        if check.get("closing_source") == "last running balance" and isinstance(
            row["closing_balance_paise"], int
        ):
            closing_source_note = closing_source_reason(int(row["closing_balance_paise"]))
    stated_day = account_row["statement_balance_as_of"] if account_row else None
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
        "check": row["check_status"],
        "check_detail": row["check_result"],
        "closing_adjustment_paise": closing_adjustment,
        "closing_adjustment_reason": closing_adjustment_note,
        "stated_balance_paise": stated_balance[0] if stated_balance else None,
        "stated_balance_as_of": (
            stated_day.isoformat() if stated_balance and stated_day is not None else None
        ),
        "stated_balance_reason": (
            stated_balance_reason(stated_balance[0], stated_day, stated_balance[1])
            if stated_balance and stated_day is not None
            else None
        ),
        "closing_source_note": closing_source_note,
        "opening_source": check.get("opening_source"),
        "opening_evidenced": opening_evidenced,
        "opening_required": opening_required,
        "counts": row["counts"],
        "review": row["review"],
        "reason": row["reason"],
        "recovery_action": "reupload" if row["status"] == "failed" else None,
        # The stored file is still here, so extraction can run again on the same import.
        "can_retry_parse": row["status"] == "failed" and file_present,
        "categorize": categorize,
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
                "ledger_date": (
                    movement_dates[str(line["movement_id"])].isoformat()
                    if str(line["movement_id"]) in movement_dates
                    and movement_dates[str(line["movement_id"])] != line["date"]
                    else None
                ),
                "counterparty": (line["match_detail"] or {}).get("counterparty"),
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


async def set_manual_opening(
    conn: Connection, *, import_id: str, opening_paise: int
) -> dict[str, Any]:
    """Record the opening the user states, then re-check the stored rows against it.

    A first card statement prints no opening, so the review screen asks for one. `opening_paise`
    is what the user typed: an amount owed on a card, a balance on any other account. It is
    converted to the ledger's sign once, here, and the answer is evidence only if the rows still
    add up to the printed closing; otherwise the check says why.
    """
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    if row is None:
        raise ValueError("No such import")
    if row["status"] not in ("review", "needs_input", "failed"):
        raise ValueError(f"That import is {row['status']}")
    if row["account_id"] is None:
        raise ValueError("Choose the account for this statement first")
    account = await conn.fetchrow("SELECT kind FROM accounts WHERE id = $1", row["account_id"])
    opening_paise = stated_opening_to_ledger(opening_paise, account["kind"] if account else None)
    lines = await conn.fetch(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id", import_id
    )
    rows = [
        {
            "date": line["date"].isoformat(),
            "debit": int(line["debit_paise"] or 0),
            "credit": int(line["credit_paise"] or 0),
            "balance": line["balance_paise"],
        }
        for line in lines
    ]
    check = dict(row["check_result"] or {})
    check_status = row["check_status"]
    if rows:
        period = None
        if row["period_start"] and row["period_end"]:
            period = [row["period_start"].isoformat(), row["period_end"].isoformat()]
        recomputed = check_statement(
            rows,
            row["opening_balance_paise"],
            row["closing_balance_paise"],
            period=period,
            manual_opening=opening_paise,
        )
        check = recomputed
        check_status = check["status"]
    review = dict(row["review"] or {})
    review["manual_opening_paise"] = opening_paise
    status = row["status"]
    reason = row["reason"]
    if rows:
        status = "review" if check.get("status") == "VERIFIED" else "needs_input"
        reason = None if status == "review" else check.get("reason")
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE imports
            SET review = $1, check_result = $2::jsonb - 'status', check_status = $7,
                opening_balance_paise = $3, status = $4, reason = $5, updated_at = now()
            WHERE id = $6
            """,
            review,
            check,
            opening_paise,
            status,
            reason,
            import_id,
            check_status,
        )
    return await import_payload(conn, import_id)


async def list_imports(conn: Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT i.*, a.filename, acc.name AS account_name,
               (a.data IS NOT NULL AND a.purged_at IS NULL) AS file_present
        FROM imports i
        LEFT JOIN attachments a ON a.id = i.attachment_id
        LEFT JOIN v_accounts acc ON acc.id = i.account_id
        ORDER BY i.created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [import_list_item(row) for row in rows]


def import_list_item(row: Any) -> dict[str, Any]:
    """One import in a list: the shape `GET /api/imports` and `GET /api/import-history` share.

    `row` carries the import's columns plus `filename`, `account_name`, and `file_present`.
    """
    return {
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
        "check": row["check_status"],
        "counts": row["counts"],
        "reason": row["reason"],
        "recovery_action": "reupload" if row["status"] == "failed" else None,
        "can_retry_parse": row["status"] == "failed" and bool(row["file_present"]),
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "committed_at": row["committed_at"].isoformat() if row["committed_at"] else None,
        "undone_at": row["undone_at"].isoformat() if row["undone_at"] else None,
    }


__all__ = [
    "create_import",
    "import_list_item",
    "import_payload",
    "list_imports",
    "set_manual_opening",
    "set_progress",
]
