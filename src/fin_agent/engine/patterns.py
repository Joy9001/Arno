"""Rule-based recurring-charge and subscription detection.

Weekly, monthly, and yearly charges are recognised from at least three occurrences whose gaps are
within three days of the interval and whose amount stays within ten percent of the median. A
recurring charge with no matching schedule becomes a proposal; an expected schedule occurrence
three or more days late with no matching payment becomes a missed-charge proposal. Nothing here
writes a schedule: proposals go to the inbox and the agent only writes when the user accepts one.

A merchant copied from a statement keeps its `outside_text` flag through the proposal, so opening
the proposal in chat still asks for a confirming tap before a lasting write.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.ledger import schedules as schedules_mod
from fin_agent.ledger.common import today as ledger_today

INTERVALS = (7, 30, 365)
INTERVAL_TOLERANCE_DAYS = 3
AMOUNT_TOLERANCE = 0.10
MIN_OCCURRENCES = 3
MISSED_AFTER_DAYS = 3
HISTORY_DAYS = 800
_GENERIC = {
    "ach",
    "autopay",
    "india",
    "limited",
    "ltd",
    "mandate",
    "nach",
    "neft",
    "payment",
    "pvt",
    "rtgs",
    "upi",
}


def normalize_merchant(name: str | None) -> str:
    """A stable merchant key: case, punctuation, and bank prefixes removed."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", name).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token not in _GENERIC]
    return " ".join(tokens)


@dataclass(frozen=True, slots=True)
class Charge:
    movement_id: str
    merchant: str
    normalized: str
    payee_id: str | None
    outside_text: bool
    date: date
    amount_paise: int


@dataclass(frozen=True, slots=True)
class KnownSchedule:
    id: str
    name: str
    normalized: str
    amount_paise: int | None


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _interval(dates: list[date]) -> int | None:
    if len(dates) < MIN_OCCURRENCES:
        return None
    gaps = [(right - left).days for left, right in zip(dates, dates[1:], strict=False)]
    for interval in INTERVALS:
        if all(abs(gap - interval) <= INTERVAL_TOLERANCE_DAYS for gap in gaps):
            return interval
    return None


def _same_merchant(schedule: KnownSchedule, normalized: str) -> bool:
    if not schedule.normalized or not normalized:
        return False
    if schedule.normalized == normalized:
        return True
    left = set(schedule.normalized.split())
    right = set(normalized.split())
    return bool(left & right)


def detect_recurring(charges: list[Charge], schedules: list[KnownSchedule]) -> list[dict[str, Any]]:
    """Return new-recurring-charge proposals for merchants with no matching schedule."""
    grouped: dict[str, list[Charge]] = {}
    for charge in charges:
        if not charge.normalized:
            continue
        grouped.setdefault(charge.normalized, []).append(charge)

    proposals: list[dict[str, Any]] = []
    for normalized, items in sorted(grouped.items()):
        if len(items) < MIN_OCCURRENCES:
            continue
        items = sorted(items, key=lambda item: (item.date, item.movement_id))
        dates = [item.date for item in items]
        interval = _interval(dates)
        if interval is None:
            continue
        amounts = [item.amount_paise for item in items]
        median = _median(amounts)
        if median <= 0 or any(
            abs(amount - median) > median * AMOUNT_TOLERANCE for amount in amounts
        ):
            continue
        if any(_same_merchant(schedule, normalized) for schedule in schedules):
            continue
        outside = any(item.outside_text for item in items)
        proposals.append(
            {
                "kind": "new_subscription",
                "merchant": items[-1].merchant,
                "normalized": normalized,
                "payee_id": next(
                    (item.payee_id for item in reversed(items) if item.payee_id), None
                ),
                "outside_text": outside,
                "amount_paise": median,
                "interval_days": interval,
                "occurrences": len(items),
                "first_date": dates[0].isoformat(),
                "last_date": dates[-1].isoformat(),
            }
        )
    return proposals


async def _load_charges(conn: Connection, as_of: date) -> list[Charge]:
    from fin_agent.tools.read import _reversed_ids  # one definition of a corrected-away row

    start = as_of - timedelta(days=HISTORY_DAYS)
    rows = await conn.fetch(
        """
        SELECT v.movement_id, v.date, v.payee_id, p.display_name, p.outside_text,
               sum(v.amount_paise)::bigint AS amount_paise
        FROM v_spending v
        JOIN payees p ON p.id = v.payee_id
        JOIN movements m ON m.id = v.movement_id AND m.reverses_id IS NULL
        WHERE v.amount_paise > 0
          AND v.date BETWEEN $1 AND $2
          AND p.active
          AND NOT (v.movement_id = ANY($3))
        GROUP BY v.movement_id, v.date, v.payee_id, p.display_name, p.outside_text
        """,
        start,
        as_of,
        await _reversed_ids(conn),
    )
    return [
        Charge(
            movement_id=str(row["movement_id"]),
            merchant=str(row["display_name"]),
            normalized=normalize_merchant(row["display_name"]),
            payee_id=str(row["payee_id"]) if row["payee_id"] else None,
            outside_text=bool(row["outside_text"]),
            date=row["date"],
            amount_paise=int(row["amount_paise"]),
        )
        for row in rows
    ]


async def _load_schedules(conn: Connection) -> list[KnownSchedule]:
    return [
        KnownSchedule(
            id=str(row["id"]),
            name=str(row["name"]),
            normalized=normalize_merchant(row["name"]),
            amount_paise=int(row["amount_paise"]) if row["amount_paise"] else None,
        )
        for row in await conn.fetch("SELECT * FROM schedules WHERE active ORDER BY name")
    ]


async def detect_missed(conn: Connection, *, as_of: date | None = None) -> list[dict[str, Any]]:
    """Expected obligation occurrences three or more days old with no payment evidence."""
    day = as_of or ledger_today()
    start = day - timedelta(days=90)
    end = day - timedelta(days=MISSED_AFTER_DAYS)
    proposals: list[dict[str, Any]] = []
    for row in await conn.fetch(
        """
        SELECT * FROM schedules
        WHERE active AND kind IN ('obligation', 'instalment') AND amount_paise IS NOT NULL
        ORDER BY name
        """
    ):
        occurrences = await schedules_mod.expand_schedule(
            conn, row, max(start, row["starts"]), end, as_of=day
        )
        for occurrence in occurrences:
            amount = occurrence["amount_paise"]
            if not amount or amount <= 0 or occurrence["paid_paise"]:
                continue
            expected = date.fromisoformat(occurrence["date"])
            proposals.append(
                {
                    "kind": "missed_recurring",
                    "schedule_id": str(row["id"]),
                    "merchant": str(occurrence["name"]),
                    "outside_text": bool(occurrence["outside_text"]),
                    "amount_paise": int(amount),
                    "expected_date": expected.isoformat(),
                    "days_late": (day - expected).days,
                    "subtype": occurrence["subtype"],
                }
            )
    return proposals


async def detect(conn: Connection, *, as_of: date | None = None) -> list[dict[str, Any]]:
    """Run both detectors over the ledger and return proposals; no schedule is written."""
    day = as_of or ledger_today()
    proposals = detect_recurring(await _load_charges(conn, day), await _load_schedules(conn))
    proposals.extend(await detect_missed(conn, as_of=day))
    proposals.sort(key=lambda item: (item["kind"], item["merchant"]))
    return proposals


__all__ = [
    "AMOUNT_TOLERANCE",
    "INTERVALS",
    "MIN_OCCURRENCES",
    "Charge",
    "KnownSchedule",
    "detect",
    "detect_missed",
    "detect_recurring",
    "normalize_merchant",
]
