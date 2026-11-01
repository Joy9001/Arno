"""The read tools, as plain Python over the database.

PydanticAI never appears here: the agent toolset, a future MCP server, and the REST API all call
these same functions. Every return is JSON-native, every balance carries its `as_of`, a missing
number is `None` rather than zero, and source IDs travel beside the values they explain.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from fin_agent.db.conn import connect
from fin_agent.engine.money import money
from fin_agent.ledger import accounts as accounts_mod
from fin_agent.ledger import categories as categories_mod
from fin_agent.ledger import goals as goals_mod
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger import payees as payees_mod
from fin_agent.ledger import schedules as schedules_mod
from fin_agent.ledger import settings as settings_mod
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


def coverage(
    conn: psycopg.Connection,
    *,
    account_id: str | None,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Which months have a committed statement. A missing month is a gap, never zero."""
    params: list[Any] = [start, end]
    account_clause = ""
    if account_id is not None:
        account_clause = "AND account_id = %s"
        params.append(account_id)
    rows = list(
        conn.execute(
            f"""
            SELECT account_id, period_start, period_end
            FROM imports
            WHERE status = 'committed'
              AND period_start IS NOT NULL AND period_end IS NOT NULL
              AND period_end >= %s AND period_start <= %s
              {account_clause}
            """,  # noqa: S608
            params,
        ).fetchall()
    )
    gaps: list[str] = []
    for key in _month_keys(start, end):
        year, month = (int(part) for part in key.split("-"))
        month_start = date(year, month, 1)
        month_end = date(year, month, 28)
        covered = any(
            row["period_start"] <= month_end and row["period_end"] >= month_start for row in rows
        )
        if not covered:
            gaps.append(key)
    return {
        "complete": not gaps,
        "gaps": gaps,
        "from": start.isoformat(),
        "to": end.isoformat(),
    }


def _account_coverage(
    conn: psycopg.Connection, account_id: str, start: date, end: date
) -> dict[str, Any]:
    return coverage(conn, account_id=account_id, start=start, end=end)


def _payee_is_outside(conn: psycopg.Connection, payee_id: str | None) -> bool:
    if not payee_id:
        return False
    row = conn.execute("SELECT outside_text FROM payees WHERE id = %s", (payee_id,)).fetchone()
    return bool(row and row["outside_text"])


def get_financial_context(conn: psycopg.Connection) -> dict[str, Any]:
    """One comprehensive snapshot: settings, balances, the next 30 days, goals, owed, setup gaps."""
    day = ledger_today()
    settings = settings_mod.read_settings(conn)
    account_rows = accounts_mod.list_accounts(conn)
    outside = False
    accounts: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in account_rows:
        as_of = row["balance_as_of"] or row["statement_balance_as_of"]
        known = as_of is not None or row["statement_balance_paise"] is not None
        balance = money(row["balance_paise"]) if known else None
        coverage_row = _account_coverage(conn, row["id"], day - timedelta(days=366), day)
        age = row["statement_age_days"]
        if age is not None and age > 30:
            warnings.append(f"{row['name']} balance is {age} days old")
        if coverage_row["gaps"]:
            warnings.append(
                f"{row['name']} has no statement covering {len(coverage_row['gaps'])} month(s)"
            )
        if not known:
            warnings.append(f"{row['name']} has no stated balance")
        accounts.append(
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "liquid": row["liquid"],
                "balance": balance,
                "as_of": as_of or None,
                "statement_age_days": age,
                "credit_limit": money(row["credit_limit_paise"]),
                "available_credit": money(row.get("available_credit_paise")),
                "outstanding": money(
                    row.get("card_outstanding_paise") or row.get("loan_outstanding_paise")
                ),
                "covered": {
                    "from": coverage_row["from"],
                    "to": coverage_row["to"],
                    "gaps": coverage_row["gaps"],
                },
            }
        )

    liquid_known = [
        row
        for row in account_rows
        if row["liquid"] and (row["balance_as_of"] or row["statement_balance_as_of"])
    ]
    liquid_total = (
        money(sum(int(row["balance_paise"]) for row in liquid_known))
        if len(liquid_known) == len([row for row in account_rows if row["liquid"]])
        else None
    )

    upcoming_rows = schedules_mod.upcoming(conn, days=30, start=day)["events"]
    upcoming: list[dict[str, Any]] = []
    for event in upcoming_rows:
        name = event["name"]
        schedule_id = event["schedule_id"]
        if schedule_id:
            schedule = conn.execute(
                "SELECT outside_text FROM schedules WHERE id = %s", (schedule_id,)
            ).fetchone()
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

    goal_rows = goals_mod.list_goals(conn)
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

    owed_rows = list(
        conn.execute(
            """
            SELECT v.payee_id, v.display_name, v.balance_paise, v.direction,
                   v.next_expected_date, p.outside_text
            FROM v_owed v
            JOIN payees p ON p.id = v.payee_id
            ORDER BY v.display_name
            """
        ).fetchall()
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
        for row in conn.execute(
            """
            SELECT id, body FROM preferences
            WHERE active AND deleted_at IS NULL
            ORDER BY created_at
            """
        ).fetchall()
    ]

    income_count = conn.execute(
        "SELECT count(*) AS n FROM schedules WHERE active AND kind LIKE 'income%'"
    ).fetchone()["n"]
    obligation_count = conn.execute(
        "SELECT count(*) AS n FROM schedules WHERE active AND kind IN ('obligation', 'instalment')"
    ).fetchone()["n"]
    baseline_count = conn.execute("SELECT count(*) AS n FROM baselines").fetchone()["n"]
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
    if any(
        row["balance_as_of"] is None and row["statement_balance_paise"] is None
        for row in account_rows
    ):
        setup_gaps.append("an account has no balance")

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
        "goals": goals,
        "owed": owed,
        "preferences": preferences,
        "setup_gaps": setup_gaps,
        "warnings": warnings,
        "source_ids": {
            "accounts": [row["id"] for row in account_rows],
            "schedules": [event["source_id"] for event in upcoming if event["source_id"]],
            "goals": [row["id"] for row in goal_rows],
            "payees": [row["payee_id"] for row in owed_rows],
        },
        "outside_text": outside,
    }


