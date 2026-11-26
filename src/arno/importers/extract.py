"""What extraction returns, and the host step that checks it and moves the import to review.

Both extraction paths (`agent/extractor.py`) end in an `ExtractedStatement`. The host does the
rest here, in plain code: scrub the account number, resolve the account, derive the period, run
the checks, and write review. Nothing a model says is trusted past `checks.account_check`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from arno.db.conn import Connection
from arno.importers.checks import OPENING_MISSING, account_check, failure_reason
from arno.importers.identifiers import scrub_account_number
from arno.importers.periods import derive_period, ledger_last_date
from arno.importers.periods import ledger_opening as opening_before
from arno.importers.pipeline import _finalize, _resolve_parsed_account, _set_status
from arno.ledger.common import today as ledger_today
from arno.ledger.payees import is_placeholder_name


class ExtractedRow(BaseModel):
    date: str = Field(description="ISO date, YYYY-MM-DD")
    description: str = Field(description="The narration, copied exactly")
    debit: int = Field(default=0, ge=0, description="Paise leaving the account")
    credit: int = Field(default=0, ge=0, description="Paise arriving in the account")
    balance: int | None = Field(default=None, description="Printed running balance, ledger sign")
    upi_ref: str | None = None
    counterparty: str | None = Field(default=None, description="Who the narration names, if clear")


class ExtractedAccount(BaseModel):
    name: str | None = None
    institution: str | None = None
    account_number: str | None = Field(default=None, description="As printed; the host keeps 4")


class ExtractedStatement(BaseModel):
    is_statement: bool = Field(
        default=True, description="False for a payslip, letter, or anything but a statement"
    )
    account: ExtractedAccount = Field(default_factory=ExtractedAccount)
    opening: int | None = Field(default=None, description="Printed opening, ledger sign")
    closing: int | None = Field(default=None, description="Printed closing, ledger sign")
    period_start: str | None = Field(default=None, description="Printed period start, ISO")
    period_end: str | None = Field(default=None, description="Printed period end, ISO")
    rows: list[ExtractedRow] = Field(default_factory=list)
    empty_statement: bool = Field(
        default=False, description="True when the statement says there were no transactions"
    )


_GENERIC_NAME_WORDS = frozenset(
    {
        "bank",
        "card",
        "credit",
        "debit",
        "account",
        "main",
        "savings",
        "saving",
        "current",
        "the",
        "wallet",
        "cash",
        "salary",
        "and",
    }
)
_CARD_PAYMENT = re.compile(
    r"cc\s*(re)?pay|credit\s*card|card\s*(bill|pay|repay|due)|ccpay", re.IGNORECASE
)


def names_account(text: str, account: dict[str, Any], *, only_card: bool) -> bool:
    """Whether a statement line's own text names this account as its other side.

    A name word or the last four digits name it. The institution alone does not: a UPI narration
    prints the payee's bank code (`/HDFC/`), so it names a card only beside a card-payment phrase.
    A card-payment phrase alone names a card only when the user has one card. The user's own
    name names no account.
    """
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    institution = set(re.findall(r"[a-z0-9]+", str(account.get("institution") or "").lower()))
    for word in re.findall(r"[a-z0-9]+", str(account.get("name") or "").lower()):
        if (
            len(word) >= 3
            and word not in _GENERIC_NAME_WORDS
            and word not in institution
            and word in words
        ):
            return True
    last4 = str(account.get("last4") or "")
    if len(last4) == 4 and last4 in lowered:
        return True
    if account.get("kind") != "card" or not _CARD_PAYMENT.search(text):
        return False
    return only_card or bool(institution & words)


async def suggest_transfers(conn: Connection, import_id: str, account_id: str) -> int:
    """Mark statement lines whose own text names exactly one other account of the user's.

    The review screen asks the user to confirm each one; a line never becomes a transfer
    on this evidence alone.
    """
    others = await conn.fetch(
        """
        SELECT id, name, kind, institution, last4 FROM v_accounts
        WHERE active AND kind IN ('bank', 'card', 'wallet', 'cash') AND id <> $1
        """,
        account_id,
    )
    cards = sum(1 for other in others if other["kind"] == "card")
    marked = 0
    for line in await conn.fetch(
        "SELECT id, description_raw FROM statement_lines WHERE import_id = $1", import_id
    ):
        named = [
            other
            for other in others
            if names_account(str(line["description_raw"] or ""), other, only_card=cards == 1)
        ]
        if len(named) != 1:
            continue
        await conn.execute(
            """
            UPDATE statement_lines
            SET match_detail = coalesce(match_detail, '{}'::jsonb) || $1::jsonb, updated_at = now()
            WHERE id = $2
            """,
            {"suggested_transfer_account_id": named[0]["id"]},
            line["id"],
        )
        marked += 1
    return marked


@dataclass
class Assessment:
    """An extraction against the ledger: who it belongs to, its period, and the checks' verdict."""

    account: dict[str, Any] | None
    period: dict[str, Any] | None
    check: dict[str, Any]
    parsed: dict[str, Any]


