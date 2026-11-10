"""Recaps: the story of a day, week, or month, computed from ledger rows.

The page, the agent's `recap` tool, and the ready notifications all read this module, so a recap
and an answer about the same period never disagree. Totals and groups come from
`summarize_spending`; the rows behind a figure come from the same spending-account transfers, so
they sum to it. The sentences are templates. See `docs/arno/recaps.md`.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from arno.db.conn import Connection
from arno.engine import patterns
from arno.engine.money import money
from arno.india.formatting import format_paise
from arno.ledger.common import today as ledger_today
from arno.ledger.movements import ledger_span
from arno.tools.mark import CLOSE, OPEN, mark
from arno.tools.read import _reversed_ids, summarize_spending

DAYS_LISTED = 7
WEEKS_LISTED = 4
MONTHS_LISTED = 24

_ID = re.compile(r"^(day|week)-(\d{4}-\d{2}-\d{2})$|^month-(\d{4})-(\d{2})$")


@dataclass(frozen=True)
class Period:
    id: str
    kind: str
    start: date
    end: date
    complete: bool
    title: str
    previous_start: date
    previous_end: date
    previous_label: str

    def span(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"

    def previous_span(self) -> str:
        return f"{self.previous_start.isoformat()}..{self.previous_end.isoformat()}"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "complete": self.complete,
            "compared_with": {
                "start": self.previous_start.isoformat(),
                "end": self.previous_end.isoformat(),
                "label": self.previous_label,
            },
        }


def _month_name(day: date, today: date) -> str:
    name = calendar.month_name[day.month]
    return name if day.year == today.year else f"{name} {day.year}"


def _short(day: date) -> str:
    return f"{day.day} {calendar.month_abbr[day.month]}"


def day_period(day: date, today: date) -> Period:
    before = day - timedelta(days=7)
    title = (
        "Yesterday"
        if day == today - timedelta(days=1)
        else (f"{calendar.day_abbr[day.weekday()]}, {_short(day)}")
    )
    return Period(
        id=f"day-{day.isoformat()}",
        kind="day",
        start=day,
        end=day,
        complete=day < today,
        title=title,
        previous_start=before,
        previous_end=before,
        previous_label="the same day last week",
    )


def week_period(monday: date, today: date) -> Period:
    end = monday + timedelta(days=6)
    complete = end < today
    shown_end = end if complete else today
    return Period(
        id=f"week-{monday.isoformat()}",
        kind="week",
        start=monday,
        end=shown_end,
        complete=complete,
        title=f"Week of {_short(monday)}" if complete else "This week so far",
        previous_start=monday - timedelta(days=7),
        previous_end=shown_end - timedelta(days=7),
        previous_label="the week before" if complete else "the same days last week",
    )


def month_period(first: date, today: date) -> Period:
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    complete = last < today
    shown_end = last if complete else today
    previous_end_of_month = first - timedelta(days=1)
    previous_first = previous_end_of_month.replace(day=1)
    if complete:
        previous_end = previous_end_of_month
        label = _month_name(previous_first, today)
    else:
        elapsed = (shown_end - first).days
        previous_end = min(previous_first + timedelta(days=elapsed), previous_end_of_month)
        label = f"the same days of {_month_name(previous_first, today)}"
    return Period(
        id=f"month-{first.strftime('%Y-%m')}",
        kind="month",
        start=first,
        end=shown_end,
        complete=complete,
        title=_month_name(first, today) if complete else f"{_month_name(first, today)} so far",
        previous_start=previous_first,
        previous_end=previous_end,
        previous_label=label,
    )


def parse_period(value: str, today: date) -> Period:
    """A period ID, or the period words the agent uses."""
    word = value.strip().lower().replace(" ", "_")
    monday = today - timedelta(days=today.weekday())
    if word == "yesterday":
        return day_period(today - timedelta(days=1), today)
    if word == "this_week":
        return week_period(monday, today)
    if word == "last_week":
        return week_period(monday - timedelta(days=7), today)
    if word == "this_month":
        return month_period(today.replace(day=1), today)
    if word == "last_month":
        return month_period((today.replace(day=1) - timedelta(days=1)).replace(day=1), today)
    if re.fullmatch(r"\d{4}-\d{2}", word):
        word = f"month-{word}"
    match = _ID.match(word)
    if match is None:
        raise ValueError(
            "A recap period is an ID like month-2026-10, week-2026-11-02, or day-2026-11-08, "
            "or one of yesterday, this_week, last_week, this_month, last_month, 2026-10."
        )
    if match.group(1):
        day = date.fromisoformat(match.group(2))
        if match.group(1) == "day":
            if day >= today:
                raise ValueError("A day recap is for a day that has ended.")
            return day_period(day, today)
        if day.weekday() != 0:
            raise ValueError("A week recap starts on a Monday.")
        if day > today:
            raise ValueError("That week has not started.")
        return week_period(day, today)
    first = date(int(match.group(3)), int(match.group(4)), 1)
    if first > today:
        raise ValueError("That month has not started.")
    return month_period(first, today)


async def _spending_days(conn: Connection, start: date, end: date) -> set[date]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT m.date
        FROM pgledger_transfers t
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        JOIN accounts sp ON sp.kind = 'spending'
        WHERE t.to_account_id = sp.ledger_account_id AND m.date BETWEEN $1 AND $2
          AND m.kind <> 'reversal' AND NOT (m.id = ANY($3))
        """,
        start,
        end,
        await _reversed_ids(conn),
    )
    return {row["date"] for row in rows}


