"""The import pipeline: open, known layout or parse run, checks, dedupe, reconcile.

Stages run as a job off the chat path. Nothing reaches the ledger before a commit.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fin_agent.db.conn import connect
from fin_agent.importers.categorizer import categorize_import
from fin_agent.importers.checks import account_check
from fin_agent.importers.helpers import HELPER_VERSION
from fin_agent.importers.identifiers import scrub_account_number
from fin_agent.importers.layout import detect_fingerprint
from fin_agent.importers.layouts import layout_for, touch_layout
from fin_agent.importers.monty import ScriptContext, normalize_accounts, run_script
from fin_agent.importers.passwords import PASSWORDS
from fin_agent.importers.periods import derive_period, ledger_last_date, ledger_opening
from fin_agent.importers.readers import Doc, DocError, open_documents
from fin_agent.importers.reconcile import insert_lines, reconcile
from fin_agent.jobs.runner import enqueue_job
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import today as ledger_today

logger = logging.getLogger("fin_agent.importers")

PARSER_RUNNER = Callable[
    [Doc, ScriptContext],
    Awaitable[Any],
]


def _account_for_row(conn: psycopg.Connection, import_row: dict[str, Any]) -> dict[str, Any] | None:
    if not import_row.get("account_id"):
        return None
    row = conn.execute(
        "SELECT * FROM accounts WHERE id = %s", (import_row["account_id"],)
    ).fetchone()
    return row


def _real_accounts(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            """
            SELECT * FROM accounts
            WHERE active AND kind IN ('bank', 'card', 'wallet')
            ORDER BY kind, name
            """
        ).fetchall()
    )


def _infer_account(conn: psycopg.Connection, doc: Doc) -> dict[str, Any] | None:
    accounts = _real_accounts(conn)
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


def _resolve_parsed_account(
    conn: psycopg.Connection,
    *,
    parsed_account: dict[str, Any],
    current: dict[str, Any] | None,
    doc: Doc,
) -> dict[str, Any] | None:
    if current is not None:
        return current
    accounts = _real_accounts(conn)
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
    return _infer_account(conn, doc)


def create_import(
    conn: psycopg.Connection,
    *,
    attachment_id: str,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Create an import and its job in one transaction, or return the one already importing it."""
    attachment = conn.execute(
        "SELECT * FROM attachments WHERE id = %s", (attachment_id,)
    ).fetchone()
    if attachment is None:
        raise ValueError("No such attachment")
    existing = conn.execute(
        """
        SELECT i.*
        FROM imports i
        JOIN attachments a ON a.id = i.attachment_id
        WHERE a.sha256 = %s
          AND i.status IN ('parsing', 'needs_password', 'needs_input', 'review', 'committed')
        ORDER BY i.created_at DESC
        LIMIT 1
        """,
        (attachment["sha256"],),
    ).fetchone()
    if existing is not None:
        return dict(existing)
    _finish_open_transaction(conn)
    with conn.transaction():
        import_id = new_id(conn, "imp")
        row = conn.execute(
            """
            INSERT INTO imports
                (id, file_hash, status, attachment_id, account_id, parser, commit_groups)
            VALUES (%s, %s, 'parsing', %s, %s, NULL, '[]'::jsonb)
            RETURNING *
            """,
            (import_id, attachment["sha256"], attachment_id, account_id),
        ).fetchone()
        conn.execute(
            "UPDATE attachments SET import_id = %s WHERE id = %s",
            (import_id, attachment_id),
        )
        enqueue_job(
            conn,
            key=f"import:{import_id}",
            kind="import_statement",
            payload={"import_id": import_id},
        )
    return dict(row)


