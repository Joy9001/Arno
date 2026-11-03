"""Bill, EMI, and card-due reminders, N lead days ahead.

The job runs at the notification time and asks the ledger's own `upcoming()` for the next N days.
Each due event becomes one inbox item keyed by its source and due date, so a retry or a later run
inside the lead window does not repeat it.
"""

from __future__ import annotations

import logging
from contextlib import closing
from datetime import date, timedelta
from typing import Any

import psycopg

from fin_agent.db.conn import connect
from fin_agent.india.formatting import format_paise
from fin_agent.ledger import schedules as schedules_mod
from fin_agent.ledger.common import today as ledger_today
from fin_agent.notifications import create_notification, send_push_for_kind

logger = logging.getLogger("fin_agent.jobs.reminders")


def _when_text(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def send_reminders(
    conn: psycopg.Connection,
    *,
    as_of: date | None = None,
    lead_days: int | None = None,
) -> list[dict[str, Any]]:
    day = as_of or ledger_today()
    if lead_days is None:
        row = conn.execute("SELECT reminder_lead_days FROM settings WHERE id = 1").fetchone()
        lead_days = int(row["reminder_lead_days"]) if row else 3
    lead_days = max(0, min(int(lead_days), 30))
    events = schedules_mod.upcoming(conn, days=lead_days, start=day)["events"]
    created: list[dict[str, Any]] = []
    for event in events:
        if event["kind"] not in {"obligation", "instalment", "card_due"}:
            continue
        due = date.fromisoformat(event["date"])
        if due < day or due > day + timedelta(days=lead_days):
            continue
        amount_paise = event["amount_paise"]
        source_id = event["schedule_id"] or event["account_id"]
        key = f"reminder:{source_id}:{event['date']}"
        outside = False
        if event["schedule_id"]:
            schedule = conn.execute(
                "SELECT outside_text FROM schedules WHERE id = %s",
                (event["schedule_id"],),
            ).fetchone()
            outside = bool(schedule and schedule["outside_text"])
        days = (due - day).days
        title = f"{event['name']} is due {_when_text(days)}"
        body = (
            f"Due {event['date']}. Amount {format_paise(int(amount_paise))}."
            if amount_paise is not None
            else f"Due {event['date']}. The amount is unknown."
        )
        row = create_notification(
            conn,
            kind="reminder",
            title=title,
            body=body,
            data={
                "date": event["date"],
                "name": event["name"],
                "kind": event["kind"],
                "amount_paise": amount_paise,
                "source_id": source_id,
                "outside_text": outside,
            },
            dedupe_key=key,
        )
        if row is not None:
            created.append(row)
    return created


def handle_reminders(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    with closing(connect()) as conn:
        rows = send_reminders(conn)
        conn.commit()
        if rows:
            send_push_for_kind(conn, "reminder")
            conn.commit()
        return {"created": len(rows), "ids": [row["id"] for row in rows]}


__all__ = ["handle_reminders", "send_reminders"]
