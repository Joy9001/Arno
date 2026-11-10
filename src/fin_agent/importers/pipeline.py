"""Statement imports: rows, accounts, and the step that moves an import to review.

The parser subagent reads the file and submits a script; this module owns the import row,
resolving it to an account, and writing the reviewed rows. Nothing reaches the ledger before a
commit.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from fin_agent.db.conn import Connection, connect
from fin_agent.importers.checks import (
    check_statement,
    opening_is_evidenced,
    stated_opening_to_ledger,
)
from fin_agent.importers.commit import (
    closing_adjustment_reason,
    closing_source_reason,
    review_closing_adjustment,
    stated_balance_conflict,
    stated_balance_reason,
)
from fin_agent.importers.identifiers import scrub_account_number
from fin_agent.importers.passwords import PASSWORDS
from fin_agent.importers.readers import Doc
from fin_agent.importers.reconcile import credit_decision_required, insert_lines, reconcile
from fin_agent.jobs.runner import MAX_ATTEMPTS
from fin_agent.ledger.audit import new_id

logger = logging.getLogger("fin_agent.importers")


async def _account_for_row(conn: Connection, import_row: dict[str, Any]) -> dict[str, Any] | None:
    if not import_row.get("account_id"):
        return None
    row = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", import_row["account_id"])
    return row


async def _real_accounts(conn: Connection) -> list[dict[str, Any]]:
    """Every account a statement can be filed against.

    Cash is a first-class account kind, so a statement the user keeps for it can be filed.
    Loan and asset accounts stay out: their statements are not a list of movements.
    """
    return await conn.fetch(
        """
        SELECT * FROM accounts
        WHERE active AND kind IN ('bank', 'card', 'wallet', 'cash')
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
    doc: Doc,
) -> dict[str, Any] | None:
    if current is not None:
        # An account already chosen — inferred at open, or answered on the import card — is the
        # user's; the script's own naming cannot overrule it.
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
        # One attachment is one import, whatever its status. A parser that asks for this turn's
        # state again must not turn a file the user rejected into a second import.
        return dict(own)
    existing = await conn.fetchrow(
        """
        SELECT i.*
        FROM imports i
        JOIN attachments a ON a.id = i.attachment_id
        WHERE a.sha256 = $1
          AND i.status IN ('parsing', 'needs_password', 'needs_input', 'paused', 'review',
                           'committed')
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
    **fields: Any,
) -> None:
    assignments = ["status = $1", "reason = $2", "progress = NULL"]
    params: list[Any] = [status, reason]
    for key, value in fields.items():
        assignments.append(f"{key} = ${len(params) + 1}")
        params.append(value)
    params.append(import_id)
    # A rejected import is the user's decision. A parser that is still running must not move it
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
    script: str,
    helper_version: str | None,
    fingerprint: str | None,
    period: dict[str, Any] | None,
    status: str = "review",
    reason: str | None = None,
) -> bool:
    """Write the reviewed rows and move the import to `status`.

    Returns False when the import was rejected (or committed) while the parser was still
    working: the user's decision stands and the rows are not written.
    """
    async with conn.transaction():
        finalized = await conn.fetchval(
            """
            UPDATE imports
            SET account_id = $1, period_start = $2, period_end = $3,
                opening_balance_paise = $4, closing_balance_paise = $5,
                check_result = $6, parser = $7, helper_version = $8,
                fingerprint = $9, institution = $10, status = $12, reason = $13,
                progress = NULL
            WHERE id = $11 AND status NOT IN ('rejected', 'committed')
            RETURNING id
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


def _moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def categorize_state(conn: Connection, import_id: str) -> dict[str, Any] | None:
    """Where this import's categorization stands, from its newest job (and one legacy query)."""
    rows = await conn.fetch(
        """
        SELECT * FROM jobs
        WHERE kind IN ('categorize_import', 'collect_batch')
          AND payload ->> 'import_id' = $1
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """,
        import_id,
    )
    if not rows:
        return None
    job = rows[0]
    status = str(job["status"])
    attempts = int(job["attempts"] or 0)
    result = job["result"] or {}

    def state(**overrides: Any) -> dict[str, Any]:
        view: dict[str, Any] = {
            "state": "queued",
            "labeled": None,
            "uncategorized": None,
            "failed_payees": None,
            "error": None,
            # The app shows the attempt in progress, so a failed job shows the one that failed.
            "attempt": attempts if status == "failed" else attempts + 1,
            "max_attempts": MAX_ATTEMPTS,
            "next_run_at": None,
            "started_at": _moment(job["started_at"]),
            "finished_at": _moment(job["finished_at"]),
            "job_id": str(job["id"]),
        }
        view.update(overrides)
        return view

    if job["kind"] == "collect_batch":
        if status == "failed":
            return state(state="failed", error=job["last_error"])
        return state(
            state="waiting_on_batch",
            next_run_at=_moment(job["run_at"]) if status == "pending" else None,
        )

    if status == "pending":
        retrying = attempts > 0
        return state(
            state="retrying" if retrying else "queued",
            next_run_at=_moment(job["run_at"]),
            error=job["last_error"] if retrying else None,
        )
    if status == "running":
        return state(state="running")
    if status == "failed":
        return state(state="failed", error=job["last_error"])

    outcome = result.get("status")
    counts = {
        "labeled": result.get("labeled"),
        "uncategorized": result.get("uncategorized"),
        "failed_payees": result.get("failed_payees"),
    }
    if outcome == "batch_submitted":
        run_id = str(result.get("run_id") or result.get("batch_id") or "")
        pending = next(
            (
                row
                for row in rows
                if row["kind"] == "collect_batch"
                and row["status"] == "pending"
                and str((row["payload"] or {}).get("run_id")) == run_id
            ),
            None,
        )
        return state(
            state="waiting_on_batch",
            next_run_at=_moment(pending["run_at"]) if pending is not None else None,
        )
    if outcome == "partial":
        return state(state="partial", **counts)
    if outcome is not None:
        return state(state="done", **counts)

    # Rows written before `jobs.result` existed: the run row still has the counts.
    run = await conn.fetchrow(
        """
        SELECT * FROM categorize_runs
        WHERE import_id = $1
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        import_id,
    )
    if run is None:
        return state(state="done")
    return state(
        state="done",
        labeled=run["labeled"],
        uncategorized=run["uncategorized"],
        finished_at=_moment(run["finished_at"]),
    )


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
        and check.get("status") == "VERIFIED"
    ):
        closing_adjustment = await review_closing_adjustment(
            conn,
            import_id=import_id,
            account=account_row,
            check=check,
            period_start=row["period_start"],
            period_end=row["period_end"],
        )
        if closing_adjustment:
            closing_adjustment_note = closing_adjustment_reason(
                closing_adjustment, row["period_end"]
            )
        # A stated balance the statement's own rows disagree with is a confirmation, not a
        # silent replacement: the payload says both figures before anything is committed.
        stated_balance = await stated_balance_conflict(
            conn,
            account=account_row,
            check=check,
            lines=lines,
            period_start=row["period_start"],
            period_end=row["period_end"],
        )
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
        "check": check.get("status") if check else None,
        "check_detail": check or None,
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
        "parser_saved": row["layout_id"] is not None,
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
        # The header check passed when the parser was gated; the manual opening cannot undo it.
        for key in ("fingerprint", "header_match"):
            if key in check:
                recomputed.setdefault(key, check[key])
        check = recomputed
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
            SET review = $1, check_result = $2, opening_balance_paise = $3,
                status = $4, reason = $5, updated_at = now()
            WHERE id = $6
            """,
            review,
            check,
            opening_paise,
            status,
            reason,
            import_id,
        )
    return await import_payload(conn, import_id)


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
    "set_manual_opening",
    "set_progress",
]
