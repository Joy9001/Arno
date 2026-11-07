"""Schedules: recurring and one-off cash events, with India's working-day adjustments.

The `rule` is an iCalendar RRULE. "Last working day" is a monthly rule over Monday-Friday plus
`holiday_adjust = 'previous'`; a bill due on a holiday uses `'next'`.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.india.recurrence import expand_rule, first_occurrence, parse_rule
from fin_agent.india.workdays import HOLIDAY_ADJUSTMENTS, calendar_for
from fin_agent.ledger.audit import write_group
from fin_agent.ledger.common import get_account_row, ledger_balance, today
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

SCHEDULE_KINDS = (
    "income_fixed",
    "income_variable",
    "income_oneoff",
    "obligation",
    "instalment",
    "goal_contribution",
)
OBLIGATION_SUBTYPES = (
    "rent",
    "emi",
    "subscription",
    "insurance",
    "family_transfer",
    "other",
)


async def _schedule_row(conn: Connection, schedule_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", schedule_id)
    if row is None:
        raise NotFoundLedgerError(f"No schedule {schedule_id}")
    return row


async def _validate_schedule(
    *,
    kind: str,
    subtype: str | None,
    amount_paise: int | None,
    rule: str,
    starts: date,
    ends: date | None,
    holiday_adjust: str,
    account_id: str | None,
    category_id: str | None,
    conn: Connection,
) -> None:
    if kind not in SCHEDULE_KINDS:
        raise InvalidLedgerError(f"Unknown schedule kind {kind}")
    if kind == "obligation":
        if subtype is not None and subtype not in OBLIGATION_SUBTYPES:
            raise InvalidLedgerError(f"Unknown obligation subtype {subtype}")
    elif subtype is not None:
        raise InvalidLedgerError("Only an obligation has a subtype")
    if amount_paise is not None and amount_paise <= 0:
        raise InvalidLedgerError("A schedule amount must be positive")
    if ends is not None and ends < starts:
        raise InvalidLedgerError("A schedule cannot end before it starts")
    if holiday_adjust not in HOLIDAY_ADJUSTMENTS:
        raise InvalidLedgerError("holiday_adjust is none, next, or previous")
    if account_id is not None:
        await get_account_row(conn, account_id)
    if category_id is not None:
        row = await conn.fetchrow("SELECT 1 FROM categories WHERE id = $1", category_id)
        if row is None:
            raise NotFoundLedgerError(f"No category {category_id}")
    try:
        first = first_occurrence(rule, starts)
    except ValueError as exc:
        raise InvalidLedgerError(str(exc)) from exc
    if first is None:
        raise InvalidLedgerError("That RRULE never occurs")
    if kind == "instalment":
        upper = rule.upper()
        if "COUNT=" not in upper and "UNTIL=" not in upper:
            raise InvalidLedgerError("An instalment plan needs COUNT or UNTIL in its RRULE")


async def schedule_payload(conn: Connection, row: dict[str, Any]) -> dict[str, Any]:
    account = (
        await conn.fetchrow("SELECT name FROM accounts WHERE id = $1", row["account_id"])
        if row["account_id"]
        else None
    )
    category = (
        await conn.fetchrow("SELECT name FROM categories WHERE id = $1", row["category_id"])
        if row["category_id"]
        else None
    )
    upcoming = await expand_schedule(conn, row, today(), today() + timedelta(days=366))
    return {
        "id": row["id"],
        "kind": row["kind"],
        "subtype": row["subtype"],
        "name": row["name"],
        "amount_paise": row["amount_paise"],
        "account_id": row["account_id"],
        "account_name": account["name"] if account else None,
        "category_id": row["category_id"],
        "category_name": category["name"] if category else None,
        "rule": row["rule"],
        "holiday_adjust": row["holiday_adjust"],
        "confirmed": row["confirmed"],
        "starts": row["starts"].isoformat(),
        "ends": row["ends"].isoformat() if row["ends"] else None,
        "active": row["active"],
        "outside_text": row["outside_text"],
        "next_date": upcoming[0]["date"] if upcoming else None,
    }


async def create_schedule(
    conn: Connection,
    *,
    kind: str,
    name: str,
    rule: str,
    starts: date,
    amount_paise: int | None = None,
    subtype: str | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    confirmed: bool = True,
    ends: date | None = None,
    holiday_adjust: str = "none",
    outside_text: bool = False,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if not name.strip():
        raise InvalidLedgerError("A schedule needs a name")
    await _validate_schedule(
        kind=kind,
        subtype=subtype,
        amount_paise=amount_paise,
        rule=rule,
        starts=starts,
        ends=ends,
        holiday_adjust=holiday_adjust,
        account_id=account_id,
        category_id=category_id,
        conn=conn,
    )
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        row = await conn.fetchrow(
            """
            INSERT INTO schedules (
                kind, subtype, name, amount_paise, account_id, category_id,
                rule, confirmed, starts, ends, holiday_adjust, outside_text
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING *
            """,
            kind,
            subtype,
            name.strip(),
            amount_paise,
            account_id,
            category_id,
            rule.strip(),
            confirmed,
            starts,
            ends,
            holiday_adjust,
            outside_text,
        )
        group.record_created("schedules", row)
    payload = await schedule_payload(conn, row)
    return payload | {"undo_id": group.group_id}


async def list_schedules(
    conn: Connection, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE active"
    rows = await conn.fetch(
        f"SELECT * FROM schedules {where} ORDER BY kind, name"  # noqa: S608
    )
    return [await schedule_payload(conn, row) for row in rows]


async def get_schedule(conn: Connection, schedule_id: str) -> dict[str, Any]:
    return await schedule_payload(conn, await _schedule_row(conn, schedule_id))


async def update_schedule(
    conn: Connection,
    schedule_id: str,
    *,
    name: str | None = None,
    amount_paise: int | None = None,
    rule: str | None = None,
    subtype: str | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    confirmed: bool | None = None,
    starts: date | None = None,
    ends: date | None = None,
    holiday_adjust: str | None = None,
    active: bool | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    before = await _schedule_row(conn, schedule_id)
    updates: dict[str, Any] = {}
    for key, value in {
        "name": name,
        "amount_paise": amount_paise,
        "rule": rule,
        "subtype": subtype,
        "account_id": account_id,
        "category_id": category_id,
        "confirmed": confirmed,
        "starts": starts,
        "ends": ends,
        "holiday_adjust": holiday_adjust,
        "active": active,
    }.items():
        if value is not None:
            updates[key] = value
    if not updates:
        payload = await schedule_payload(conn, before)
        return payload | {"undo_id": None}

    merged = {**before, **updates}
    await _validate_schedule(
        kind=merged["kind"],
        subtype=merged["subtype"],
        amount_paise=merged["amount_paise"],
        rule=merged["rule"],
        starts=merged["starts"],
        ends=merged["ends"],
        holiday_adjust=merged["holiday_adjust"],
        account_id=merged["account_id"],
        category_id=merged["category_id"],
        conn=conn,
    )
    columns = list(updates)
    assignments = ", ".join(f"{column} = ${index}" for index, column in enumerate(columns, start=1))
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        after = await conn.fetchrow(
            f"""
            UPDATE schedules SET {assignments},
                updated_at = now()
            WHERE id = ${len(columns) + 1} RETURNING *
            """,
            *[updates[column] for column in columns],
            schedule_id,
        )
        group.record_updated("schedules", schedule_id, before, after)
    payload = await schedule_payload(conn, after)
    return payload | {"undo_id": group.group_id}


async def archive_schedule(
    conn: Connection,
    schedule_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return await update_schedule(
        conn, schedule_id, active=False, actor=actor, message_id=message_id
    )


async def expand_schedule(
    conn: Connection, row: dict[str, Any], start: date, end: date
) -> list[dict[str, Any]]:
    dates = expand_rule(
        row["rule"],
        row["starts"],
        start=start,
        end=end,
        ends=row["ends"],
        holiday_adjust=row["holiday_adjust"],
        calendar=calendar_for(start - timedelta(days=7), end + timedelta(days=7)),
    )
    account = (
        await conn.fetchrow("SELECT name FROM accounts WHERE id = $1", row["account_id"])
        if row["account_id"]
        else None
    )
    category = (
        await conn.fetchrow("SELECT name FROM categories WHERE id = $1", row["category_id"])
        if row["category_id"]
        else None
    )
    return [
        {
            "date": day.isoformat(),
            "schedule_id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "subtype": row["subtype"],
            "amount_paise": row["amount_paise"],
            "confirmed": row["confirmed"],
            "account_id": row["account_id"],
            "account_name": account["name"] if account else None,
            "category_id": row["category_id"],
            "category_name": category["name"] if category else None,
        }
        for day in dates
    ]


def _card_due_dates(card: dict[str, Any], start: date, end: date) -> list[date]:
    due_day = card["due_day"]
    if not due_day:
        return []
    cursor = date(start.year, start.month, 1)
    dates: list[date] = []
    while cursor <= end:
        day = min(due_day, _calendar.monthrange(cursor.year, cursor.month)[1])
        candidate = date(cursor.year, cursor.month, day)
        if start <= candidate <= end:
            dates.append(candidate)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return dates


async def upcoming(
    conn: Connection, *, days: int = 30, start: date | None = None
) -> dict[str, Any]:
    if days < 1 or days > 730:
        raise InvalidLedgerError("days must be between 1 and 730")
    start = start or today()
    end = start + timedelta(days=days)
    events: list[dict[str, Any]] = []
    for row in await conn.fetch("SELECT * FROM schedules WHERE active ORDER BY name"):
        events.extend(await expand_schedule(conn, row, start, end))

    for card in await conn.fetch(
        "SELECT * FROM accounts WHERE kind = 'card' AND active ORDER BY name"
    ):
        outstanding = max(-await ledger_balance(conn, card["ledger_account_id"]), 0)
        if outstanding <= 0:
            continue
        for due in _card_due_dates(card, start, end):
            events.append(
                {
                    "date": due.isoformat(),
                    "schedule_id": None,
                    "name": f"{card['name']} bill due",
                    "kind": "card_due",
                    "subtype": None,
                    "amount_paise": outstanding,
                    "confirmed": True,
                    "account_id": card["id"],
                    "account_name": card["name"],
                    "category_id": None,
                    "category_name": None,
                }
            )
    events.sort(key=lambda event: (event["date"], event["name"]))
    return {
        "events": events,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "as_of": today().isoformat(),
    }


__all__ = [
    "OBLIGATION_SUBTYPES",
    "SCHEDULE_KINDS",
    "archive_schedule",
    "create_schedule",
    "expand_schedule",
    "get_schedule",
    "list_schedules",
    "upcoming",
    "update_schedule",
    "parse_rule",
]
