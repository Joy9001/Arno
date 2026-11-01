"""Schedules: recurring and one-off cash events, with India's working-day adjustments.

The `rule` is an iCalendar RRULE. "Last working day" is a monthly rule over Monday-Friday plus
`holiday_adjust = 'previous'`; a bill due on a holiday uses `'next'`.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta
from typing import Any

import psycopg

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


def _schedule_row(conn: psycopg.Connection, schedule_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM schedules WHERE id = %s", (schedule_id,)).fetchone()
    if row is None:
        raise NotFoundLedgerError(f"No schedule {schedule_id}")
    return row


def _validate_schedule(
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
    conn: psycopg.Connection,
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
        get_account_row(conn, account_id)
    if category_id is not None:
        row = conn.execute("SELECT 1 FROM categories WHERE id = %s", (category_id,)).fetchone()
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


def schedule_payload(conn: psycopg.Connection, row: dict[str, Any]) -> dict[str, Any]:
    account = (
        conn.execute("SELECT name FROM accounts WHERE id = %s", (row["account_id"],)).fetchone()
        if row["account_id"]
        else None
    )
    category = (
        conn.execute("SELECT name FROM categories WHERE id = %s", (row["category_id"],)).fetchone()
        if row["category_id"]
        else None
    )
    upcoming = expand_schedule(conn, row, today(), today() + timedelta(days=366))
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


def create_schedule(
    conn: psycopg.Connection,
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
    _validate_schedule(
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
    with write_group(conn, actor=actor, message_id=message_id) as group:
        row = conn.execute(
            """
            INSERT INTO schedules (
                kind, subtype, name, amount_paise, account_id, category_id,
                rule, confirmed, starts, ends, holiday_adjust, outside_text
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
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
            ),
        ).fetchone()
        group.record_created("schedules", row)
    return schedule_payload(conn, row) | {"undo_id": group.group_id}


def list_schedules(
    conn: psycopg.Connection, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE active"
    rows = conn.execute(
        f"SELECT * FROM schedules {where} ORDER BY kind, name"  # noqa: S608
    ).fetchall()
    return [schedule_payload(conn, row) for row in rows]


def get_schedule(conn: psycopg.Connection, schedule_id: str) -> dict[str, Any]:
    return schedule_payload(conn, _schedule_row(conn, schedule_id))


def update_schedule(
    conn: psycopg.Connection,
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
    before = _schedule_row(conn, schedule_id)
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
        return schedule_payload(conn, before) | {"undo_id": None}

    merged = {**before, **updates}
    _validate_schedule(
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
    with write_group(conn, actor=actor, message_id=message_id) as group:
        after = conn.execute(
            f"""
            UPDATE schedules SET {", ".join(f"{column} = %s" for column in columns)},
                updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (*[updates[column] for column in columns], schedule_id),
        ).fetchone()
        group.record_updated("schedules", schedule_id, before, after)
    return schedule_payload(conn, after) | {"undo_id": group.group_id}


def archive_schedule(
    conn: psycopg.Connection,
    schedule_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return update_schedule(conn, schedule_id, active=False, actor=actor, message_id=message_id)


def expand_schedule(
    conn: psycopg.Connection, row: dict[str, Any], start: date, end: date
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
        conn.execute("SELECT name FROM accounts WHERE id = %s", (row["account_id"],)).fetchone()
        if row["account_id"]
        else None
    )
    category = (
        conn.execute("SELECT name FROM categories WHERE id = %s", (row["category_id"],)).fetchone()
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


def upcoming(
    conn: psycopg.Connection, *, days: int = 30, start: date | None = None
) -> dict[str, Any]:
    if days < 1 or days > 730:
        raise InvalidLedgerError("days must be between 1 and 730")
    start = start or today()
    end = start + timedelta(days=days)
    events: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM schedules WHERE active ORDER BY name").fetchall():
        events.extend(expand_schedule(conn, row, start, end))

    for card in conn.execute(
        "SELECT * FROM accounts WHERE kind = 'card' AND active ORDER BY name"
    ).fetchall():
        outstanding = max(-ledger_balance(conn, card["ledger_account_id"]), 0)
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
