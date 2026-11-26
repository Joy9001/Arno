"""Recaps: the story of a day, week, or month, computed from ledger rows.

The page, the agent's `recap` tool, and the ready notifications all read this module, so a recap
and an answer about the same period never disagree. Totals and groups come from
`summarize_spending`; the rows behind a figure come from the same spending-account transfers, so
they sum to it. The sentences are templates. See `docs/arno/specs/data-and-engine.md#recaps`.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from arno.db.conn import Connection
from arno.engine.money import Money, money
from arno.india.formatting import format_paise
from arno.ledger.common import today as ledger_today
from arno.ledger.movements import ledger_span
from arno.tools import patterns
from arno.tools.periods import resolve_period

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


def _month_keys(start: date, end: date) -> list[str]:
    keys: list[str] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        keys.append(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return keys


def _merge(intervals: list[tuple[date, date]]) -> list[list[date]]:
    merged: list[list[date]] = []
    for interval_start, interval_end in sorted(intervals):
        if merged and interval_start <= merged[-1][1] + timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], interval_end)
        else:
            merged.append([interval_start, interval_end])
    return merged


async def coverage_by_span(
    conn: Connection,
    spans: list[tuple[date, date]],
    *,
    account_id: str | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Which months of each span have a committed statement. A missing month is a gap, never zero.

    Days after `today` are not a gap: the ledger cannot hold them, so no statement can cover
    them. A month that has not started is unknown, never complete. Without an account filter,
    every active account that has ever committed a statement must cover the month: one
    account's statement must not make another account's spending look known. An account that
    never imports statements (cash, a loan) never holds completeness back. Two queries serve
    every span, so a list of periods costs the same as one.
    """
    day = today or ledger_today()
    params: list[Any] = []
    account_clause = ""
    if account_id is not None:
        account_clause = "AND account_id = $1"
        params.append(account_id)
    rows = await conn.fetch(
        f"""
        SELECT account_id, period_start, period_end
        FROM imports
        WHERE status = 'committed'
          AND undone_at IS NULL
          AND period_start IS NOT NULL AND period_end IS NOT NULL
          {account_clause}
        """,  # noqa: S608
        *params,
    )
    if account_id is not None:
        required = [str(account_id)]
    else:
        required = [
            str(row["id"])
            for row in await conn.fetch(
                """
                SELECT DISTINCT i.account_id AS id
                FROM imports i
                JOIN accounts a ON a.id = i.account_id
                WHERE i.status = 'committed' AND i.undone_at IS NULL AND a.active
                ORDER BY 1
                """
            )
        ]
    results: list[dict[str, Any]] = []
    for start, end in spans:
        intervals: dict[str, list[tuple[date, date]]] = {}
        for row in rows:
            if row["period_end"] >= start and row["period_start"] <= end:
                intervals.setdefault(str(row["account_id"]), []).append(
                    (row["period_start"], row["period_end"])
                )
        merged = {account: _merge(found) for account, found in intervals.items()}
        gaps: list[str] = []
        for key in _month_keys(start, end):
            year, month = (int(part) for part in key.split("-"))
            month_start = date(year, month, 1)
            month_end = date(year, month, calendar.monthrange(year, month)[1])
            # A month counts as covered only for the part of it that was asked about, and only
            # when one statement (or adjacent statements) covers that whole part.
            required_start = max(month_start, start)
            required_end = min(month_end, end)
            if required_start > day:
                gaps.append(key)
                continue
            covered = bool(required) and all(
                any(
                    interval_start <= required_start and interval_end >= min(required_end, day)
                    for interval_start, interval_end in merged.get(account, [])
                )
                for account in required
            )
            if not covered:
                gaps.append(key)
        span = _merge([interval for found in intervals.values() for interval in found])
        covered_from = max(span[0][0], start) if span else None
        covered_to = min(span[-1][1], end) if span else None
        results.append(
            {
                "complete": not gaps,
                "gaps": gaps,
                "from": covered_from.isoformat() if covered_from else None,
                "to": covered_to.isoformat() if covered_to else None,
            }
        )
    return results


async def coverage(
    conn: Connection,
    *,
    account_id: str | None,
    start: date,
    end: date,
    today: date | None = None,
) -> dict[str, Any]:
    """The coverage of one span; see `coverage_by_span`."""
    return (await coverage_by_span(conn, [(start, end)], account_id=account_id, today=today))[0]


