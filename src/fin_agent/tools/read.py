"""The read tools, as plain Python over the database.

PydanticAI never appears here: the agent toolset, a future MCP server, and the REST API all call
these same functions. Every return is JSON-native, every balance carries its `as_of`, a missing
number is `None` rather than zero, and source IDs travel beside the values they explain.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fin_agent.db.conn import Connection, connect
from fin_agent.engine.money import money
from fin_agent.ledger import accounts as accounts_mod
from fin_agent.ledger import categories as categories_mod
from fin_agent.ledger import goals as goals_mod
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger import payees as payees_mod
from fin_agent.ledger import schedules as schedules_mod
from fin_agent.ledger import settings as settings_mod
from fin_agent.ledger.common import has_known_balance
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools.mark import mark
from fin_agent.tools.periods import resolve_period


def _month_keys(start: date, end: date) -> list[str]:
    keys: list[str] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        keys.append(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return keys


async def coverage(
    conn: Connection,
    *,
    account_id: str | None,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Which months have a committed statement. A missing month is a gap, never zero."""
    params: list[Any] = [start, end]
    account_clause = ""
    if account_id is not None:
        account_clause = f"AND account_id = ${len(params) + 1}"
        params.append(account_id)
    rows = await conn.fetch(
        f"""
        SELECT account_id, period_start, period_end
        FROM imports
        WHERE status = 'committed'
          AND period_start IS NOT NULL AND period_end IS NOT NULL
          AND period_end >= $1 AND period_start <= $2
          {account_clause}
        """,  # noqa: S608
        *params,
    )
    gaps: list[str] = []
    intervals = sorted(
        (row["period_start"], row["period_end"])
        for row in rows
        if row["period_start"] is not None and row["period_end"] is not None
    )
    merged: list[list[date]] = []
    for interval_start, interval_end in intervals:
        if merged and interval_start <= merged[-1][1] + timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], interval_end)
        else:
            merged.append([interval_start, interval_end])
    for key in _month_keys(start, end):
        year, month = (int(part) for part in key.split("-"))
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        # A month counts as covered only for the part of it that was asked about, and only when
        # one statement (or adjacent statements) covers that whole part. A statement for the
        # first five days must not make the rest of the month look known.
        required_start = max(month_start, start)
        required_end = min(month_end, end)
        covered = any(
            interval_start <= required_start and interval_end >= required_end
            for interval_start, interval_end in merged
        )
        if not covered:
            gaps.append(key)
    covered_from = max(merged[0][0], start) if merged else None
    covered_to = min(merged[-1][1], end) if merged else None
    return {
        "complete": not gaps,
        "gaps": gaps,
        "from": covered_from.isoformat() if covered_from else None,
        "to": covered_to.isoformat() if covered_to else None,
    }


async def _account_coverage(
    conn: Connection, account_id: str, start: date, end: date
) -> dict[str, Any]:
    return await coverage(conn, account_id=account_id, start=start, end=end)


async def _payee_is_outside(conn: Connection, payee_id: str | None) -> bool:
    if not payee_id:
        return False
    row = await conn.fetchrow("SELECT outside_text FROM payees WHERE id = $1", payee_id)
    return bool(row and row["outside_text"])


