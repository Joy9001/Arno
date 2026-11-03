"""Nightly and post-import pattern detection, written to the inbox as proposals only."""

from __future__ import annotations

import logging
from contextlib import closing
from datetime import date
from typing import Any

from fin_agent.db.conn import connect
from fin_agent.engine import patterns
from fin_agent.india.formatting import format_paise
from fin_agent.notifications import create_notification, send_push_for_kind

logger = logging.getLogger("fin_agent.jobs.patterns")


def _suggested_rule(interval: int, last_date: date) -> str:
    if interval == 7:
        days = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
        return f"FREQ=WEEKLY;BYDAY={days[last_date.weekday()]}"
    if interval == 365:
        return f"FREQ=YEARLY;BYMONTH={last_date.month};BYMONTHDAY={last_date.day}"
    return f"FREQ=MONTHLY;BYMONTHDAY={last_date.day}"


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


def run_pattern_detection(
    conn,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for proposal in patterns.detect(conn, as_of=as_of):
        if proposal["kind"] == "new_subscription":
            title, body, key, data = _new_subscription(proposal)
            kind = "new_subscription"
        else:
            title, body, key, data = _missed(proposal)
            kind = "missed_recurring"
        row = create_notification(
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


def handle_pattern_detection(payload: dict[str, Any]) -> dict[str, Any]:
    with closing(connect()) as conn:
        rows = run_pattern_detection(conn)
        conn.commit()
        if rows:
            send_push_for_kind(conn, "new_subscription")
            conn.commit()
        return {"created": len(rows), "ids": [row["id"] for row in rows]}


__all__ = ["handle_pattern_detection", "run_pattern_detection"]
