"""Nightly and post-import pattern detection, written to the inbox as proposals only."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from arno.db.conn import Connection, connect
from arno.india.formatting import format_paise
from arno.notifications import create_notification
from arno.tools import patterns

logger = logging.getLogger("arno.jobs.patterns")

_CADENCE_LABEL = {
    7: "week",
    14: "two weeks",
    15: "half-month",
    28: "four weeks",
    30: "month",
    90: "quarter",
    180: "six months",
    365: "year",
}


def _new_subscription(proposal: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    interval = int(proposal["interval_days"])
    cadence = _CADENCE_LABEL.get(interval, f"{interval} days")
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
        "suggested_rule": proposal["rule"]["rrule"],
        "suggestion": "Add it as an obligation if it is yours.",
    }
    return title, body, key, data


def _new_income(proposal: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    interval = int(proposal["interval_days"])
    cadence = _CADENCE_LABEL.get(interval, f"{interval} days")
    amount = format_paise(int(proposal["amount_paise"]))
    title = f"New recurring income: {proposal['merchant']}"
    body = (
        f"{proposal['merchant']} paid {amount} about every {cadence}, "
        f"{proposal['occurrences']} times from {proposal['first_date']} to "
        f"{proposal['last_date']}. Add it as recurring income?"
    )
    key = f"pattern:income:{proposal['normalized']}:{interval}:{proposal['last_date']}"
    data = {
        **proposal,
        "suggested_rule": proposal["rule"]["rrule"],
        "suggestion": "Add it as recurring income if it is yours.",
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


def _price_change(proposal: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    old = format_paise(int(proposal["old_paise"]))
    new = format_paise(int(proposal["new_paise"]))
    ask = (
        "Update the schedule to the new amount?"
        if proposal.get("schedule_id")
        else "Was this expected?"
    )
    title = f"Price change: {proposal['merchant']}"
    body = (
        f"{proposal['merchant']} moved from {old} to {new}, first charged at the new amount on "
        f"{proposal['step_date']}. {ask}"
    )
    identity = proposal.get("schedule_id") or proposal["payee_id"]
    key = f"pattern:price:{identity}:{proposal['step_date']}"
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
        elif proposal["kind"] == "new_income":
            title, body, key, data = _new_income(proposal)
            kind = "new_income"
        elif proposal["kind"] == "price_change":
            title, body, key, data = _price_change(proposal)
            kind = "price_change"
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


async def handle_pattern_detection() -> None:
    async with connect() as conn:
        await run_pattern_detection(conn)


__all__ = ["handle_pattern_detection", "run_pattern_detection"]