async def get_financial_context(conn: Connection) -> dict[str, Any]:
    """One comprehensive snapshot: settings, balances, the next 30 days, goals, owed, setup gaps."""
    day = ledger_today()
    settings = await settings_mod.read_settings(conn)
    account_rows = await accounts_mod.list_accounts(conn)
    outside = False
    accounts: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in account_rows:
        known = has_known_balance(row)
        as_of = (row["balance_as_of"] or row["statement_balance_as_of"]) if known else None
        balance = money(row["balance_paise"]) if known else None
        coverage_row = await _account_coverage(conn, row["id"], day - timedelta(days=366), day)
        age = row["statement_age_days"]
        if age is not None and age > 30:
            warnings.append(f"{row['name']} balance is {age} days old")
        if coverage_row["gaps"]:
            warnings.append(
                f"{row['name']} has no statement covering {len(coverage_row['gaps'])} month(s)"
            )
        if not known:
            warnings.append(f"{row['name']} has no stated balance")
        # A known zero is a real zero; only an absent card value falls through to the loan value.
        outstanding_paise = row.get("card_outstanding_paise")
        if outstanding_paise is None:
            outstanding_paise = row.get("loan_outstanding_paise")
        accounts.append(
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "liquid": row["liquid"],
                "known": known,
                "balance": balance,
                "as_of": as_of or None,
                "statement_age_days": age,
                "credit_limit": money(row["credit_limit_paise"]),
                "statement_day": row["statement_day"],
                "due_day": row["due_day"],
                "available_credit": money(row.get("available_credit_paise")),
                "outstanding": money(outstanding_paise),
                "covered": {
                    "from": coverage_row["from"],
                    "to": coverage_row["to"],
                    "gaps": coverage_row["gaps"],
                },
            }
        )

    liquid_rows = [row for row in account_rows if row["liquid"]]
    liquid_known = [row for row in liquid_rows if has_known_balance(row)]
    liquid_total = (
        money(sum(int(row["balance_paise"]) for row in liquid_known))
        if len(liquid_known) == len(liquid_rows)
        else None
    )

    upcoming_rows = (await schedules_mod.upcoming(conn, days=30, start=day))["events"]
    upcoming: list[dict[str, Any]] = []
    for event in upcoming_rows:
        name = event["name"]
        schedule_id = event["schedule_id"]
        if schedule_id:
            schedule = await conn.fetchrow(
                "SELECT outside_text FROM schedules WHERE id = $1", schedule_id
            )
            if schedule and schedule["outside_text"]:
                name = mark(name) or name
                outside = True
        upcoming.append(
            {
                "date": event["date"],
                "name": name,
                "kind": event["kind"],
                "subtype": event["subtype"],
                "amount": money(event["amount_paise"]),
                "confirmed": event["confirmed"],
                "source_id": schedule_id or event["account_id"],
            }
        )

    goal_rows = await goals_mod.list_goals(conn)
    goals = [
        {
            "id": row["id"],
            "name": row["name"],
            "target": money(row["target_paise"]),
            "saved": money(row["saved_paise"]),
            "remaining": money(row["remaining_paise"]),
            "deadline": row["deadline"],
            "priority": row["priority"],
            "status": row["status"],
        }
        for row in goal_rows
    ]

    schedules: list[dict[str, Any]] = []
    for row in await schedules_mod.list_schedules(conn):
        name = row["name"]
        if row["outside_text"]:
            name = mark(name) or name
            outside = True
        schedules.append(
            {
                "id": row["id"],
                "name": name,
                "kind": row["kind"],
                "subtype": row["subtype"],
                "amount": money(row["amount_paise"]),
                "rule": row["rule"],
                "next_date": row["next_date"],
                "confirmed": row["confirmed"],
                "active": row["active"],
            }
        )

    owed_rows = await conn.fetch(
        """
        SELECT v.payee_id, v.display_name, v.balance_paise, v.direction,
               v.next_expected_date, p.outside_text
        FROM v_owed v
        JOIN payees p ON p.id = v.payee_id
        ORDER BY v.display_name
        """
    )
    owed: list[dict[str, Any]] = []
    for row in owed_rows:
        if int(row["balance_paise"]) == 0:
            continue
        name = row["display_name"]
        if row["outside_text"]:
            name = mark(name) or name
            outside = True
        owed.append(
            {
                "person": name,
                "payee_id": row["payee_id"],
                "amount": money(abs(int(row["balance_paise"]))),
                "direction": row["direction"],
                "expected_date": row["next_expected_date"].isoformat()
                if row["next_expected_date"]
                else None,
            }
        )

    preferences = [
        {"id": row["id"], "text": row["body"]}
        for row in await conn.fetch(
            """
            SELECT id, body FROM preferences
            WHERE active AND deleted_at IS NULL
            ORDER BY created_at
            """
        )
    ]

    income_count = (
        await conn.fetchrow(
            "SELECT count(*) AS n FROM schedules WHERE active AND kind LIKE 'income%'"
        )
    )["n"]
    obligation_count = (
        await conn.fetchrow(
            "SELECT count(*) AS n FROM schedules "
            "WHERE active AND kind IN ('obligation', 'instalment')"
        )
    )["n"]
    baseline_count = (await conn.fetchrow("SELECT count(*) AS n FROM baselines"))["n"]
    setup_gaps: list[str] = []
    if not account_rows:
        setup_gaps.append("no accounts added")
    if not income_count:
        setup_gaps.append("no income schedule")
    if not obligation_count:
        setup_gaps.append("no obligations")
    if not goal_rows:
        setup_gaps.append("no goals")
    if settings["floor_paise"] is None:
        setup_gaps.append("no floor set")
    if not baseline_count:
        setup_gaps.append("no estimated baselines")
    if any(not has_known_balance(row) for row in account_rows):
        setup_gaps.append("an account has no stated balance")

    return {
        "today": day.isoformat(),
        "settings": {
            "currency": settings["base_currency"],
            "floor": money(settings["floor_paise"]),
            "default_horizon_days": settings["default_horizon_days"],
            "time_zone": settings["time_zone"],
        },
        "accounts": accounts,
        "liquid_total": liquid_total,
        "upcoming": upcoming,
        "schedules": schedules,
        "goals": goals,
        "owed": owed,
        "preferences": preferences,
        "setup_gaps": setup_gaps,
        "warnings": warnings,
        "source_ids": {
            "accounts": [row["id"] for row in account_rows],
            "schedules": [row["id"] for row in schedules],
            "goals": [row["id"] for row in goal_rows],
            "payees": [row["payee_id"] for row in owed_rows],
        },
        "outside_text": outside,
    }


