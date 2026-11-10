"""The non-negotiable checks, run before a single row reaches the ledger.

The balance identity alone is not enough: a day/month swap and an inserted column both
balance. So the period comes from outside the parser, the header fingerprint must match a
saved layout, an empty result is refused unless the statement says it was empty, and the
printed opening is compared with the ledger's.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, TypeGuard

from fin_agent.importers.layout import normalize_headers
from fin_agent.ledger.common import today

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

OPENING_MISSING = "opening balance not found in the file or the ledger"
CLOSING_MISSING = "closing balance not found in the file"
INPUT_REASONS = (OPENING_MISSING, CLOSING_MISSING)


def _period_end(period: Any) -> date | None:
    try:
        return date.fromisoformat(str(period[1]))
    except IndexError, TypeError, ValueError:
        return None


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def stated_opening_to_ledger(paise: int, account_kind: str | None) -> int:
    """The ledger value of an opening the user states as an amount owed.

    Balances are ledger-signed everywhere in the pipeline: a card that owes money holds a
    negative balance, and a statement's printed dues are negated by the parser. So the one
    place a printed figure arrives from the user (the review screen's opening answer) is
    converted here, by account kind.
    """
    return -paise if account_kind == "card" else paise


def _opening_value(
    opening: Any,
    ledger_opening: int | None,
    manual_opening: int | None,
) -> tuple[int | None, str | None]:
    """Choose the opening and its provenance.

    A user-entered value is trusted. An anchored ledger value is trusted. A parser value is a
    claim: a zero is exactly the known fallback shape, so it is never labeled as printed.
    """
    if _is_int(manual_opening):
        return manual_opening, "manual"
    if _is_int(ledger_opening):
        return ledger_opening, "ledger"
    if _is_int(opening):
        return opening, "printed" if opening else "parser-zero"
    return None, None


def check_statement(
    rows: Any,
    opening: Any = None,
    closing: Any = None,
    *,
    period: tuple[str, str] | list[str] | None = None,
    ledger_opening: int | None = None,
    manual_opening: int | None = None,
    headers: Any = None,
    expected_fingerprint: str | None = None,
    empty_statement: bool = False,
) -> dict[str, Any]:
    """Integer paise. A row has date, debit, credit, and optionally balance.

    Everything here is ledger-signed: `opening`, `closing`, the rows' running `balance`, and
    both opening sources. A card that owes money is negative, so a parser returns a printed
    amount owed negated (`PARSE_INSTRUCTIONS` in `agent/parser.py` and `import.md` §Signs say
    so).
    """
    result: dict[str, Any] = {
        "status": "FAILED",
        "rows": 0,
        "reason": None,
        "chain_breaks": [],
        "computed_closing": None,
        "opening_source": None,
        "opening_paise": None,
        "closing_paise": None,
    }
    try:
        if not isinstance(rows, list):
            result["reason"] = "rows is not a list"
            return result
        count = len(rows)
        result["rows"] = count
        if count == 0:
            effective_opening, opening_source = _opening_value(
                opening, ledger_opening, manual_opening
            )
            if empty_statement and effective_opening is not None and effective_opening == closing:
                result.update(
                    status="VERIFIED",
                    computed_closing=closing,
                    opening_source=opening_source,
                    opening_paise=effective_opening,
                    closing_paise=effective_opening,
                    reason="empty statement, balance unchanged",
                )
                return result
            result["reason"] = "no rows"
            return result
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                result["reason"] = f"row {index}: not a dict"
                return result
            for key in ("debit", "credit"):
                value = row.get(key)
                if not _is_int(value) or value < 0:
                    result["reason"] = f"row {index}: {key} not a non-negative int"
                    return result
            if row["debit"] and row["credit"]:
                result["reason"] = f"row {index}: both debit and credit"
                return result
            if row.get("balance") is not None and not _is_int(row["balance"]):
                result["reason"] = f"row {index}: balance not int"
                return result
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("date") or "")):
                result["reason"] = f"row {index}: bad date {row.get('date')!r}"
                return result
        effective_opening, opening_source = _opening_value(opening, ledger_opening, manual_opening)
        # The closing is known from the printed figure or the last running balance even when the
        # opening is not, so a statement parked for its opening still carries what to check.
        effective_closing = closing
        if not _is_int(effective_closing):
            last = rows[-1].get("balance") if rows else None
            if _is_int(last):
                effective_closing = last
                result["closing_source"] = "last running balance"
            else:
                result["reason"] = CLOSING_MISSING
                return result
        result["closing_paise"] = effective_closing
        if effective_opening is None:
            result["reason"] = OPENING_MISSING
            return result
        result["opening_source"] = opening_source
        result["opening_paise"] = effective_opening
        credits = sum(row["credit"] for row in rows)
        debits = sum(row["debit"] for row in rows)
        computed = int(effective_opening) + credits - debits
        result["computed_closing"] = computed
        result["total_ok"] = computed == effective_closing

        running = int(effective_opening)
        breaks: list[int] = []
        balance_rows = 0
        for index, row in enumerate(rows):
            running += row["credit"] - row["debit"]
            if row.get("balance") is not None:
                balance_rows += 1
                if row["balance"] != running:
                    breaks.append(index)
                    running = row["balance"]
        result["balance_rows"] = balance_rows
        result["chain_ok"] = (not breaks) if balance_rows else None
        result["chain_breaks"] = breaks[:5]

        dates = [row["date"] for row in rows]
        result["dates_monotonic"] = dates == sorted(dates) or dates == sorted(dates, reverse=True)
        if period:
            start, end = str(period[0]), str(period[1])
            result["period"] = [start, end]
            result["dates_in_period"] = all(start <= day <= end for day in dates)
        else:
            result["dates_in_period"] = None

        if headers is not None and expected_fingerprint:
            actual = normalize_headers(headers)
            result["fingerprint"] = actual
            result["header_match"] = actual == expected_fingerprint

        # A manual value is the user's evidence; a parser claim never overrides it.
        opening_disagrees = (
            opening_source == "ledger"
            and _is_int(opening)
            and ledger_opening is not None
            and opening != ledger_opening
        )
        result["opening_matches_ledger"] = not opening_disagrees
        if opening_disagrees:
            result["reason"] = "printed opening differs from the ledger"

        ok = bool(result["total_ok"]) and result.get("chain_ok") is not False
        if period and not result.get("dates_in_period"):
            ok = False
            result["reason"] = "dates outside the statement period"
        # A period that has not ended yet cannot be checked against a ledger that is still
        # moving, and committing it would pin a balance for days that have not happened. A
        # corrupt export (or a mistyped year) is the usual cause.
        end = _period_end(period) if period else None
        if end is not None and end > today():
            ok = False
            result["reason"] = (
                f"This statement's period ends on {end.isoformat()}, after today "
                f"({today().isoformat()}). A statement that has not ended yet is not imported."
            )
        if expected_fingerprint and result.get("header_match") is False:
            ok = False
            result["reason"] = "column headers do not match the saved layout"
        if opening_disagrees:
            ok = False
        result["status"] = "VERIFIED" if ok else "DISCREPANCY"
        return result
    except Exception as exc:  # noqa: BLE001 - a malformed script result must never crash the host
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result


def opening_is_evidenced(
    *,
    check: dict[str, Any],
    review: dict[str, Any],
    account: dict[str, Any] | None,
    period_start: Any = None,
) -> bool:
    """Whether a saved opening is backed by trusted evidence.

    The narrower rule is local to a first card import: a parser label alone (including the old
    `opening_source=printed` on a fallback zero) cannot commit without a user manual entry or a
    prior anchored ledger balance. Bank formats, including printed openings with no running
    balances, keep their existing contract.
    """
    if check.get("status") != "VERIFIED":
        return False
    if check.get("reason") == "empty statement, balance unchanged":
        return True
    if account is None or account.get("kind") != "card":
        return True
    manual = review.get("manual_opening_paise")
    if (
        _is_int(manual)
        and check.get("opening_source") == "manual"
        and check.get("opening_paise") == manual
    ):
        return True
    if check.get("opening_source") == "ledger":
        anchor = account.get("statement_balance_paise")
        as_of = account.get("statement_balance_as_of")
        if isinstance(as_of, str):
            as_of = date.fromisoformat(as_of)
        if isinstance(period_start, str):
            period_start = date.fromisoformat(period_start)
        if _is_int(anchor) and as_of is not None:
            return period_start is None or as_of < period_start
    return False


def account_check(account: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return check_statement(
        account.get("rows"),
        account.get("opening"),
        account.get("closing"),
        headers=account.get("headers"),
        empty_statement=bool(account.get("empty_statement")),
        **kwargs,
    )


def worst_status(statuses: list[str]) -> str:
    rank = {"VERIFIED": 3, "DISCREPANCY": 2, "FAILED": 1}
    if not statuses:
        return "FAILED"
    return min(statuses, key=lambda status: rank.get(status, 0))


__all__ = [
    "CLOSING_MISSING",
    "INPUT_REASONS",
    "OPENING_MISSING",
    "account_check",
    "check_statement",
    "opening_is_evidenced",
    "stated_opening_to_ledger",
    "worst_status",
]