def _finish_open_transaction(conn: psycopg.Connection) -> None:
    """A read starts an implicit transaction; make the next `conn.transaction()` a real one."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.commit()


def retry_with_password(conn: psycopg.Connection, *, import_id: str, password: str) -> None:
    PASSWORDS.put(import_id, password)
    conn.execute(
        """
        UPDATE imports SET status = 'parsing', reason = NULL WHERE id = %s
        """,
        (import_id,),
    )
    enqueue_job(
        conn,
        key=f"import:{import_id}:pw:{datetime.now().isoformat(timespec='microseconds')}",
        kind="import_statement",
        payload={"import_id": import_id},
    )
    conn.commit()


def retry_with_account(conn: psycopg.Connection, *, import_id: str, account_id: str) -> None:
    row = conn.execute("SELECT id FROM accounts WHERE id = %s AND active", (account_id,)).fetchone()
    if row is None:
        raise ValueError("No such account")
    conn.execute(
        """
        UPDATE imports SET status = 'parsing', account_id = %s, reason = NULL WHERE id = %s
        """,
        (account_id, import_id),
    )
    enqueue_job(
        conn,
        key=f"import:{import_id}:account:{datetime.now().isoformat(timespec='microseconds')}",
        kind="import_statement",
        payload={"import_id": import_id},
    )
    conn.commit()


def retry_with_opening(conn: psycopg.Connection, *, import_id: str, opening_paise: int) -> None:
    if opening_paise < 0:
        raise ValueError("The opening balance cannot be negative")
    row = conn.execute("SELECT review FROM imports WHERE id = %s", (import_id,)).fetchone()
    if row is None:
        raise ValueError("No such import")
    review = dict(row["review"] or {})
    review["manual_opening_paise"] = int(opening_paise)
    conn.execute(
        """
        UPDATE imports SET status = 'parsing', reason = NULL, review = %s WHERE id = %s
        """,
        (Jsonb(review), import_id),
    )
    enqueue_job(
        conn,
        key=f"import:{import_id}:opening:{datetime.now().isoformat(timespec='microseconds')}",
        kind="import_statement",
        payload={"import_id": import_id},
    )
    conn.commit()


def _set_status(
    conn: psycopg.Connection,
    import_id: str,
    status: str,
    *,
    reason: str | None = None,
    **fields: Any,
) -> None:
    assignments = ["status = %s", "reason = %s"]
    params: list[Any] = [status, reason]
    for key, value in fields.items():
        assignments.append(f"{key} = %s")
        params.append(value)
    params.append(import_id)
    conn.execute(
        f"UPDATE imports SET {', '.join(assignments)} WHERE id = %s",  # noqa: S608 - fixed keys
        params,
    )
    conn.commit()


def _context(
    *,
    account: dict[str, Any] | None,
    period: dict[str, Any] | None,
    opening: dict[str, Any] | None,
    fingerprint: str | None,
    expected_fingerprint: str | None,
) -> ScriptContext:
    return ScriptContext(
        period=(period["start"], period["end"]) if period else None,
        ledger_opening_paise=opening["paise"] if opening else None,
        ledger_opening_as_of=opening["as_of"] if opening else None,
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
    expected_fingerprint: str | None,
) -> dict[str, Any]:
    result = account_check(
        parsed_account,
        period=(period["start"], period["end"]) if period else None,
        ledger_opening=ledger_opening_paise,
        expected_fingerprint=expected_fingerprint,
    )
    opening = parsed_account.get("opening")
    result["opening_paise"] = opening if isinstance(opening, int) else ledger_opening_paise
    closing = parsed_account.get("closing")
    if not isinstance(closing, int) and parsed_account.get("rows"):
        last = parsed_account["rows"][-1].get("balance")
        closing = last if isinstance(last, int) else None
    result["closing_paise"] = closing
    return result


def _finalize(
    conn: psycopg.Connection,
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
    _finish_open_transaction(conn)
    with conn.transaction():
        conn.execute("DELETE FROM statement_lines WHERE import_id = %s", (import_id,))
        conn.execute(
            """
            UPDATE imports
            SET account_id = %s, period_start = %s, period_end = %s,
                opening_balance_paise = %s, closing_balance_paise = %s,
                check_result = %s, parser = %s, helper_version = %s,
                fingerprint = %s, institution = %s, status = 'review', reason = NULL
            WHERE id = %s
            """,
            (
                account["id"],
                date.fromisoformat(period["start"]) if period else None,
                date.fromisoformat(period["end"]) if period else None,
                check.get("opening_paise"),
                check.get("closing_paise"),
                Jsonb(check),
                script,
                helper_version,
                fingerprint,
                parsed_account.get("institution") or account["institution"] or account["name"],
                import_id,
            ),
        )
        insert_lines(
            conn,
            import_id=import_id,
            account=account,
            parsed_account=parsed_account,
        )
        period_dates = None
        if period:
            period_dates = (date.fromisoformat(period["start"]), date.fromisoformat(period["end"]))
        review = reconcile(
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
    with closing(connect()) as conn:
        row = dict(conn.execute("SELECT * FROM imports WHERE id = %s", (import_id,)).fetchone())
        if row["status"] in ("committed", "rejected"):
            return
        attachment = conn.execute(
            "SELECT * FROM attachments WHERE import_id = %s ORDER BY created_at DESC LIMIT 1",
            (import_id,),
        ).fetchone()
        if attachment is None:
            _set_status(conn, import_id, "failed", reason="the attachment is gone")
            return
        if attachment["data"] is None:
            _set_status(conn, import_id, "failed", reason="the file was cleaned up")
            return
        data = bytes(attachment["data"])
        account = _account_for_row(conn, row)
        password = PASSWORDS.pop(import_id)
        conn.execute(
            """
            UPDATE imports
            SET status = 'parsing', reason = NULL, updated_at = now()
            WHERE id = %s
            """,
            (import_id,),
        )
        conn.commit()

    try:
        docs = open_documents(data, password=password)
    except DocError as exc:
        with closing(connect()) as conn:
            if exc.code in ("password_required", "wrong_password"):
                _set_status(
                    conn,
                    import_id,
                    "needs_password",
                    reason="wrong password"
                    if exc.code == "wrong_password"
                    else "password required",
                )
            else:
                _set_status(
                    conn, import_id, "failed", reason=f"could not open the file: {exc.code}"
                )
        return
    except Exception as exc:  # noqa: BLE001
        with closing(connect()) as conn:
            _set_status(
                conn, import_id, "failed", reason=f"could not open the file: {type(exc).__name__}"
            )
        return

    doc = docs[0]
    with closing(connect()) as conn:
        if account is None:
            account = _infer_account(conn, doc)
        ledger_last = ledger_last_date(conn, account["id"]) if account else None
        period = derive_period(doc, ledger_last=ledger_last, today=ledger_today())
        opening = (
            ledger_opening(conn, account["id"], date.fromisoformat(period["start"]))
            if account and period
            else None
        )
        manual_opening = (row.get("review") or {}).get("manual_opening_paise")
        if opening is None and manual_opening is not None:
            opening = {"paise": int(manual_opening), "as_of": None}
        fingerprint = detect_fingerprint(doc) or _account_fingerprint(account)
        layout = layout_for(conn, fingerprint)
        if layout is not None and layout["fingerprint"] != fingerprint:
            layout = None
        if layout is not None and layout["helper_version"] != HELPER_VERSION:
            # The saved script ran with another helper block. Replaying it with this one would
            # silently change its meaning, so the layout counts as new.
            layout = None
        if layout is not None and account is None:
            _set_status(
                conn, import_id, "needs_input", reason="Which account is this statement for?"
            )
            return
        context = _context(
            account=account,
            period=period,
            opening=opening,
            fingerprint=fingerprint,
            expected_fingerprint=layout["fingerprint"] if layout else None,
        )

    if layout is not None:
        replayed = await run_script(doc, layout["parser"], context)
        parsed_accounts, reason = normalize_accounts(replayed.output)
        if replayed.output is not None and not reason and parsed_accounts:
            parsed = parsed_accounts[0]
            resolved = account
            check = _check_account(
                parsed,
                period=period,
                ledger_opening_paise=opening["paise"] if opening else None,
                expected_fingerprint=layout["fingerprint"],
            )
            if check["status"] == "VERIFIED":
                with closing(connect()) as conn:
                    _finalize(
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
                    touch_layout(conn, layout["id"])
                    conn.commit()
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
        with closing(connect()) as conn:
            _set_status(
                conn,
                import_id,
                "not_a_statement",
                reason=f"This isn't a statement: {parse_result.kind or 'unknown document'}",
            )
        return
    if status == "needs_input":
        with closing(connect()) as conn:
            _set_status(
                conn,
                import_id,
                "needs_input",
                reason=parse_result.question or "More information is needed",
            )
        return
    if status != "parser_submitted" or not parse_result.script:
        with closing(connect()) as conn:
            _set_status(
                conn, import_id, "failed", reason=parse_result.reason or "the parse run gave up"
            )
        return

    with closing(connect()) as conn:
        fresh = await run_script(doc, parse_result.script, context)
        parsed_accounts, reason = normalize_accounts(fresh.output)
        if not parsed_accounts:
            _set_status(
                conn, import_id, "failed", reason=reason or "the parser returned no accounts"
            )
            return
        parsed = parsed_accounts[0]
        resolved = _resolve_parsed_account(conn, parsed_account=parsed, current=account, doc=doc)
        if resolved is None:
            _set_status(
                conn, import_id, "needs_input", reason="Which account is this statement for?"
            )
            return
        if not period:
            ledger_last = ledger_last_date(conn, resolved["id"])
            period = derive_period(doc, ledger_last=ledger_last, today=ledger_today())
        if opening is None and period:
            opening = ledger_opening(conn, resolved["id"], date.fromisoformat(period["start"]))
        if opening is None and manual_opening is not None:
            opening = {"paise": int(manual_opening), "as_of": None}
        check = _check_account(
            parsed,
            period=period,
            ledger_opening_paise=opening["paise"] if opening else None,
            expected_fingerprint=fingerprint,
        )
        if check["status"] != "VERIFIED":
            reason = check.get("reason") or "the checks did not pass"
            if reason in (
                "opening balance not found in the file or the ledger",
                "closing balance not found in the file",
            ):
                _set_status(
                    conn,
                    import_id,
                    "needs_input",
                    reason="What was the opening balance?",
                    check_result=Jsonb(check),
                )
                return
            _set_status(
                conn,
                import_id,
                "failed",
                reason=reason,
                check_result=Jsonb(check),
            )
            return
        _finalize(
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
        conn.commit()


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


def import_payload(conn: psycopg.Connection, import_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM imports WHERE id = %s", (import_id,)).fetchone()
    if row is None:
        raise ValueError("No such import")
    account = None
    if row["account_id"]:
        account_row = conn.execute(
            "SELECT id, name, kind, institution, last4 FROM accounts WHERE id = %s",
            (row["account_id"],),
        ).fetchone()
        account = dict(account_row) if account_row else None
    lines = [
        dict(line)
        for line in conn.execute(
            "SELECT * FROM statement_lines WHERE import_id = %s ORDER BY date, id",
            (import_id,),
        ).fetchall()
    ]
    attachment = conn.execute(
        """
        SELECT filename, content_type, size_bytes, sha256, purged_at
        FROM attachments WHERE import_id = %s ORDER BY created_at DESC LIMIT 1
        """,
        (import_id,),
    ).fetchone()
    check = row["check_result"] or {}
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
                "candidates": (line["match_detail"] or {}).get("candidates", [])
                if line["match_state"] == "ambiguous"
                else [],
            }
            for line in lines
        ],
    }


def list_imports(conn: psycopg.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT i.*, a.filename, acc.name AS account_name
        FROM imports i
        LEFT JOIN attachments a ON a.import_id = i.id
        LEFT JOIN accounts acc ON acc.id = i.account_id
        ORDER BY i.created_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
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