async def list_periods(conn: Connection, *, today: date | None = None) -> list[Period]:
    """Every listed period, newest first within each kind: days, then weeks, then months."""
    day = today or ledger_today()
    span = await ledger_span(conn)
    if span["first_date"] is None:
        return []
    first = date.fromisoformat(span["first_date"])
    spent = await _spending_days(conn, day - timedelta(days=DAYS_LISTED), day - timedelta(days=1))
    days = [
        day_period(day - timedelta(days=offset), day)
        for offset in range(1, DAYS_LISTED + 1)
        if day - timedelta(days=offset) in spent
    ]
    monday = day - timedelta(days=day.weekday())
    weeks = [week_period(monday - timedelta(days=7 * n), day) for n in range(WEEKS_LISTED + 1)]
    months: list[Period] = []
    cursor = day.replace(day=1)
    while len(months) < MONTHS_LISTED + 1:
        months.append(month_period(cursor, day))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        if cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1]) < first:
            break
    return [period for period in [*days, *weeks, *months] if period.end >= first]


def latest_complete(periods: list[Period]) -> dict[str, Period]:
    """The newest complete period of each kind: the only ones that can be new."""
    latest: dict[str, Period] = {}
    for period in periods:
        if period.complete and (period.kind not in latest or period.end > latest[period.kind].end):
            latest[period.kind] = period
    return latest


async def recap_list(conn: Connection, *, today: date | None = None) -> dict[str, Any]:
    """The list the Recaps page shows, with which recaps are new."""
    day = today or ledger_today()
    periods = await list_periods(conn, today=day)
    seen = {str(row["period_id"]) for row in await conn.fetch("SELECT period_id FROM recap_views")}
    latest = {period.id for period in latest_complete(periods).values()}
    items = []
    for period in periods:
        spend = await summarize_spending(conn, group_by="none", period=period.span())
        total = spend["total"]
        # The same rule the badge and the job use: a period is new only when it has spending.
        # A refund-only month has a negative total and is not new.
        is_new = (
            period.id in latest
            and period.id not in seen
            and total is not None
            and total["paise"] > 0
        )
        items.append({**period.public(), "spent": total, "new": is_new})
    return {
        "as_of": day.isoformat(),
        "periods": items,
        "unseen": sum(1 for item in items if item["new"]),
    }


async def recap_unseen(conn: Connection, *, today: date | None = None) -> int:
    """How many recaps are new, without adding up every listed period (the rail polls this)."""
    day = today or ledger_today()
    latest = latest_complete(await list_periods(conn, today=day)).values()
    seen = {
        str(row["period_id"])
        for row in await conn.fetch(
            "SELECT period_id FROM recap_views WHERE period_id = ANY($1)",
            [period.id for period in latest],
        )
    }
    count = 0
    for period in latest:
        if period.id in seen:
            continue
        spend = await summarize_spending(conn, group_by="none", period=period.span())
        if spend["total"] and spend["total"]["paise"] > 0:
            count += 1
    return count


async def mark_seen(conn: Connection, period_id: str, *, today: date | None = None) -> None:
    period = parse_period(period_id, today or ledger_today())
    await conn.execute(
        "INSERT INTO recap_views (period_id) VALUES ($1) ON CONFLICT (period_id) DO NOTHING",
        period.id,
    )


