"""The statement period comes from outside the parser.

A day/month swap keeps the sums right, so the dates are checked against a period read from
the header (a date range or a month name) or, failing that, from where the ledger ends.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.importers.layout_detect import line_text, text_lines
from fin_agent.importers.readers import Doc
from fin_agent.ledger.movements import _effectively_reversed_ids

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_DATE = r"(\d{1,2})[/\-. ]([A-Za-z]{3,9}|\d{1,2})[/\-. ](\d{4}|\d{2})"
_ISO_RANGE = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})\b\s*(?:to|through|until|till|[-–—])\s*"
    r"\b(\d{4})-(\d{2})-(\d{2})\b",
    re.IGNORECASE,
)
_RANGE = re.compile(
    rf"{_DATE}\s*(?:to|through|until|till|[-–—])\s*{_DATE}",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\s+(\d{4})\b",
    re.IGNORECASE,
)
_YEARLESS_RANGE = re.compile(
    r"\b(\d{1,2})\s+("
    + "|".join(sorted(_MONTHS, key=len, reverse=True))
    + r")\s*(?:to|through|until|till|[-–—])\s*(\d{1,2})\s+("
    + "|".join(sorted(_MONTHS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)
_SHORT_YEAR = re.compile(r"['’](\d{2})\b")


def _one_date(day: str, month: str, year: str) -> dt.date | None:
    try:
        month_number = int(month) if month.isdigit() else _MONTHS.get(month.lower()[:9])
        if month_number is None:
            return None
        year_number = int(year)
        if year_number < 100:
            year_number += 2000
        return dt.date(year_number, int(month_number), int(day))
    except TypeError, ValueError:
        return None


def _document_text(doc: Doc) -> str:
    chunks: list[str] = []
    try:
        if doc.kind == "pdf":
            for page in range(min(doc.page_count(), 2)):
                for line in text_lines(doc.words(page)):
                    chunks.append(line_text(line))
        elif doc.kind in ("xlsx", "xls", "xlsb", "ods", "docx", "html", "rtf", "text", "ofx"):
            rows = doc.cells(0)
            for row in rows[:40]:
                chunks.append(" ".join(str(cell) for cell in row if cell is not None))
        elif doc.kind in ("json", "camt", "xml", "mt940"):
            rows = doc.cells(0)
            for row in rows[:60]:
                chunks.append(" ".join(str(cell) for cell in row if cell is not None))
    except Exception:  # noqa: BLE001 - a period is best effort; the checks say when it is missing
        pass
    return "\n".join(chunks)


def derive_period(
    doc: Doc,
    *,
    ledger_last: dt.date | None = None,
    today: dt.date | None = None,
) -> dict[str, Any] | None:
    text = _document_text(doc)
    # An ISO range first: the day-first pattern would read "2026-04-01" as a 26 April 2001.
    iso = _ISO_RANGE.search(text)
    if iso:
        try:
            iso_start = dt.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            iso_end = dt.date(int(iso.group(4)), int(iso.group(5)), int(iso.group(6)))
        except ValueError:
            iso_start = iso_end = None
        if iso_start and iso_end:
            if iso_end < iso_start:
                iso_start, iso_end = iso_end, iso_start
            return {
                "start": iso_start.isoformat(),
                "end": iso_end.isoformat(),
                "source": "header",
            }
    match = _RANGE.search(text)
    if match:
        start = _one_date(*match.group(1, 2, 3))
        end = _one_date(*match.group(4, 5, 6))
        if start and end:
            if end < start:
                start, end = end, start
            return {"start": start.isoformat(), "end": end.isoformat(), "source": "header"}
    yearless = _YEARLESS_RANGE.search(text)
    years = [2000 + int(year) for year in _SHORT_YEAR.findall(text)]
    if yearless and years:
        end_year = max(years)
        start_month = _MONTHS[yearless.group(2).lower()[:9]]
        end_month = _MONTHS[yearless.group(4).lower()[:9]]
        start_year = end_year - 1 if end_month < start_month else end_year
        start = _one_date(yearless.group(1), yearless.group(2), str(start_year))
        end = _one_date(yearless.group(3), yearless.group(4), str(end_year))
        if start and end:
            return {"start": start.isoformat(), "end": end.isoformat(), "source": "header"}
    month_year = _MONTH_YEAR.search(text)
    if month_year:
        month_number = _MONTHS[month_year.group(1).lower()[:9]]
        year_number = int(month_year.group(2))
        start = dt.date(year_number, month_number, 1)
        if month_number == 12:
            end = dt.date(year_number, 12, 31)
        else:
            end = dt.date(year_number, month_number + 1, 1) - dt.timedelta(days=1)
        return {"start": start.isoformat(), "end": end.isoformat(), "source": "month-name"}
    if ledger_last is not None:
        day = today or dt.date.today()
        start = ledger_last + dt.timedelta(days=1)
        if start <= day:
            return {
                "start": start.isoformat(),
                "end": day.isoformat(),
                "source": "ledger-continuity",
            }
    return None


async def ledger_last_date(conn: Connection, account_id: str) -> dt.date | None:
    row = await conn.fetchrow(
        """
        SELECT max(m.date) AS last
        FROM accounts a
        JOIN pgledger_transfers t
          ON t.from_account_id = a.ledger_account_id OR t.to_account_id = a.ledger_account_id
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE a.id = $1
        """,
        account_id,
    )
    return row["last"] if row else None


async def ledger_opening(
    conn: Connection, account_id: str, period_start: dt.date | None
) -> dict[str, Any] | None:
    """The account's ledger balance before the statement, or None on a first import.

    A movement a reversal still cancels is not evidence of a balance: after an undone import, its
    rows and their reversals are both in the ledger. Counting them as an opening made every later
    statement fail its printed-opening check, because the net of a cancelled pair is meaningless.
    So the balance is the ledger's own net (the pairs cancel), and the evidence test counts only
    movements that stand.
    """
    account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
    if account is None:
        return None
    cancelled = await _effectively_reversed_ids(conn)
    params: list[Any] = [account["ledger_account_id"]]
    cutoff = ""
    if period_start is not None:
        cutoff = """
            AND coalesce(m.date, (t.event_at AT TIME ZONE 'Asia/Kolkata')::date) < $2
        """
        params.append(period_start)
    standing = (
        f"m.id IS NULL OR (m.reverses_id IS NULL AND NOT (m.id = ANY(${len(params) + 1}::text[])))"
    )
    params.append(cancelled)
    row = await conn.fetchrow(
        f"""
        SELECT coalesce(sum(e.amount), 0)::bigint AS balance,
               count(*) FILTER (WHERE {standing})::bigint AS entries,
               max(coalesce(m.date, (t.event_at AT TIME ZONE 'Asia/Kolkata')::date))
                   FILTER (WHERE {standing}) AS as_of
        FROM pgledger_entries e
        JOIN pgledger_transfers t ON t.id = e.transfer_id
        LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE e.account_id = $1
        {cutoff}
        """,  # noqa: S608 - cutoff and standing are constants of this module
        *params,
    )
    if row is None or int(row["entries"]) == 0:
        return None
    return {
        "paise": int(row["balance"]),
        "as_of": row["as_of"].isoformat() if row["as_of"] else None,
    }


__all__ = ["derive_period", "ledger_last_date", "ledger_opening"]