async def _resolve_named(conn: Connection, table: str, value: str | None) -> str | None:
    if value is None:
        return None
    if table == "accounts":
        row = await conn.fetchrow(
            "SELECT id FROM accounts WHERE lower(name) = lower($1) AND active LIMIT 1", value
        )
    elif table == "categories":
        row = await conn.fetchrow(
            "SELECT id FROM categories WHERE lower(name) = lower($1) AND active LIMIT 1",
            value,
        )
    elif table == "payees":
        row = await conn.fetchrow(
            """
            SELECT id FROM payees WHERE active AND (lower(display_name) = lower($1) OR upi_id = $2)
            ORDER BY display_name LIMIT 1
            """,
            value,
            value,
        )
    else:  # pragma: no cover - internal misuse
        raise ValueError(table)
    if row is None:
        raise ValueError(f"No {table[:-1]} named {value!r}")
    return str(row["id"])


MOVEMENT_FILTERS = frozenset(
    {
        "period",
        "account",
        "category",
        "payee",
        "kind",
        "min_paise",
        "max_paise",
        "text",
        "include_reversed",
    }
)
SPEND_FILTERS = frozenset({"account", "category", "payee", "text"})


def _check_filters(filters: dict[str, Any], allowed: frozenset[str], period_hint: str) -> None:
    """An unknown key is an error, never ignored: a dropped filter answers a different question."""
    unknown = sorted(set(filters) - allowed)
    if unknown:
        raise ValueError(
            f"Unknown filter {', '.join(map(repr, unknown))}. Filters take "
            f"{', '.join(sorted(allowed))}. {period_hint}"
        )


async def _reversed_ids(conn: Connection) -> list[str]:
    """Movements a live reversal cancels. A corrected entry and its reversal are bookkeeping, not
    activity, so lists and counts leave both out. Few exist, so one pass covers the ledger."""
    ids = [
        str(row["id"])
        for row in await conn.fetch(
            "SELECT DISTINCT reverses_id AS id FROM movements WHERE reverses_id IS NOT NULL"
        )
    ]
    states = await movements_mod._reversal_state(conn, ids)  # noqa: SLF001
    return [movement_id for movement_id in ids if states[movement_id]["reversed"]]


