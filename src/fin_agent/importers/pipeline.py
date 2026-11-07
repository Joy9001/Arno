"""The import pipeline: open, known layout or parse run, checks, dedupe, reconcile.

Stages run as a job off the chat path. Nothing reaches the ledger before a commit.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from fin_agent.db.conn import Connection, connect
from fin_agent.importers.categorizer import categorize_import
from fin_agent.importers.checks import (
    INPUT_REASONS,
    account_check,
    check_statement,
    opening_is_evidenced,
)
from fin_agent.importers.helpers import HELPER_VERSION
from fin_agent.importers.identifiers import scrub_account_number
from fin_agent.importers.layout import detect_fingerprint
from fin_agent.importers.layouts import layout_for, touch_layout
from fin_agent.importers.monty import ScriptContext, normalize_accounts, run_script
from fin_agent.importers.passwords import PASSWORDS
from fin_agent.importers.periods import derive_period, ledger_last_date, ledger_opening
from fin_agent.importers.readers import Doc, DocError, open_documents
from fin_agent.importers.reconcile import credit_decision_required, insert_lines, reconcile
from fin_agent.jobs.runner import enqueue_job
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import today as ledger_today

logger = logging.getLogger("fin_agent.importers")

PARSER_RUNNER = Callable[
    [Doc, ScriptContext],
    Awaitable[Any],
]


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
    """Create an import and its job in one transaction, or return the one already importing it."""
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
        await enqueue_job(
            conn,
            key=f"import:{import_id}",
            kind="import_statement",
            payload={"import_id": import_id},
        )
    return dict(row)


async def retry_with_password(conn: Connection, *, import_id: str, password: str) -> None:
    PASSWORDS.put(import_id, password)
    await conn.execute(
        """
        UPDATE imports SET status = 'parsing', reason = NULL WHERE id = $1
        """,
        import_id,
    )
    await enqueue_job(
        conn,
        key=f"import:{import_id}:pw:{datetime.now().isoformat(timespec='microseconds')}",
        kind="import_statement",
        payload={"import_id": import_id},
    )


async def retry_with_account(conn: Connection, *, import_id: str, account_id: str) -> None:
    row = await conn.fetchrow("SELECT id FROM accounts WHERE id = $1 AND active", account_id)
    if row is None:
        raise ValueError("No such account")
    await conn.execute(
        """
        UPDATE imports SET status = 'parsing', account_id = $1, reason = NULL WHERE id = $2
        """,
        account_id,
        import_id,
    )
    await enqueue_job(
        conn,
        key=f"import:{import_id}:account:{datetime.now().isoformat(timespec='microseconds')}",
        kind="import_statement",
        payload={"import_id": import_id},
    )


async def retry_with_opening(conn: Connection, *, import_id: str, opening_paise: int) -> None:
    row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    if row is None:
        raise ValueError("No such import")
    review = dict(row["review"] or {})
    review["manual_opening_paise"] = int(opening_paise)
    lines = await conn.fetch(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id", import_id
    )
    if lines and row["status"] in ("review", "failed"):
        # Recheck the stored rows instead of reparsing: the user's card-credit decisions survive.
        await _recheck_opening(
            conn, row=row, review=review, lines=lines, opening_paise=opening_paise
        )
        return
    await conn.execute(
        """
        UPDATE imports SET status = 'parsing', reason = NULL, review = $1 WHERE id = $2
        """,
        review,
        import_id,
    )
    await enqueue_job(
        conn,
        key=f"import:{import_id}:opening:{datetime.now().isoformat(timespec='microseconds')}",
        kind="import_statement",
        payload={"import_id": import_id},
    )


async def _recheck_opening(
    conn: Connection,
    *,
    row: dict[str, Any],
    review: dict[str, Any],
    lines: list[dict[str, Any]],
    opening_paise: int,
) -> None:
    rows = [
        {
            "date": line["date"].isoformat(),
            "debit": int(line["debit_paise"] or 0),
            "credit": int(line["credit_paise"] or 0),
            "balance": line["balance_paise"],
        }
        for line in lines
    ]
    period = None
    if row["period_start"] and row["period_end"]:
        period = (row["period_start"].isoformat(), row["period_end"].isoformat())
    check = check_statement(
        rows,
        None,
        row["closing_balance_paise"],
        period=period,
        manual_opening=int(opening_paise),
    )
    status = "review"
    reason = None
    if check["status"] != "VERIFIED":
        reason = check.get("reason") or "The opening, rows, and closing do not reconcile."
    await conn.execute(
        """
        UPDATE imports
        SET status = $1, reason = $2, review = $3, check_result = $4,
            opening_balance_paise = $5
        WHERE id = $6
        """,
        status,
        reason,
        review,
        check,
        int(opening_paise),
        row["id"],
    )


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


def _context(
    *,
    account: dict[str, Any] | None,
    period: dict[str, Any] | None,
    opening: dict[str, Any] | None,
    manual_opening_paise: int | None,
    fingerprint: str | None,
    expected_fingerprint: str | None,
) -> ScriptContext:
    return ScriptContext(
        period=(period["start"], period["end"]) if period else None,
        ledger_opening_paise=opening["paise"] if opening else None,
        ledger_opening_as_of=opening["as_of"] if opening else None,
        manual_opening_paise=manual_opening_paise,
        account_name=account["name"] if account else None,
        fingerprint=fingerprint,
        expected_fingerprint=expected_fingerprint,
        allow_vision=True,
    )


def _check_account(
    parsed_account: dict[str, Any],
    *,
    period: dict[str, Any] | None,
    ledger_opening_paise: int | None,
    manual_opening_paise: int | None,
    expected_fingerprint: str | None,
) -> dict[str, Any]:
    return account_check(
        parsed_account,
        period=(period["start"], period["end"]) if period else None,
        ledger_opening=ledger_opening_paise,
        manual_opening=manual_opening_paise,
        expected_fingerprint=expected_fingerprint,
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
    return await _finalize_body(
        conn,
        import_id=import_id,
        account=account,
        parsed_account=parsed_account,
        check=check,
        script=script,
        helper_version=helper_version,
        fingerprint=fingerprint,
        period=period,
    )


async def _finalize_body(
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
            parsed_account.get("institution") or account["institution"] or account["name"],
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


async def _run_import(
    import_id: str,
    *,
    parser_runner: PARSER_RUNNER | None = None,
) -> None:
    """Run one import: read, replay or parse, finalize, categorize."""
    await _run_import_body(import_id, parser_runner=parser_runner)


async def _run_import_body(
    import_id: str,
    *,
    parser_runner: PARSER_RUNNER | None = None,
) -> None:
    async with connect() as conn:
        row = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
        if row["status"] in ("committed", "rejected"):
            return
        attachment = await conn.fetchrow(
            "SELECT * FROM attachments WHERE import_id = $1 ORDER BY created_at DESC LIMIT 1",
            import_id,
        )
        if attachment is None:
            await _set_status(conn, import_id, "failed", reason="the attachment is gone")
            return
        if attachment["data"] is None:
            await _set_status(conn, import_id, "failed", reason="the file was cleaned up")
            return
        data = bytes(attachment["data"])
        account = await _account_for_row(conn, row)
        password = PASSWORDS.pop(import_id)
        await conn.execute(
            """
            UPDATE imports
            SET status = 'parsing', reason = NULL, updated_at = now()
            WHERE id = $1
            """,
            import_id,
        )

    try:
        docs = open_documents(data, password=password)
    except DocError as exc:
        async with connect() as conn:
            if exc.code in ("password_required", "wrong_password"):
                await _set_status(
                    conn,
                    import_id,
                    "needs_password",
                    reason="wrong password"
                    if exc.code == "wrong_password"
                    else "password required",
                )
            else:
                await _set_status(
                    conn, import_id, "failed", reason=f"could not open the file: {exc.code}"
                )
        return
    except Exception as exc:  # noqa: BLE001
        async with connect() as conn:
            await _set_status(
                conn, import_id, "failed", reason=f"could not open the file: {type(exc).__name__}"
            )
        return

    doc = docs[0]
    async with connect() as conn:
        if account is None:
            account = await _infer_account(conn, doc)
        ledger_last = await ledger_last_date(conn, account["id"]) if account else None
        period = derive_period(doc, ledger_last=ledger_last, today=ledger_today())
        opening = (
            await ledger_opening(conn, account["id"], date.fromisoformat(period["start"]))
            if account and period
            else None
        )
        manual_opening = (row.get("review") or {}).get("manual_opening_paise")
        if manual_opening is not None:
            manual_opening = int(manual_opening)
        fingerprint = detect_fingerprint(doc) or _account_fingerprint(account)
        layout = await layout_for(conn, fingerprint)
        if layout is not None and layout["fingerprint"] != fingerprint:
            layout = None
        if layout is not None and layout["helper_version"] != HELPER_VERSION:
            # The saved script ran with another helper block. Replaying it with this one would
            # silently change its meaning, so the layout counts as new.
            layout = None
        if layout is not None and account is None:
            await _set_status(
                conn, import_id, "needs_input", reason="Which account is this statement for?"
            )
            return
        context = _context(
            account=account,
            period=period,
            opening=opening,
            manual_opening_paise=manual_opening,
            fingerprint=fingerprint,
            expected_fingerprint=layout["fingerprint"] if layout else None,
        )

    if layout is not None:
        replayed = await run_script(doc, layout["parser"], context, stage="layout_replay")
        parsed_accounts, reason = normalize_accounts(replayed.output)
        if replayed.output is not None and not reason and parsed_accounts:
            parsed = parsed_accounts[0]
            resolved = account
            check = _check_account(
                parsed,
                period=period,
                ledger_opening_paise=opening["paise"] if opening else None,
                manual_opening_paise=manual_opening,
                expected_fingerprint=layout["fingerprint"],
            )
            if check["status"] == "VERIFIED":
                async with connect() as conn:
                    await _finalize(
                        conn,
                        import_id=import_id,
                        account=resolved,
                        parsed_account=parsed,
                        check=check,
                        script=layout["parser"],
                        helper_version=layout["helper_version"],
                        fingerprint=fingerprint,
                        period=period,
                    )
                    await categorize_import(conn, import_id, resolved)
                    await touch_layout(conn, layout["id"])
                return
            if check.get("reason") in INPUT_REASONS:
                # A missing input cannot be repaired by replaying the layout; ask for it.
                async with connect() as conn:
                    await _set_status(
                        conn,
                        import_id,
                        "needs_input",
                        reason="What was the opening balance?",
                        check_result=check,
                    )
                return

    if parser_runner is None:
        from fin_agent.agent.parse_run import run_parse

        parser_runner = run_parse
    parse_result = await parser_runner(
        doc,
        context,
        account_name=account["name"] if account else None,
    )
    status = parse_result.status
    if status == "not_a_statement":
        async with connect() as conn:
            await _set_status(
                conn,
                import_id,
                "not_a_statement",
                reason=f"This isn't a statement: {parse_result.kind or 'unknown document'}",
            )
        return
    if status == "needs_input":
        async with connect() as conn:
            await _set_status(
                conn,
                import_id,
                "needs_input",
                reason=parse_result.question or "More information is needed",
            )
        return
    if status != "parser_submitted" or not parse_result.script:
        async with connect() as conn:
            await _set_status(
                conn, import_id, "failed", reason=parse_result.reason or "the parse run gave up"
            )
        return

    async with connect() as conn:
        fresh = await run_script(doc, parse_result.script, context, stage="fresh_replay")
        parsed_accounts, reason = normalize_accounts(fresh.output)
        if not parsed_accounts:
            await _set_status(
                conn, import_id, "failed", reason=reason or "the parser returned no accounts"
            )
            return
        parsed = parsed_accounts[0]
        resolved = await _resolve_parsed_account(
            conn, parsed_account=parsed, current=account, doc=doc
        )
        if resolved is None:
            await _set_status(
                conn, import_id, "needs_input", reason="Which account is this statement for?"
            )
            return
        if not period:
            ledger_last = await ledger_last_date(conn, resolved["id"])
            period = derive_period(doc, ledger_last=ledger_last, today=ledger_today())
        if opening is None and period:
            opening = await ledger_opening(
                conn, resolved["id"], date.fromisoformat(period["start"])
            )
        check = _check_account(
            parsed,
            period=period,
            ledger_opening_paise=opening["paise"] if opening else None,
            manual_opening_paise=manual_opening,
            expected_fingerprint=fingerprint,
        )
        if check["status"] != "VERIFIED":
            reason = check.get("reason") or "the checks did not pass"
            if reason in INPUT_REASONS:
                await _set_status(
                    conn,
                    import_id,
                    "needs_input",
                    reason="What was the opening balance?",
                    check_result=check,
                )
                return
            await _set_status(
                conn,
                import_id,
                "failed",
                reason=reason,
                check_result=check,
            )
            return
        await _finalize(
            conn,
            import_id=import_id,
            account=resolved,
            parsed_account=parsed,
            check=check,
            script=parse_result.script,
            helper_version=HELPER_VERSION,
            fingerprint=fingerprint or _fingerprint_from_headers(parsed),
            period=period,
        )
        await categorize_import(conn, import_id, resolved)


def _account_fingerprint(account: dict[str, Any] | None) -> str | None:
    """A card statement has no column headers; key its layout by the card instead."""
    if not account or not account.get("last4"):
        return None
    return f"{account['kind']}|{account.get('institution') or account['name']}|{account['last4']}"


def _fingerprint_from_headers(parsed: dict[str, Any]) -> str | None:
    from fin_agent.importers.layout import normalize_headers

    headers = parsed.get("headers")
    return normalize_headers(headers) if headers else None


async def handle_import_job(payload: dict[str, Any]) -> dict[str, Any]:
    import_id = str(payload.get("import_id") or "")
    if not import_id:
        raise ValueError("import job has no import_id")
    await _run_import(import_id)
    return {"import_id": import_id}


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
        SELECT filename, content_type, size_bytes, sha256, purged_at
        FROM attachments WHERE import_id = $1 ORDER BY created_at DESC LIMIT 1
        """,
        import_id,
    )
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
        "attachment": dict(attachment) if attachment else None,
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
    "handle_import_job",
    "import_payload",
    "list_imports",
    "retry_with_account",
    "retry_with_opening",
    "retry_with_password",
]