def _plain(text: str) -> str:
    return text.replace(OPEN, "").replace(CLOSE, "")


def _change_line(total: int, before: int, label: str) -> str:
    if before <= 0:
        return f"Nothing was recorded for {label} to compare with."
    change = round((total - before) / before * 100)
    if change == 0:
        return f"About the same as {label}."
    return f"{abs(change)}% {'less' if change < 0 else 'more'} than {label}."


async def _income(conn: Connection, start: date, end: date) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT m.id, m.date, m.description,
               sum(CASE WHEN t.from_account_id = inc.ledger_account_id
                        THEN t.amount ELSE -t.amount END)::bigint AS amount_paise
        FROM pgledger_transfers t
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        JOIN accounts inc ON inc.kind = 'income'
        WHERE (t.from_account_id = inc.ledger_account_id OR t.to_account_id = inc.ledger_account_id)
          AND m.date BETWEEN $1 AND $2
        GROUP BY m.id, m.date, m.description
        HAVING sum(CASE WHEN t.from_account_id = inc.ledger_account_id
                        THEN t.amount ELSE -t.amount END) <> 0
        ORDER BY m.date DESC, m.id
        """,
        start,
        end,
    )
    return [
        {
            "id": row["id"],
            "date": row["date"].isoformat(),
            "description": row["description"],
            "payee": None,
            "category": None,
            "amount_paise": int(row["amount_paise"]),
        }
        for row in rows
    ]


async def spending_rows(
    conn: Connection,
    start: date,
    end: date,
    *,
    category: str | None = None,
    payee: str | None = None,
    movement_id: str | None = None,
) -> list[dict[str, Any]]:
    """Each movement's net spend in the period, one row per movement and category.

    The same transfers `summarize_spending` adds up, so an unfiltered list sums to its total.
    A correction and the entry it reverses both appear when both fall in the period; they net to
    zero, so the sum still matches.
    """
    params: list[Any] = [start, end]
    clauses = [
        "(t.from_account_id = sp.ledger_account_id OR t.to_account_id = sp.ledger_account_id)",
        "m.date BETWEEN $1 AND $2",
    ]
    if category is not None:
        if category == "Uncategorized":
            clauses.append("c.name IS NULL")
        else:
            params.append(category)
            clauses.append(f"c.name = ${len(params)}")
    if payee is not None:
        if payee == "No payee":
            clauses.append("p.display_name IS NULL")
        else:
            params.append(_plain(payee))
            clauses.append(f"p.display_name = ${len(params)}")
    if movement_id is not None:
        params.append(movement_id)
        clauses.append(f"m.id = ${len(params)}")
    signed = "CASE WHEN t.to_account_id = sp.ledger_account_id THEN t.amount ELSE -t.amount END"
    rows = await conn.fetch(
        f"""
        SELECT m.id, m.date, m.description, m.kind,
               c.name AS category, p.display_name AS payee,
               (coalesce(p.outside_text, false) OR coalesce(source.from_source, false)) AS outside,
               sum({signed})::bigint AS amount_paise
        FROM pgledger_transfers t
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        JOIN accounts sp ON sp.kind = 'spending'
        LEFT JOIN transfer_labels tl ON tl.transfer_id = t.id
        LEFT JOIN categories c ON c.id = tl.category_id
        LEFT JOIN payees p ON p.id = tl.payee_id
        LEFT JOIN LATERAL (
            SELECT bool_or(cap.kind IN ('statement_line', 'photo')) AS from_source
            FROM captures cap
            WHERE cap.movement_id = m.id
        ) source ON true
        WHERE {" AND ".join(clauses)}
        GROUP BY m.id, m.date, m.description, m.kind, c.name, p.display_name, p.outside_text,
                 source.from_source
        HAVING sum({signed}) <> 0
        ORDER BY m.date DESC, m.id
        """,  # noqa: S608
        *params,
    )
    return [
        {
            "id": row["id"],
            "date": row["date"].isoformat(),
            "description": row["description"],
            "kind": row["kind"],
            "payee": row["payee"],
            "category": row["category"] or "Uncategorized",
            "outside_text": bool(row["outside"]),
            "amount_paise": int(row["amount_paise"]),
        }
        for row in rows
    ]


def _bars(groups: list[dict[str, Any]], total: int) -> list[dict[str, Any]]:
    top = [
        {
            "name": group["key"],
            "amount": group["amount"],
            "rows": {"story": "where", "key": group["key"]},
        }
        for group in groups[:4]
    ]
    rest = sum(int(group["amount"]["paise"]) for group in groups[4:])
    if rest:
        top.append({"name": "Everything else", "amount": money(rest), "rows": None})
    return top


async def _recurring(conn: Connection, period: Period) -> list[dict[str, Any]]:
    found = await patterns.detect(conn, as_of=period.end)
    return [
        proposal
        for proposal in found
        if proposal["kind"] == "new_subscription"
        and period.start.isoformat() <= proposal["last_date"] <= period.end.isoformat()
    ]


def _cadence(days: int) -> str:
    return {7: "week", 30: "month", 365: "year"}.get(days, f"{days} days")


async def recap(conn: Connection, period_id: str, *, today: date | None = None) -> dict[str, Any]:
    """The stories of one period. Every figure is a money value with the rows behind it."""
    day = today or ledger_today()
    period = parse_period(period_id, day)
    now = await summarize_spending(conn, group_by="category", period=period.span())
    before = await summarize_spending(conn, group_by="category", period=period.previous_span())
    total = int(now["total"]["paise"]) if now["total"] else 0
    # Statement merchants are outside text: marked here, and the flag reaches the conversation.
    outside = bool(now.get("outside_text"))
    before_total = int(before["total"]["paise"]) if before["total"] else 0
    stories: list[dict[str, Any]] = []

    if total < 0:
        # A refund landed in a period with no purchase to offset. The figure is real and must
        # match the list and the rows, not be flattened to zero.
        stories.append(
            {
                "key": "total",
                "label": "You spent",
                "figure": money(total),
                "line": "Refunds came to more than purchases in this period.",
                "rows": {"story": "total", "key": None},
            }
        )
        return _result(period, now, stories, outside)

    if total == 0:
        stories.append(
            {
                "key": "total",
                "label": "You spent",
                "figure": money(0),
                "line": "No spending recorded in this period.",
                "rows": None,
            }
        )
        return _result(period, now, stories, outside)

    stories.append(
        {
            "key": "total",
            "label": "You spent",
            "figure": money(total),
            "line": _change_line(total, before_total, period.previous_label),
            "rows": {"story": "total", "key": None},
        }
    )

    income = sum(row["amount_paise"] for row in await _income(conn, period.start, period.end))
    if income > 0:
        left = income - total
        stories.append(
            {
                "key": "in_out",
                "label": "Money in and out",
                "figure": money(left),
                "line": (
                    f"{format_paise(income)} came in and {format_paise(total)} went out, "
                    f"so {'you kept' if left >= 0 else 'you spent more than came in by'} "
                    f"{format_paise(abs(left))}."
                ),
                "in": money(income),
                "out": money(total),
                "rows": {"story": "in_out", "key": None},
            }
        )

    groups = [group for group in now["groups"] if int(group["amount"]["paise"]) > 0]
    if groups:
        lead = groups[0]
        share = round(int(lead["amount"]["paise"]) / total * 100)
        stories.append(
            {
                "key": "where",
                "label": "Where it went",
                "figure": {**lead["amount"], "name": lead["key"]},
                "line": (
                    f"{lead['amount']['text']}, {share}% of the total, across "
                    f"{lead['count']} {'row' if lead['count'] == 1 else 'rows'}."
                ),
                "bars": _bars(groups, total),
                "rows": {"story": "where", "key": lead["key"]},
            }
        )

    if before_total > 0:
        earlier = {group["key"]: int(group["amount"]["paise"]) for group in before["groups"]}
        current = {group["key"]: int(group["amount"]["paise"]) for group in now["groups"]}
        moves = sorted(
            (
                (current.get(name, 0) - earlier.get(name, 0), name)
                for name in set(current) | set(earlier)
            ),
            key=lambda item: (-abs(item[0]), item[1]),
        )
        if moves and moves[0][0] != 0:
            delta, name = moves[0]
            stories.append(
                {
                    "key": "change",
                    "label": "The change worth a look",
                    "figure": {
                        **money(abs(delta)),
                        "name": name,
                        "direction": "up" if delta > 0 else "down",
                    },
                    "line": (
                        f"{name} went {'up' if delta > 0 else 'down'} by "
                        f"{format_paise(abs(delta))}: "
                        f"{format_paise(current.get(name, 0))}, against "
                        f"{format_paise(earlier.get(name, 0))} in {period.previous_label}."
                    ),
                    "rows": {"story": "change", "key": name},
                }
            )

    rows = await spending_rows(conn, period.start, period.end)
    reversed_ids = set(await _reversed_ids(conn))
    purchases = [
        row
        for row in rows
        if row["kind"] == "expense" and row["id"] not in reversed_ids and row["amount_paise"] > 0
    ]
    if purchases:
        biggest = max(purchases, key=lambda row: (row["amount_paise"], row["date"]))
        who = biggest["payee"] or biggest["description"]
        if biggest["outside_text"]:
            who = mark(who) or who
            outside = True
        stories.append(
            {
                "key": "biggest",
                "label": "The biggest single spend",
                "figure": money(biggest["amount_paise"]),
                "line": f"{who} on {_short(date.fromisoformat(biggest['date']))}.",
                "rows": {"story": "biggest", "key": biggest["id"]},
            }
        )

    payees = await summarize_spending(conn, group_by="payee", period=period.span())
    named = [
        group for group in payees["groups"] if group["key"] != "No payee" and group["count"] > 1
    ]
    if named:
        frequent = sorted(named, key=lambda group: (-group["count"], group["key"]))[0]
        outside = outside or OPEN in str(frequent["key"])
        stories.append(
            {
                "key": "frequent",
                "label": "Your most frequent",
                "figure": {
                    **frequent["amount"],
                    "name": frequent["key"],
                    "count": frequent["count"],
                },
                "line": f"{frequent['count']} times, {frequent['amount']['text']} in all.",
                "rows": {"story": "frequent", "key": frequent["key"]},
            }
        )

    recurring = await _recurring(conn, period)
    for item in recurring:
        if item.get("outside_text"):
            item["merchant"] = mark(item["merchant"]) or item["merchant"]
            outside = True
    if recurring:
        stories.append(
            {
                "key": "recurring",
                "label": "Repeat charges with no schedule",
                "figure": {"count": len(recurring)},
                "line": "; ".join(
                    f"{item['merchant']}, {format_paise(int(item['amount_paise']))} every "
                    f"{_cadence(int(item['interval_days']))}"
                    for item in recurring[:3]
                )
                + ("." if len(recurring) <= 3 else f"; and {len(recurring) - 3} more."),
                "charges": [
                    {
                        "merchant": item["merchant"],
                        "amount": money(int(item["amount_paise"])),
                        "every": _cadence(int(item["interval_days"])),
                        "last_date": item["last_date"],
                    }
                    for item in recurring
                ],
                "rows": None,
            }
        )
    return _result(period, now, stories, outside)


def _result(
    period: Period, spend: dict[str, Any], stories: list[dict[str, Any]], outside: bool
) -> dict[str, Any]:
    return {
        "period": period.public(),
        "stories": stories,
        "coverage": spend["coverage"],
        "outside_text": outside,
    }


async def recap_rows(
    conn: Connection,
    period_id: str,
    *,
    story: str,
    key: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """The movements behind one figure, and their sum, which equals the figure."""
    period = parse_period(period_id, today or ledger_today())
    if story == "in_out":
        rows = await _income(conn, period.start, period.end)
    elif story == "total":
        rows = await spending_rows(conn, period.start, period.end)
    elif story in {"where", "change"}:
        if not key:
            raise ValueError("A category story needs its category as `key`.")
        rows = await spending_rows(conn, period.start, period.end, category=key)
    elif story == "frequent":
        if not key:
            raise ValueError("The frequent story needs its payee as `key`.")
        rows = await spending_rows(conn, period.start, period.end, payee=key)
    elif story == "biggest":
        if not key:
            raise ValueError("The biggest story needs its movement ID as `key`.")
        rows = await spending_rows(conn, period.start, period.end, movement_id=key)
    else:
        raise ValueError("story is one of total, in_out, where, change, biggest, or frequent.")
    return {
        "period": period.public(),
        "story": story,
        "key": key,
        "rows": rows,
        "sum": money(sum(row["amount_paise"] for row in rows)),
    }


__all__ = [
    "Period",
    "latest_complete",
    "list_periods",
    "mark_seen",
    "parse_period",
    "recap",
    "recap_list",
    "recap_rows",
    "recap_unseen",
    "spending_rows",
]