async def _resolve_named(conn: Connection, table: str, value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if table == "accounts":
        rows = await conn.fetch(
            "SELECT id, name FROM v_accounts WHERE lower(name) = lower($1) AND active ORDER BY id",
            value,
        )
    elif table == "categories":
        rows = await conn.fetch(
            "SELECT id, name FROM categories WHERE lower(name) = lower($1) AND active ORDER BY id",
            value,
        )
    else:  # pragma: no cover - internal misuse
        raise ValueError(table)
    if not rows:
        raise ValueError(f"No {table[:-1]} named {value!r}")
    if len(rows) > 1:
        # Two accounts or payees can share a name. Picking one would answer about a different
        # set of rows without saying so.
        candidates = ", ".join(f"{row['name']} ({row['id']})" for row in rows)
        raise ValueError(f"{table[:-1].capitalize()} {value!r} is ambiguous: {candidates}")
    return str(rows[0]["id"])


SPEND_FILTERS = frozenset({"account", "category"})


async def summarize_spending(
    conn: Connection,
    *,
    group_by: str = "category",
    period: str = "this_month",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Net spend (refunds offset purchases), grouped by category, payee, month, or not at all."""
    if group_by not in {"category", "payee", "month", "none"}:
        raise ValueError("group_by is category, payee, month, or none")
    filters = filters or {}
    unknown = sorted(set(filters) - SPEND_FILTERS)
    if unknown:
        raise ValueError(f"Unknown filter {unknown}. Filters take {sorted(SPEND_FILTERS)}.")
    day = ledger_today()
    start, end = resolve_period(period, day)
    account_id = await _resolve_named(conn, "accounts", filters.get("account"))
    category_id = await _resolve_named(conn, "categories", filters.get("category"))

    key_sql = {
        "category": "coalesce(c.name, 'Uncategorized')",
        "payee": "coalesce(p.display_name, 'No payee')",
        "month": "to_char(m.date, 'YYYY-MM')",
        "none": "'total'",
    }[group_by]

    # The sum keeps every row, since a reversal nets its original to zero; the count leaves the
    # pair out, so three grocery trips with one corrected entry count as three, not five.
    params: list[Any] = [start, end]
    clauses = [
        "(t.from_account_id = sp.ledger_account_id OR t.to_account_id = sp.ledger_account_id)",
        "m.date BETWEEN $1 AND $2",
    ]
    if account_id:
        params.extend([account_id, account_id])
        clauses.append(f"(fa.id = ${len(params) - 1} OR ta.id = ${len(params)})")
    if category_id:
        params.append(category_id)
        clauses.append(f"ml.category_id = ${len(params)}")

    rows = await conn.fetch(
        f"""
        SELECT {key_sql} AS key,
               sum(
                   CASE WHEN t.to_account_id = sp.ledger_account_id
                        THEN t.amount ELSE -t.amount END
               )::bigint AS amount_paise,
               count(DISTINCT m.id) FILTER (
                   WHERE ms.is_effective
               )::bigint AS n
        FROM movement_legs ml
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        JOIN v_movement_state ms ON ms.movement_id = m.id
        JOIN accounts sp ON sp.kind = 'spending'
        LEFT JOIN v_accounts fa ON fa.ledger_account_id = t.from_account_id
        LEFT JOIN v_accounts ta ON ta.ledger_account_id = t.to_account_id
        LEFT JOIN categories c ON c.id = ml.category_id
        LEFT JOIN payees p ON p.id = ml.payee_id
        WHERE {" AND ".join(clauses)}
        GROUP BY 1
        ORDER BY 2 DESC, 1
        """,  # noqa: S608
        *params,
    )
    groups: list[dict[str, Any]] = []
    for row in rows:
        amount_paise = int(row["amount_paise"])
        count = int(row["n"])
        if amount_paise == 0 and count == 0:
            # A movement a live reversal cancelled leaves nothing to show. It still keeps the
            # period's total defined, but it must not appear as a ₹0 category.
            continue
        groups.append(
            {
                "key": row["key"],
                "amount": money(amount_paise),
                "count": count,
            }
        )
    cov = await coverage(conn, account_id=account_id, start=start, end=end, today=day)
    total_paise = sum(int(row["amount_paise"]) for row in rows)
    total = money(total_paise) if rows or cov["complete"] else None
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "groups": groups,
        "total": total,
        "coverage": cov,
    }


async def _spending_days(conn: Connection, start: date, end: date) -> set[date]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT m.date
        FROM movement_legs ml
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        JOIN v_movement_state ms ON ms.movement_id = m.id
        JOIN accounts sp ON sp.kind = 'spending'
        WHERE t.to_account_id = sp.ledger_account_id AND m.date BETWEEN $1 AND $2
          AND ms.is_effective
        """,
        start,
        end,
    )
    return {row["date"] for row in rows}


async def period_totals(
    conn: Connection, periods: list[Period], *, today: date | None = None
) -> dict[str, Money | None]:
    """Net spend of each period in one grouped query: a money value, or None when the period has
    no entries and its statement coverage is incomplete."""
    if not periods:
        return {}
    day = today or ledger_today()
    rows = await conn.fetch(
        """
        SELECT q.id, sum(
                   CASE WHEN t.to_account_id = sp.ledger_account_id
                        THEN t.amount ELSE -t.amount END
               )::bigint AS amount_paise, count(m.id) AS n
        FROM unnest($1::text[], $2::date[], $3::date[]) AS q(id, d1, d2)
        JOIN movements m ON m.date BETWEEN q.d1 AND q.d2
        JOIN movement_legs ml ON ml.movement_id = m.id
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        JOIN accounts sp ON sp.kind = 'spending'
        WHERE t.from_account_id = sp.ledger_account_id OR t.to_account_id = sp.ledger_account_id
        GROUP BY q.id
        """,
        [period.id for period in periods],
        [period.start for period in periods],
        [min(period.end, day) for period in periods],
    )
    spent = {str(row["id"]): int(row["amount_paise"]) for row in rows}
    covers = await coverage_by_span(
        conn, [(period.start, min(period.end, day)) for period in periods], today=day
    )
    return {
        period.id: (
            money(spent[period.id])
            if period.id in spent
            else (money(0) if cover["complete"] else None)
        )
        for period, cover in zip(periods, covers, strict=True)
    }


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
    totals = await period_totals(conn, periods, today=day)
    for period in periods:
        total = totals[period.id]
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
    unseen = [period for period in latest if period.id not in seen]
    totals = await period_totals(conn, unseen, today=day)
    return sum(1 for total in totals.values() if total and total["paise"] > 0)


async def mark_seen(conn: Connection, period_id: str, *, today: date | None = None) -> None:
    period = parse_period(period_id, today or ledger_today())
    await conn.execute(
        "INSERT INTO recap_views (period_id) VALUES ($1) ON CONFLICT (period_id) DO NOTHING",
        period.id,
    )


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
        FROM movement_legs ml
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        JOIN movements m ON m.id = ml.movement_id
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
            params.append(payee)
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
        FROM movement_legs ml
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        JOIN accounts sp ON sp.kind = 'spending'
        LEFT JOIN categories c ON c.id = ml.category_id
        LEFT JOIN payees p ON p.id = ml.payee_id
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
    found = await patterns.detect(conn, as_of=period.end, write=False)
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
    before_total = int(before["total"]["paise"]) if before["total"] else 0
    comparable = now["coverage"]["complete"] and before["coverage"]["complete"]
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
        return _result(period, now, stories)

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
        return _result(period, now, stories)

    stories.append(
        {
            "key": "total",
            "label": "You spent",
            "figure": money(total),
            "line": (
                _change_line(total, before_total, period.previous_label)
                if comparable
                else "Statement coverage is incomplete; a comparison is unavailable."
            ),
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

    if comparable and before_total > 0:
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
    reversed_ids = {
        str(row["movement_id"])
        for row in await conn.fetch(
            "SELECT movement_id FROM movement_reversal_state() WHERE is_reversed"
        )
    }
    purchases = [
        row
        for row in rows
        if row["kind"] == "expense" and row["id"] not in reversed_ids and row["amount_paise"] > 0
    ]
    if purchases:
        biggest = max(purchases, key=lambda row: (row["amount_paise"], row["date"]))
        who = biggest["payee"] or biggest["description"]
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
    return _result(period, now, stories)


def _result(period: Period, spend: dict[str, Any], stories: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "period": period.public(),
        "stories": stories,
        "coverage": spend["coverage"],
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
