"""The non-negotiable checks, run before a single row reaches the ledger.

The balance identity alone is not enough: a day/month swap and an inserted column both
balance. So the period comes from outside the parser, the header fingerprint must match a
saved layout, an empty result is refused unless the statement says it was empty, and the
printed opening is compared with the ledger's.
"""

from __future__ import annotations

import re
from typing import Any

from fin_agent.importers.layout import normalize_headers

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_statement(
    rows: Any,
    opening: Any = None,
    closing: Any = None,
    *,
    period: tuple[str, str] | list[str] | None = None,
    ledger_opening: int | None = None,
    headers: Any = None,
    expected_fingerprint: str | None = None,
    empty_statement: bool = False,
) -> dict[str, Any]:
    """Integer paise. A row has date, debit, credit, and optionally balance."""
    result: dict[str, Any] = {
        "status": "FAILED",
        "rows": 0,
        "reason": None,
        "chain_breaks": [],
        "computed_closing": None,
    }
    try:
        if not isinstance(rows, list):
            result["reason"] = "rows is not a list"
            return result
        count = len(rows)
        result["rows"] = count
        if count == 0:
            effective_opening = opening if _is_int(opening) else ledger_opening
            if empty_statement and _is_int(effective_opening) and effective_opening == closing:
                result.update(
                    status="VERIFIED",
                    computed_closing=closing,
                    opening_source="printed" if _is_int(opening) else "ledger",
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
        effective_opening = opening if _is_int(opening) else ledger_opening
        if not _is_int(effective_opening):
            result["reason"] = "opening balance not found in the file or the ledger"
            return result
        effective_closing = closing
        if not _is_int(effective_closing):
            last = rows[-1].get("balance") if rows else None
            if _is_int(last):
                effective_closing = last
                result["closing_source"] = "last running balance"
            else:
                result["reason"] = "closing balance not found in the file"
                return result
        result["opening_source"] = "printed" if _is_int(opening) else "ledger"
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

        opening_disagrees = (
            _is_int(opening) and ledger_opening is not None and opening != ledger_opening
        )
        result["opening_matches_ledger"] = not opening_disagrees
        if opening_disagrees:
            result["reason"] = "printed opening differs from the ledger"

        ok = bool(result["total_ok"]) and result.get("chain_ok") is not False
        if period and not result.get("dates_in_period"):
            ok = False
            result["reason"] = "dates outside the statement period"
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


__all__ = ["account_check", "check_statement", "worst_status"]