async def query_movements(
    conn: Connection,
    *,
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List individual movements with their transfers, labels, and evidence."""
    filters = filters or {}
    _check_filters(
        filters,
        MOVEMENT_FILTERS,
        'A date range goes in period as "YYYY-MM-DD..YYYY-MM-DD".',
    )
    day = ledger_today()
    start = end = None
    if filters.get("period"):
        start, end = resolve_period(filters["period"], day)
    account_id = await _resolve_named(conn, "accounts", filters.get("account"))
    category_id = await _resolve_named(conn, "categories", filters.get("category"))
    payee_id = await _resolve_named(conn, "payees", filters.get("payee"))

    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        params.append(start)
        clauses.append(f"m.date >= ${len(params)}")
    if end is not None:
        params.append(end)
        clauses.append(f"m.date <= ${len(params)}")
    if filters.get("kind"):
        params.append(filters["kind"])
        clauses.append(f"m.kind = ${len(params)}")
    if account_id:
        params.extend([account_id, account_id])
        clauses.append(
            f"""
            EXISTS (
                SELECT 1 FROM pgledger_transfers t
                JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
                JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
                WHERE t.metadata ->> 'movement_id' = m.id
                  AND (fa.id = ${len(params) - 1} OR ta.id = ${len(params)})
            )
            """
        )
    if category_id or payee_id or filters.get("text"):
        label_clauses = ["tl.movement_id = m.id"]
        if category_id:
            params.append(category_id)
            label_clauses.append(f"tl.category_id = ${len(params)}")
        if payee_id:
            params.append(payee_id)
            label_clauses.append(f"tl.payee_id = ${len(params)}")
        if filters.get("text"):
            pattern = f"%{filters['text']}%"
            params.extend([pattern, pattern])
            label_clauses.append(
                f"(tl.note ILIKE ${len(params) - 1} OR m.description ILIKE ${len(params)})"
            )
        clauses.append(
            f"EXISTS (SELECT 1 FROM transfer_labels tl WHERE {' AND '.join(label_clauses)})"
        )
    if filters.get("min_paise") is not None or filters.get("max_paise") is not None:
        inner: list[str] = []
        if filters.get("min_paise") is not None:
            params.append(filters["min_paise"])
            inner.append(f"amount >= ${len(params)}")
        if filters.get("max_paise") is not None:
            params.append(filters["max_paise"])
            inner.append(f"amount <= ${len(params)}")
        clauses.append(
            "EXISTS (SELECT 1 FROM pgledger_transfers t WHERE t.metadata ->> 'movement_id' = m.id "
            f"AND {' AND '.join(inner)})"
        )
    if cursor:
        try:
            cursor_date, cursor_id = cursor.split("|", 1)
            params.extend([date.fromisoformat(cursor_date), cursor_id])
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid cursor") from exc
        clauses.append(f"(m.date, m.id) < (${len(params) - 1}, ${len(params)})")
    if not filters.get("include_reversed"):
        # In SQL, not after LIMIT, so total_count, the page, and next_cursor agree.
        clauses.append("m.kind <> 'reversal'")
        hidden = await _reversed_ids(conn)
        if hidden:
            params.append(hidden)
            clauses.append(f"NOT (m.id = ANY(${len(params)}))")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = int(
        (
            await conn.fetchrow(
                f"SELECT count(*) AS n FROM movements m {where}",  # noqa: S608
                *params,
            )
        )["n"]
    )
    params.append(limit + 1)
    rows = await conn.fetch(
        f"""
        SELECT m.* FROM movements m {where}
        ORDER BY m.date DESC, m.id DESC
        LIMIT ${len(params)}
        """,  # noqa: S608
        *params,
    )
    states = await movements_mod._reversal_state(  # noqa: SLF001
        conn, [str(row["id"]) for row in rows]
    )
    has_more = len(rows) > limit
    visible = rows[:limit]

    outside = False
    items: list[dict[str, Any]] = []
    for row in visible:
        payload = await movements_mod.movement_payload(conn, row, state=states[str(row["id"])])
        is_outside = [
            await _payee_is_outside(conn, transfer.get("payee_id"))
            for transfer in payload["transfers"]
        ]
        transfers: list[dict[str, Any]] = []
        for transfer, hidden_text in zip(payload["transfers"], is_outside, strict=False):
            if hidden_text:
                transfer["payee_name"] = mark(transfer["payee_name"])
                outside = True
            transfers.append(
                {
                    **transfer,
                    "from": transfer["from_name"],
                    "to": transfer["to_name"],
                    "amount": money(int(transfer["amount_paise"])),
                }
            )
        if any(is_outside):
            payload["description"] = mark(payload["description"])
        items.append(
            {
                "id": payload["id"],
                "date": payload["date"],
                "kind": payload["kind"],
                "description": payload["description"],
                "payee": payload["payee_name"],
                "category": payload["category_name"],
                "amount": money(int(payload["amount_paise"])),
                "amount_paise": payload["amount_paise"],
                "transfers": transfers,
                "evidence": payload["evidence"],
                "undo_id": payload["undo_id"],
            }
        )

    last = visible[-1] if visible else None
    return {
        "rows": items,
        "total_count": total,
        "next_cursor": f"{last['date'].isoformat()}|{last['id']}" if has_more and last else None,
        "coverage": await coverage(
            conn,
            account_id=account_id,
            start=start or day - timedelta(days=366),
            end=end or day,
        ),
        "outside_text": outside,
    }


async def summarize_spending(
    conn: Connection,
    *,
    group_by: str = "category",
    period: str = "this_month",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Net spend (refunds offset purchases), grouped by category, payee, account, month, or week."""
    if group_by not in {"category", "payee", "account", "month", "week", "none"}:
        raise ValueError("group_by is category, payee, account, month, week, or none")
    filters = filters or {}
    _check_filters(
        filters,
        SPEND_FILTERS,
        'The period is its own argument: "last_month", "2026-09", or "YYYY-MM-DD..YYYY-MM-DD".',
    )
    day = ledger_today()
    start, end = resolve_period(period, day)
    account_id = await _resolve_named(conn, "accounts", filters.get("account"))
    category_id = await _resolve_named(conn, "categories", filters.get("category"))
    payee_id = await _resolve_named(conn, "payees", filters.get("payee"))

    key_sql = {
        "category": "coalesce(c.name, 'Uncategorized')",
        "payee": "coalesce(p.display_name, 'No payee')",
        "account": (
            "CASE WHEN t.to_account_id = sp.ledger_account_id THEN fa.name ELSE ta.name END"
        ),
        "month": "to_char(m.date, 'YYYY-MM')",
        "week": "to_char(m.date, 'IYYY-\"W\"IW')",
        "none": "'total'",
    }[group_by]

    # The sum keeps every row, since a reversal nets its original to zero; the count leaves the
    # pair out, so three grocery trips with one corrected entry count as three, not five.
    params: list[Any] = [await _reversed_ids(conn), start, end]
    clauses = [
        "t.metadata ->> 'movement_id' = m.id",
        "(t.from_account_id = sp.ledger_account_id OR t.to_account_id = sp.ledger_account_id)",
        "m.date BETWEEN $2 AND $3",
    ]
    if account_id:
        params.extend([account_id, account_id])
        clauses.append(f"(fa.id = ${len(params) - 1} OR ta.id = ${len(params)})")
    if category_id:
        params.append(category_id)
        clauses.append(f"tl.category_id = ${len(params)}")
    if payee_id:
        params.append(payee_id)
        clauses.append(f"tl.payee_id = ${len(params)}")
    if filters.get("text"):
        pattern = f"%{filters['text']}%"
        params.extend([pattern, pattern])
        clauses.append(f"(tl.note ILIKE ${len(params) - 1} OR m.description ILIKE ${len(params)})")

    rows = await conn.fetch(
        f"""
        SELECT {key_sql} AS key,
               sum(
                   CASE WHEN t.to_account_id = sp.ledger_account_id
                        THEN t.amount ELSE -t.amount END
               )::bigint AS amount_paise,
               count(DISTINCT m.id) FILTER (
                   WHERE m.kind <> 'reversal' AND NOT (m.id = ANY($1))
               )::bigint AS n,
               (array_agg(tl.payee_id) FILTER (WHERE tl.payee_id IS NOT NULL))[1] AS payee_id
        FROM pgledger_transfers t
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        JOIN accounts sp ON sp.kind = 'spending'
        LEFT JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        LEFT JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        LEFT JOIN transfer_labels tl ON tl.transfer_id = t.id
        LEFT JOIN categories c ON c.id = tl.category_id
        LEFT JOIN payees p ON p.id = tl.payee_id
        WHERE {" AND ".join(clauses)}
        GROUP BY 1
        ORDER BY 2 DESC, 1
        """,  # noqa: S608
        *params,
    )
    outside = False
    groups: list[dict[str, Any]] = []
    for row in rows:
        key = row["key"]
        if group_by == "payee" and await _payee_is_outside(conn, row.get("payee_id")):
            key = mark(key)
            outside = True
        groups.append(
            {
                "key": key,
                "amount": money(int(row["amount_paise"])),
                "count": int(row["n"]),
            }
        )
    cov = await coverage(conn, account_id=account_id, start=start, end=end)
    total_paise = sum(int(row["amount_paise"]) for row in rows)
    total = money(total_paise) if rows or cov["complete"] else None
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "groups": groups,
        "total": total,
        "coverage": cov,
        "outside_text": outside,
    }


