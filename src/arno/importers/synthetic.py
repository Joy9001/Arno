"""Synthetic statements that go through the real L3 import path.

These are fixtures for tests and eval trials: no real statement is read, and no row is inserted
into `imports` or `statement_lines` by hand. The helper writes a CSV, creates an attachment,
runs the parser/review pipeline, and commits the result, so `outside_text`, captures,
reconciliation, coverage, and the saved layout all behave as they do for a user import.
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, timedelta
from typing import Any

from arno.agent.parser import submit_parser_code
from arno.db.conn import Connection
from arno.importers.commit import commit_import
from arno.importers.periods import ledger_opening
from arno.importers.pipeline import create_import
from arno.ledger.audit import new_id
from arno.ledger.common import today as ledger_today

SYNTHETIC_INSTITUTION = "Synthetic Bank"

SYNTHETIC_PARSER = r"""
rows = await doc_cells(0)
header = None
header_index = -1
for i, row in enumerate(rows):
    joined = ' '.join(str(c) for c in row).lower()
    if 'narration' in joined and 'debit' in joined and 'credit' in joined and 'date' in joined:
        header = row
        header_index = i
        break
if header is None:
    result = {'not_a_statement': 'unknown'}
else:
    opening = None
    closing = None
    for row in rows:
        text = ' '.join(str(c) for c in row)
        low = text.lower()
        if 'opening balance' in low:
            opening = money(text.split(':')[-1])
        if 'closing balance' in low:
            closing = money(text.split(':')[-1])
    out = []
    for row in rows[header_index + 1:]:
        if not row or not to_iso(row[0]):
            continue
        out.append({
            'date': to_iso(row[0]),
            'description': str(row[1]),
            'debit': money(row[2]) or 0,
            'credit': money(row[3]) or 0,
            'balance': money(row[4]),
            'counterparty': str(row[1])[:120],
        })
    result = {'accounts': [{
        'name': 'Synthetic statement',
        'institution': 'Synthetic Bank',
        'opening': opening,
        'closing': closing,
        'headers': header,
        'rows': out,
    }]}
result
"""


def _money(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    value = abs(int(paise))
    return f"{sign}{value // 100}.{value % 100:02d}"


def _month_end(day: date) -> date:
    import calendar

    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def synthetic_statement_csv(
    *,
    narration: str,
    amount_paise: int = 100,
    day: date,
    period_start: date,
    period_end: date,
    opening_paise: int,
    debit: bool = True,
) -> bytes:
    """A one-line CSV statement with a printed opening and closing balance."""
    closing = opening_paise - amount_paise if debit else opening_paise + amount_paise
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([SYNTHETIC_INSTITUTION])
    writer.writerow(["Savings Account Statement"])
    writer.writerow(
        [
            "Statement Period: "
            f"{period_start.strftime('%d/%m/%Y')} to {period_end.strftime('%d/%m/%Y')}"
        ]
    )
    writer.writerow([f"Opening Balance: {_money(opening_paise)}"])
    writer.writerow(["Date", "Narration", "Debit", "Credit", "Balance"])
    writer.writerow(
        [
            day.strftime("%d/%m/%Y"),
            narration,
            _money(amount_paise) if debit else "",
            "" if debit else _money(amount_paise),
            _money(closing),
        ]
    )
    writer.writerow([f"Closing Balance: {_money(closing)}"])
    return buffer.getvalue().encode("utf-8")


async def seed_committed_statement(
    conn: Connection,
    *,
    account_id: str,
    narration: str,
    amount_paise: int = 100,
    day: date | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    debit: bool = True,
) -> dict[str, Any]:
    """Commit one synthetic statement line through the parser gate and `commit_import`.

    The account's ledger opening is used for the printed opening, so the balance check passes
    without special-casing the fixture. Returns the import, line, movement, and payee IDs.
    """
    account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1 AND active", account_id)
    if account is None:
        raise ValueError(f"No active account {account_id}")

    day = day or ledger_today() - timedelta(days=1)
    period_start = period_start or day.replace(day=1)
    # A synthetic statement never covers a day that has not happened: the checks refuse a
    # period that has not ended, and a fixture is not an excuse to claim one.
    period_end = min(period_end or _month_end(day), ledger_today())
    if not period_start <= day <= period_end:
        raise ValueError("The statement day must fall inside its period")

    opening = await ledger_opening(conn, account_id, period_start)
    opening_paise = int(opening["paise"]) if opening else 0
    payload = synthetic_statement_csv(
        narration=narration,
        amount_paise=amount_paise,
        day=day,
        period_start=period_start,
        period_end=period_end,
        opening_paise=opening_paise,
        debit=debit,
    )

    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', 'synthetic-statement.csv', 'text/csv', $2, $3, $4)
        """,
        attachment_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    import_row = await create_import(conn, attachment_id=attachment_id, account_id=account_id)

    submitted = await submit_parser_code(conn, import_ids=[import_row["id"]], code=SYNTHETIC_PARSER)
    if submitted["status"] != "parser_submitted":
        raise RuntimeError(f"Synthetic import did not pass the gate: {submitted.get('reason')}")

    current = await conn.fetchrow("SELECT * FROM imports WHERE id = $1", import_row["id"])
    if current is None or current["status"] != "review":
        reason = current["reason"] if current is not None else "the import disappeared"
        raise RuntimeError(f"Synthetic import did not reach review: {reason}")

    committed = await commit_import(conn, import_row["id"])
    line = await conn.fetchrow(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY id LIMIT 1",
        import_row["id"],
    )
    if line is None:
        raise RuntimeError("The synthetic import has no statement line")
    payee = await conn.fetchrow(
        """
        SELECT p.*
        FROM transfer_labels tl
        JOIN payees p ON p.id = tl.payee_id
        WHERE tl.movement_id = $1
        LIMIT 1
        """,
        line["movement_id"],
    )
    return {
        "import_id": str(import_row["id"]),
        "line": dict(line),
        "movement_id": line["movement_id"],
        "payee": dict(payee) if payee else None,
        "commit": committed,
    }