def _resolve_named(conn: psycopg.Connection, table: str, value: str | None) -> str | None:
    if value is None:
        return None
    if table == "accounts":
        row = conn.execute(
            "SELECT id FROM accounts WHERE lower(name) = lower(%s) AND active LIMIT 1", (value,)
        ).fetchone()
    elif table == "categories":
        row = conn.execute(
            "SELECT id FROM categories WHERE lower(name) = lower(%s) AND active LIMIT 1",
            (value,),
        ).fetchone()
    elif table == "payees":
        row = conn.execute(
            """
            SELECT id FROM payees WHERE active AND (lower(display_name) = lower(%s) OR upi_id = %s)
            ORDER BY display_name LIMIT 1
            """,
            (value, value),
        ).fetchone()
    else:  # pragma: no cover - internal misuse
        raise ValueError(table)
    if row is None:
        raise ValueError(f"No {table[:-1]} named {value!r}")
    return str(row["id"])


def query_movements(
    conn: psycopg.Connection,
    *,
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List individual movements with their transfers, labels, and evidence."""
    filters = filters or {}
    day = ledger_today()
    start = end = None
    if filters.get("period"):
        start, end = resolve_period(filters["period"], day)
    account_id = _resolve_named(conn, "accounts", filters.get("account"))
    category_id = _resolve_named(conn, "categories", filters.get("category"))
    payee_id = _resolve_named(conn, "payees", filters.get("payee"))

    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("m.date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("m.date <= %s")
        params.append(end)
    if filters.get("kind"):
        clauses.append("m.kind = %s")
        params.append(filters["kind"])
    if account_id:
        clauses.append(
            """
            EXISTS (
                SELECT 1 FROM pgledger_transfers t
                JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
                JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
                WHERE t.metadata ->> 'movement_id' = m.id
                  AND (fa.id = %s OR ta.id = %s)
            )
            """
        )
        params.extend([account_id, account_id])
    if category_id or payee_id or filters.get("text"):
        label_clauses = ["tl.movement_id = m.id"]
        if category_id:
            label_clauses.append("tl.category_id = %s")
            params.append(category_id)
        if payee_id:
            label_clauses.append("tl.payee_id = %s")
            params.append(payee_id)
        if filters.get("text"):
            text_clause = "(tl.note ILIKE %s OR m.description ILIKE %s)"
            pattern = f"%{filters['text']}%"
            label_clauses.append(text_clause)
            params.extend([pattern, pattern])
        clauses.append(
            f"EXISTS (SELECT 1 FROM transfer_labels tl WHERE {' AND '.join(label_clauses)})"
        )
    if filters.get("min_paise") is not None or filters.get("max_paise") is not None:
        inner: list[str] = []
        if filters.get("min_paise") is not None:
            inner.append("amount >= %s")
            params.append(filters["min_paise"])
        if filters.get("max_paise") is not None:
            inner.append("amount <= %s")
            params.append(filters["max_paise"])
        clauses.append(
            "EXISTS (SELECT 1 FROM pgledger_transfers t WHERE t.metadata ->> 'movement_id' = m.id "
            f"AND {' AND '.join(inner)})"
        )
    if cursor:
        try:
            cursor_date, cursor_id = cursor.split("|", 1)
            clauses.append("(m.date, m.id) < (%s, %s)")
            params.extend([date.fromisoformat(cursor_date), cursor_id])
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid cursor") from exc
    clauses.append("m.kind <> 'reversal'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = int(
        conn.execute(
            f"SELECT count(*) AS n FROM movements m {where}",  # noqa: S608
            params,
        ).fetchone()["n"]
    )
    rows = list(
        conn.execute(
            f"""
            SELECT m.* FROM movements m {where}
            ORDER BY m.date DESC, m.id DESC
            LIMIT %s
            """,  # noqa: S608
            (*params, limit + 1),
        ).fetchall()
    )
    states = movements_mod._reversal_state(conn, [str(row["id"]) for row in rows])  # noqa: SLF001
    visible = [row for row in rows if not states[str(row["id"])]["reversed"]]
    has_more = len(visible) > limit
    visible = visible[:limit]

    outside = False
    items: list[dict[str, Any]] = []
    for row in visible:
        payload = movements_mod.movement_payload(conn, row, state=states[str(row["id"])])
        transfers: list[dict[str, Any]] = []
        for transfer in payload["transfers"]:
            if _payee_is_outside(conn, transfer.get("payee_id")):
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
        if any(
            _payee_is_outside(conn, transfer.get("payee_id")) for transfer in payload["transfers"]
        ):
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
        "coverage": coverage(
            conn,
            account_id=account_id,
            start=start or day - timedelta(days=366),
            end=end or day,
        ),
        "outside_text": outside,
    }


def summarize_spending(
    conn: psycopg.Connection,
    *,
    group_by: str = "category",
    period: str = "this_month",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Net spend (refunds offset purchases), grouped by category, payee, account, month, or week."""
    if group_by not in {"category", "payee", "account", "month", "week", "none"}:
        raise ValueError("group_by is category, payee, account, month, week, or none")
    filters = filters or {}
    day = ledger_today()
    start, end = resolve_period(period, day)
    account_id = _resolve_named(conn, "accounts", filters.get("account"))
    category_id = _resolve_named(conn, "categories", filters.get("category"))
    payee_id = _resolve_named(conn, "payees", filters.get("payee"))

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

    params: list[Any] = [start, end]
    clauses = [
        "t.metadata ->> 'movement_id' = m.id",
        "(t.from_account_id = sp.ledger_account_id OR t.to_account_id = sp.ledger_account_id)",
        "m.date BETWEEN %s AND %s",
    ]
    if account_id:
        clauses.append("(fa.id = %s OR ta.id = %s)")
        params.extend([account_id, account_id])
    if category_id:
        clauses.append("tl.category_id = %s")
        params.append(category_id)
    if payee_id:
        clauses.append("tl.payee_id = %s")
        params.append(payee_id)
    if filters.get("text"):
        clauses.append("(tl.note ILIKE %s OR m.description ILIKE %s)")
        pattern = f"%{filters['text']}%"
        params.extend([pattern, pattern])

    rows = list(
        conn.execute(
            f"""
            SELECT {key_sql} AS key,
                   sum(
                       CASE WHEN t.to_account_id = sp.ledger_account_id
                            THEN t.amount ELSE -t.amount END
                   )::bigint AS amount_paise,
                   count(*)::bigint AS n,
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
            params,
        ).fetchall()
    )
    outside = False
    groups: list[dict[str, Any]] = []
    for row in rows:
        key = row["key"]
        if group_by == "payee" and _payee_is_outside(conn, row.get("payee_id")):
            key = mark(key)
            outside = True
        groups.append(
            {
                "key": key,
                "amount": money(int(row["amount_paise"])),
                "count": int(row["n"]),
            }
        )
    cov = coverage(conn, account_id=account_id, start=start, end=end)
    total_paise = sum(int(row["amount_paise"]) for row in rows)
    total = money(total_paise) if rows or cov["complete"] else None
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "groups": groups,
        "total": total,
        "coverage": cov,
        "outside_text": outside,
    }


def run_query(sql: str, *, deadline_seconds: float = 2.0) -> dict[str, Any]:
    """Run one read-only statement as `agent_ro`, always rolled back, always marked."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise ValueError("No SQL was given")
    conn = connect(role="agent_ro")
    timer = threading.Timer(deadline_seconds, conn.cancel)
    timer.daemon = True
    timer.start()
    try:
        conn.execute("SET LOCAL statement_timeout = '2s'")
        with conn.cursor() as cursor:
            cursor.execute(text, prepare=True)
            if cursor.description is None:
                raise ValueError("run_query needs a statement that returns rows")
            columns = [column.name for column in cursor.description]
            raw_rows = cursor.fetchmany(1001)
        truncated = len(raw_rows) > 1000
        rows = raw_rows[:1000]
        converted: list[list[Any]] = []
        for row in rows:
            if isinstance(row, dict):
                values_in = [row.get(column) for column in columns]
            else:
                values_in = list(row)
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
    finally:
        timer.cancel()
        conn.rollback()
        conn.close()
    return {
        "columns": columns,
        "rows": converted,
        "row_count": len(converted),
        "truncated": truncated,
        "sql": text,
        "outside_text": True,
    }


def active_preferences(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, body, created_by, active, created_at, updated_at
            FROM preferences WHERE deleted_at IS NULL ORDER BY created_at
            """
        ).fetchall()
    ]


def list_categories(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in categories_mod.list_categories(conn)]


def list_payees(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in payees_mod.list_payees(conn)]


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