async def run_query(sql: str, *, deadline_seconds: float = 2.0) -> dict[str, Any]:
    """Run one read-only statement as `agent_ro`, always rolled back, always marked."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise ValueError("No SQL was given")
    async with connect(role="agent_ro") as conn:
        tx = conn.transaction(readonly=True)
        await tx.start()
        try:
            await conn.execute("SET LOCAL statement_timeout = '2s'")
            stmt = await conn.prepare(text, timeout=deadline_seconds)
            attributes = stmt.get_attributes()
            if not attributes:
                raise ValueError("run_query needs a statement that returns rows")
            columns = [attr.name for attr in attributes]
            cursor = await stmt.cursor()
            records = await cursor.fetch(1001, timeout=deadline_seconds)
        finally:
            await tx.rollback()
    truncated = len(records) > 1000
    converted: list[list[Any]] = []
    for record in records[:1000]:
        values_in = list(record)
        values: list[Any] = []
        for value in values_in:
            if isinstance(value, Decimal):
                value = int(value) if value == value.to_integral_value() else float(value)
            elif isinstance(value, (date, datetime)):
                value = value.isoformat()
            elif isinstance(value, str):
                value = mark(value)
            values.append(value)
        converted.append(values)
    return {
        "columns": columns,
        "rows": converted,
        "row_count": len(converted),
        "truncated": truncated,
        "sql": text,
        "outside_text": True,
    }


async def active_preferences(conn: Connection) -> list[dict[str, Any]]:
    return await conn.fetch(
        """
        SELECT id, body, created_by, active, created_at, updated_at
        FROM preferences WHERE deleted_at IS NULL ORDER BY created_at
        """
    )


async def list_categories(conn: Connection) -> list[dict[str, Any]]:
    return await categories_mod.list_categories(conn)


async def list_payees(conn: Connection) -> list[dict[str, Any]]:
    return await payees_mod.list_payees(conn)


__all__ = [
    "active_preferences",
    "coverage",
    "get_financial_context",
    "list_categories",
    "list_payees",
    "query_movements",
    "run_query",
    "summarize_spending",
]
