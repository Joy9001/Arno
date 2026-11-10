"""Nightly and post-import pattern detection, written to the inbox as proposals only."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from arno.db.conn import Connection, connect
from arno.engine import patterns
from arno.india.formatting import format_paise
from arno.notifications import create_notification, send_push_for_kind

logger = logging.getLogger("arno.jobs.patterns")


def _suggested_rule(interval: int, last_date: date) -> str:
    if interval == 7:
        days = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
        return f"FREQ=WEEKLY;BYDAY={days[last_date.weekday()]}"
    if interval == 365:
        return f"FREQ=YEARLY;BYMONTH={last_date.month};BYMONTHDAY={last_date.day}"
    # BYMONTHDAY=31 has no occurrence in a short month; the last day is the charge's real day.
    day = -1 if last_date.day >= 29 else last_date.day
    return f"FREQ=MONTHLY;BYMONTHDAY={day}"


def _new_subscription(proposal: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    interval = int(proposal["interval_days"])
    cadence = {7: "week", 30: "month", 365: "year"}.get(interval, f"{interval} days")
    last = date.fromisoformat(proposal["last_date"])
    amount = format_paise(int(proposal["amount_paise"]))
    title = f"New recurring charge: {proposal['merchant']}"
    body = (
        f"{proposal['merchant']} charged {amount} about every {cadence}, "
        f"{proposal['occurrences']} times from {proposal['first_date']} to "
        f"{proposal['last_date']}. No schedule matches it. Add it as an obligation?"
    )
    key = f"pattern:new:{proposal['normalized']}:{interval}:{proposal['last_date']}"
    data = {
        **proposal,
        "suggested_rule": _suggested_rule(interval, last),
        "suggestion": "Add it as an obligation if it is yours.",
    }
    return title, body, key, data


def _missed(proposal: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    amount = format_paise(int(proposal["amount_paise"]))
    title = f"Missed recurring charge: {proposal['merchant']}"
    body = (
        f"{proposal['merchant']} was expected on {proposal['expected_date']} "
        f"({amount}) and is {proposal['days_late']} days late. No matching payment is in "
        "the ledger."
    )
    key = f"pattern:missed:{proposal['schedule_id']}:{proposal['expected_date']}"
    return title, body, key, proposal


async def run_pattern_detection(
    conn: Connection,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for proposal in await patterns.detect(conn, as_of=as_of):
        if proposal["kind"] == "new_subscription":
            title, body, key, data = _new_subscription(proposal)
            kind = "new_subscription"
        else:
            title, body, key, data = _missed(proposal)
            kind = "missed_recurring"
        row = await create_notification(
            conn,
            kind=kind,
            title=title,
            body=body,
            data=data,
            dedupe_key=key,
        )
        if row is not None:
            created.append(row)
    return created


async def handle_pattern_detection(payload: dict[str, Any]) -> dict[str, Any]:
    async with connect() as conn:
        rows = await run_pattern_detection(conn)
        for kind in ("new_subscription", "missed_recurring"):
            if any(row["kind"] == kind for row in rows):
                await send_push_for_kind(conn, kind)
        return {"created": len(rows), "ids": [row["id"] for row in rows]}


__all__ = ["handle_pattern_detection", "run_pattern_detection"]