async def _first_ledger_day(conn: Connection, account_id: str) -> date | None:
    """The first day the account's ledger holds an entry on, or None for an untouched account."""
    row = await conn.fetchrow(
        """
        SELECT min(coalesce(m.date, (t.event_at AT TIME ZONE 'Asia/Kolkata')::date)) AS day
        FROM pgledger_entries e
        JOIN pgledger_transfers t ON t.id = e.transfer_id
        LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        JOIN accounts a ON a.ledger_account_id = e.account_id
        WHERE a.id = $1
        """,
        account_id,
    )
    return row["day"] if row is not None else None


async def _quiet_month(conn: Connection, account_id: str) -> tuple[date, date, date]:
    """A statement window whose only ledger row will be the statement's own line.

    A one-line statement cannot explain a month the ledger already fills: its printed closing
    would disagree with the ledger's balance at the period end, and the commit would rightly stop
    for a balance adjustment. So the fixture's statement is the last month before the account's
    first ledger entry, where its printed opening (nothing) and closing (the one line) are the
    ledger's own figures.
    """
    first = await _first_ledger_day(conn, account_id)
    if first is None:
        day = ledger_today() - timedelta(days=1)
        return day, day.replace(day=1), min(_month_end(day), ledger_today())
    end = first.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    return start.replace(day=15), start, end


async def seed_injection_statement(
    conn: Connection,
    *,
    account_id: str,
    narration: str,
    day: date | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict[str, Any]:
    """The evals' statement-note fixture, through the L3 import path.

    With no dates, it takes the account's last quiet month so the printed balances agree with
    the ledger; a caller that names a window owns its consistency.
    """
    if day is None and period_start is None and period_end is None:
        day, period_start, period_end = await _quiet_month(conn, account_id)
    return await seed_committed_statement(
        conn,
        account_id=account_id,
        narration=narration,
        amount_paise=100,
        day=day,
        period_start=period_start,
        period_end=period_end,
    )


__all__ = [
    "SYNTHETIC_INSTITUTION",
    "SYNTHETIC_PARSER",
    "seed_committed_statement",
    "seed_injection_statement",
    "synthetic_statement_csv",
]