def _parsed(extracted: ExtractedStatement) -> dict[str, Any]:
    """The flat account dict `account_check`, `insert_lines`, and account resolution consume."""
    rows = []
    for row in extracted.rows:
        counterparty = (row.counterparty or "").strip()[:120]
        rows.append(
            {
                **row.model_dump(),
                "upi_ref": (row.upi_ref or "").strip()[:40] or None,
                "counterparty": None if is_placeholder_name(counterparty) else counterparty,
            }
        )
    account = extracted.account
    return {
        "name": account.name or "statement",
        "institution": account.institution or None,
        # Only the last four digits ever leave this function.
        "account_number": scrub_account_number(account.account_number),
        "opening": extracted.opening,
        "closing": extracted.closing,
        "empty_statement": extracted.empty_statement,
        "rows": rows,
    }


def _printed_period(extracted: ExtractedStatement) -> dict[str, Any] | None:
    try:
        start = date.fromisoformat(extracted.period_start or "")
        end = date.fromisoformat(extracted.period_end or "")
    except ValueError:
        return None
    if end < start:
        start, end = end, start
    return {"start": start.isoformat(), "end": end.isoformat()}


async def assess(
    conn: Connection, import_id: str, extracted: ExtractedStatement, *, text: str = ""
) -> Assessment:
    """Resolve the account and period for an extraction and run the checks on it.

    `text` is the file's header preview; it finds the period before the model's printed one does.
    """
    row = await conn.fetchrow_required("SELECT * FROM imports WHERE id = $1", import_id)
    parsed = _parsed(extracted)
    current = None
    if row["account_id"]:
        current = await conn.fetchrow("SELECT * FROM v_accounts WHERE id = $1", row["account_id"])
    account = await _resolve_parsed_account(conn, parsed_account=parsed, current=current, text=text)
    if account is None:
        return Assessment(None, None, {"status": "FAILED", "reason": None}, parsed)
    ledger_last = await ledger_last_date(conn, account["id"])
    period = derive_period(
        text, printed=_printed_period(extracted), ledger_last=ledger_last, today=ledger_today()
    )
    opening = (
        await opening_before(conn, account["id"], date.fromisoformat(period["start"]))
        if period
        else None
    )
    manual = (row["review"] or {}).get("manual_opening_paise")
    check = account_check(
        parsed,
        period=(period["start"], period["end"]) if period else None,
        ledger_opening=opening["paise"] if opening else None,
        manual_opening=int(manual) if manual is not None else None,
    )
    return Assessment(account, period, check, parsed)


async def retryable_reason(
    conn: Connection, import_id: str, extracted: ExtractedStatement, *, text: str = ""
) -> str | None:
    """Why extraction should be tried again, or None when the result stands as it is.

    A missing opening is not a reason: the rows are sound and the user answers it on the card.
    A statement with no account to file against is the user's to answer too.
    """
    if not extracted.is_statement:
        return None
    found = await assess(conn, import_id, extracted, text=text)
    if found.account is None or found.check["status"] == "VERIFIED":
        return None
    if found.check.get("reason") == OPENING_MISSING:
        return None
    return failure_reason(found.check)


async def finalize_extracted(
    conn: Connection, import_id: str, extracted: ExtractedStatement, *, text: str = ""
) -> str:
    """Move the import to review, needs_input, failed, or not_a_statement. Returns the status."""
    if not extracted.is_statement:
        status, reason = "not_a_statement", "This isn't a statement: no bank, card, or wallet rows."
        await _set_status(conn, import_id, status, reason=reason)
        return status
    found = await assess(conn, import_id, extracted, text=text)
    if found.account is None:
        await _set_status(
            conn, import_id, "needs_input", reason="Which account is this statement for?"
        )
        return "needs_input"
    check = found.check
    status, reason = "review", None
    if check["status"] != "VERIFIED":
        reason = failure_reason(check)
        if check.get("reason") == OPENING_MISSING:
            # The rows are sound; only the opening is unknown. Keep them, so the review card's
            # opening answer completes the check.
            status = "needs_input"
        else:
            await _set_status(conn, import_id, "failed", reason=reason, check=check)
            return "failed"
    written = await _finalize(
        conn,
        import_id=import_id,
        account=found.account,
        parsed_account=found.parsed,
        check=check,
        period=found.period,
        status=status,
        reason=reason,
    )
    if not written:
        return "rejected"
    await suggest_transfers(conn, import_id, found.account["id"])
    return status


__all__ = [
    "Assessment",
    "ExtractedAccount",
    "ExtractedRow",
    "ExtractedStatement",
    "assess",
    "finalize_extracted",
    "names_account",
    "retryable_reason",
    "suggest_transfers",
]
