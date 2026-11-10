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

from arno.db.conn import Connection, connect
from arno.engine.money import money
from arno.engine.projection import CardAccount, card_due_date, statement_close
from arno.jobs.schedules import INDIA
from arno.ledger import accounts as accounts_mod
from arno.ledger import categories as categories_mod
from arno.ledger import goals as goals_mod
from arno.ledger import movements as movements_mod
from arno.ledger import payees as payees_mod
from arno.ledger import schedules as schedules_mod
from arno.ledger import settings as settings_mod
from arno.ledger.common import balance_anchor, has_known_balance
from arno.ledger.common import today as ledger_today
from arno.scheduled_questions import list_questions as list_scheduled_questions
from arno.tools.mark import CLOSE, OPEN, mark
from arno.tools.periods import resolve_period


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


async def coverage(
    conn: Connection,
    *,
    account_id: str | None,
    start: date,
    end: date,
    today: date | None = None,
) -> dict[str, Any]:
    """Which months have a committed statement. A missing month is a gap, never zero.

    Days after `today` are not a gap: the ledger cannot hold them, so no statement can cover
    them. A month that has not started is unknown, never complete. Without an account filter,
    every active account that has ever committed a statement must cover the month: one
    account's statement must not make another account's spending look known. An account that
    never imports statements (cash, a loan) never holds completeness back.
    """
    day = today or ledger_today()
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
          AND undone_at IS NULL
          AND period_start IS NOT NULL AND period_end IS NOT NULL
          AND period_end >= $1 AND period_start <= $2
          {account_clause}
        """,  # noqa: S608
        *params,
    )
    intervals: dict[str, list[tuple[date, date]]] = {}
    for row in rows:
        intervals.setdefault(str(row["account_id"]), []).append(
            (row["period_start"], row["period_end"])
        )
    merged = {account: _merge(found) for account, found in intervals.items()}
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
    gaps: list[str] = []
    for key in _month_keys(start, end):
        year, month = (int(part) for part in key.split("-"))
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        # A month counts as covered only for the part of it that was asked about, and only when
        # one statement (or adjacent statements) covers that whole part. A statement for the
        # first five days must not make the rest of the month look known.
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
    return {
        "complete": not gaps,
        "gaps": gaps,
        "from": covered_from.isoformat() if covered_from else None,
        "to": covered_to.isoformat() if covered_to else None,
    }


async def _account_coverage(
    conn: Connection, account_id: str, start: date, end: date, *, today: date | None = None
) -> dict[str, Any]:
    return await coverage(conn, account_id=account_id, start=start, end=end, today=today)


async def _payee_is_outside(conn: Connection, payee_id: str | None) -> bool:
    if not payee_id:
        return False
    row = await conn.fetchrow("SELECT outside_text FROM payees WHERE id = $1", payee_id)
    return bool(row and row["outside_text"])


async def get_financial_context(conn: Connection) -> dict[str, Any]:
    """One comprehensive snapshot: settings, balances and their ages, the next 30 days, schedules
    with their next dates, goals, owed, preferences, scheduled questions, open imports, and setup
    gaps."""
    day = ledger_today()
    settings = await settings_mod.read_settings(conn)
    account_rows = await accounts_mod.list_accounts(conn)
    outside = False
    accounts: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in account_rows:
        known = has_known_balance(row)
        # The balance is verified as of the later of its last movement and its stated balance:
        # a stated balance that matches the ledger adds no movement, and reporting the older
        # date would make a fresh check look stale. One definition, in `ledger.common`.
        as_of = balance_anchor(row, row["balance_as_of"])
        balance = money(row["balance_paise"]) if known else None
        coverage_row = await _account_coverage(
            conn, row["id"], day - timedelta(days=366), day, today=day
        )
        # The age is days since that same anchor, computed once in `account_payload`, so the age
        # and the as_of date always agree; statement freshness is the coverage gap below, which
        # says what it actually means.
        age = row["balance_age_days"] if known else None
        if age is not None and age > 30:
            warnings.append(f"{row['name']} balance is {age} days old")
        # Only an account a bank or card issuer prints statements for can lack one; a person, a
        # goal, cash, or a loan is tracked from the ledger alone.
        if coverage_row["gaps"] and row["kind"] in {"bank", "card", "wallet"}:
            warnings.append(
                f"{row['name']} has no statement covering {len(coverage_row['gaps'])} month(s)"
            )
        if not known:
            warnings.append(f"{row['name']} has no stated balance")
        # A known zero is a real zero; only an absent card value falls through to the loan value.
        outstanding_paise = row.get("card_outstanding_paise")
        if outstanding_paise is None:
            outstanding_paise = row.get("loan_outstanding_paise")
        account = {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "liquid": row["liquid"],
            "active": row["active"],
            "known": known,
            "balance": balance,
            "as_of": as_of or None,
            "age_days": age,
            "credit_limit": money(row["credit_limit_paise"]),
            "statement_day": row["statement_day"],
            "due_day": row["due_day"],
            "available_credit": money(row.get("available_credit_paise")),
            "outstanding": money(outstanding_paise),
            "rate_bps": row["rate_bps"],
            "tenure_months": row["tenure_months"],
            "covered": {
                "from": coverage_row["from"],
                "to": coverage_row["to"],
                "gaps": coverage_row["gaps"],
            },
        }
        if row["kind"] == "card" and row["statement_day"] and row["due_day"]:
            # The engine's cycle rule, so a stated bill date cannot drift from the projection.
            terms = CardAccount(
                id=row["id"],
                name=row["name"],
                statement_day=row["statement_day"],
                due_day=row["due_day"],
                outstanding_paise=outstanding_paise or 0,
                credit_limit_paise=row["credit_limit_paise"] or 0,
            )
            account["next_statement_close"] = statement_close(terms, day).isoformat()
            account["next_bill_due"] = card_due_date(terms, day).isoformat()
        accounts.append(account)

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
        if event.get("outside_text"):
            name = mark(name) or name
            outside = True
        upcoming.append(
            {
                "date": event["date"],
                "weekday": event["weekday"],
                "name": name,
                "kind": event["kind"],
                "subtype": event["subtype"],
                "amount": money(event["amount_paise"]),
                # Why an amount is null: an unknown card bill says so in words the model quotes.
                "note": event.get("note"),
                "schedule_id": schedule_id,
                "payment_status": event["payment_status"],
                "paid": money(event["paid_paise"]),
                "remaining": money(event["remaining_paise"]),
                "payment_movements": [
                    {
                        "movement_id": payment["movement_id"],
                        "amount": money(payment["amount_paise"]),
                        "active": payment["active"],
                    }
                    for payment in event["payments"]
                ],
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
    for row in await conn.fetch("SELECT * FROM schedules WHERE active ORDER BY kind, name"):
        payload = await schedules_mod.schedule_payload(conn, row)
        name = payload["name"]
        if payload["outside_text"]:
            name = mark(name) or name
            outside = True
        # A schedule's future runs are the ledger's own expansion, not the 30-day event list:
        # `schedule_occurrences` only holds runs a payment was matched to. Each date carries its
        # weekday, computed in code, so the agent never works one out itself.
        expanded = await schedules_mod.expand_schedule(conn, row, day, day + timedelta(days=400))
        run_dates = [
            {"date": event["date"], "weekday": event["weekday"]}
            for event in expanded
            if event["remaining_paise"] != 0
        ][:4]
        schedules.append(
            {
                "id": payload["id"],
                "name": name,
                "kind": payload["kind"],
                "subtype": payload["subtype"],
                "amount": money(payload["amount_paise"]),
                "rule": payload["rule"],
                "next_date": payload["next_date"],
                # A run that is already fully paid is not still coming.
                "next_dates": run_dates,
                "confirmed": payload["confirmed"],
                "active": payload["active"],
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
        {"id": row["id"], "text": row["body"], "kind": row["kind"]}
        for row in await conn.fetch(
            """
            SELECT id, body, kind FROM preferences
            WHERE active AND deleted_at IS NULL
            ORDER BY created_at
            """
        )
    ]

    # A question the user can archive from chat: without this list, finding its id needs a raw
    # query, which flags the thread and puts a confirm card in front of a one-field change.
    scheduled_questions = [
        {
            "id": row["id"],
            "prompt": row["prompt"],
            "rule": row["rule"],
            "next_run": (
                datetime.fromisoformat(row["next_run_at"]).astimezone(INDIA).isoformat()
                if row["next_run_at"]
                else None
            ),
        }
        for row in await list_scheduled_questions(conn)
    ]

    # An import is a fact the agent must read, not recall: the card, its state, the account and
    # period it covers, and how many lines it holds. Nothing here is a parser's own words, so this
    # block can be quoted as the host's account of the import.
    open_imports = [
        {
            "id": row["id"],
            "status": row["status"],
            "account": row["account_name"],
            "period": (
                {
                    "start": row["period_start"].isoformat() if row["period_start"] else None,
                    "end": row["period_end"].isoformat() if row["period_end"] else None,
                }
                if row["period_start"] or row["period_end"]
                else None
            ),
            "lines": int(row["line_count"]),
            "counts": row["counts"] or {},
            "check": (row["check_result"] or {}).get("status"),
        }
        for row in await conn.fetch(
            """
            SELECT i.id, i.status, i.period_start, i.period_end, i.counts, i.check_result,
                   acc.name AS account_name,
                   (SELECT count(*) FROM statement_lines sl WHERE sl.import_id = i.id)
                       AS line_count
            FROM imports i
            LEFT JOIN accounts acc ON acc.id = i.account_id
            WHERE i.status NOT IN ('committed', 'rejected') AND i.undone_at IS NULL
            ORDER BY i.created_at DESC, i.id DESC
            """
        )
    ]

    income_count = (
        await conn.fetchrow_required(
            "SELECT count(*) AS n FROM schedules WHERE active AND kind LIKE 'income%'"
        )
    )["n"]
    obligation_count = (
        await conn.fetchrow_required(
            "SELECT count(*) AS n FROM schedules "
            "WHERE active AND kind IN ('obligation', 'instalment')"
        )
    )["n"]
    baseline_count = (await conn.fetchrow_required("SELECT count(*) AS n FROM baselines"))["n"]
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
        "scheduled_questions": scheduled_questions,
        "imports": open_imports,
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
    # A name the model read back from a payee group carries the outside-text markers; it is
    # still the same name, so strip them before matching.
    value = value.replace(OPEN, "").replace(CLOSE, "").strip()
    if table == "accounts":
        rows = await conn.fetch(
            "SELECT id, name FROM accounts WHERE lower(name) = lower($1) AND active ORDER BY id",
            value,
        )
    elif table == "categories":
        rows = await conn.fetch(
            "SELECT id, name FROM categories WHERE lower(name) = lower($1) AND active ORDER BY id",
            value,
        )
    elif table == "payees":
        rows = await conn.fetch(
            """
            SELECT id, display_name AS name FROM payees
            WHERE active AND (lower(display_name) = lower($1) OR lower(upi_id) = lower($2))
            ORDER BY display_name, id
            """,
            value,
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
    activity, so lists and counts leave both out. One definition, shared with the ledger."""
    return await movements_mod._effectively_reversed_ids(conn)  # noqa: SLF001


def _like(value: str) -> str:
    """A search is literal: `%`, `_`, and `\\` match themselves, never act as a wildcard."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
        'A single day or a range goes in period as "YYYY-MM-DD" or "YYYY-MM-DD..YYYY-MM-DD".',
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
    if category_id or payee_id:
        label_clauses = ["tl.movement_id = m.id"]
        if category_id:
            params.append(category_id)
            label_clauses.append(f"tl.category_id = ${len(params)}")
        if payee_id:
            params.append(payee_id)
            label_clauses.append(f"tl.payee_id = ${len(params)}")
        clauses.append(
            f"EXISTS (SELECT 1 FROM transfer_labels tl WHERE {' AND '.join(label_clauses)})"
        )
    if filters.get("text"):
        # A description lives on the movement, so it must match even when the row has no label
        # (a plain expense with no payee, category, or note). The note needs a label row.
        pattern = _like(filters["text"])
        params.extend([pattern, pattern])
        clauses.append(
            f"(m.description ILIKE ${len(params) - 1} OR EXISTS ("
            "SELECT 1 FROM transfer_labels tl "
            f"WHERE tl.movement_id = m.id AND tl.note ILIKE ${len(params)}))"
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
    if not filters.get("include_reversed"):
        # In SQL, not after LIMIT, so total_count, the page, and next_cursor agree.
        clauses.append("m.kind <> 'reversal'")
        hidden = await _reversed_ids(conn)
        if hidden:
            params.append(hidden)
            clauses.append(f"NOT (m.id = ANY(${len(params)}))")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    # The cursor narrows the page, never the total: every page reports the whole match count.
    total = int(
        (
            await conn.fetchrow_required(
                f"SELECT count(*) AS n FROM movements m {where}",  # noqa: S608
                *params,
            )
        )["n"]
    )
    page_where = where
    page_params = list(params)
    if cursor:
        try:
            cursor_date, cursor_id = cursor.split("|", 1)
            page_params.extend([date.fromisoformat(cursor_date), cursor_id])
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid cursor") from exc
        cursor_clause = f"(m.date, m.id) < (${len(page_params) - 1}, ${len(page_params)})"
        page_where = f"WHERE {' AND '.join([*clauses, cursor_clause])}"
    page_params.append(limit + 1)
    rows = await conn.fetch(
        f"""
        SELECT m.* FROM movements m {page_where}
        ORDER BY m.date DESC, m.id DESC
        LIMIT ${len(page_params)}
        """,  # noqa: S608
        *page_params,
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
        # A statement line or a photo is outside text whatever its payee: the description is
        # still the bank's words, not the user's, and it must be marked before the model reads it.
        from_source = any(kind in {"statement_line", "photo"} for kind in payload["evidence"])
        payee_outside = [
            await _payee_is_outside(conn, transfer.get("payee_id"))
            for transfer in payload["transfers"]
        ]
        transfers: list[dict[str, Any]] = []
        for transfer, hidden_text in zip(payload["transfers"], payee_outside, strict=False):
            if hidden_text:
                transfer["payee_name"] = mark(transfer["payee_name"])
            transfers.append(
                {
                    **transfer,
                    "from": transfer["from_name"],
                    "to": transfer["to_name"],
                    "amount": money(int(transfer["amount_paise"])),
                }
            )
        if from_source or any(payee_outside):
            payload["description"] = mark(payload["description"])
            outside = True
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
            today=day,
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
        'The period is its own argument: "this_month", "last_month", "this_week", "last_week", '
        '"2026-09", or "YYYY-MM-DD..YYYY-MM-DD".',
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
        pattern = _like(filters["text"])
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
        amount_paise = int(row["amount_paise"])
        count = int(row["n"])
        if amount_paise == 0 and count == 0:
            # A movement a live reversal cancelled leaves nothing to show. It still keeps the
            # period's total defined, but it must not appear as a ₹0 category.
            continue
        key = row["key"]
        if group_by == "payee" and await _payee_is_outside(conn, row.get("payee_id")):
            key = mark(key)
            outside = True
        groups.append(
            {
                "key": key,
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
        SELECT id, body, kind, created_by, active, created_at, updated_at
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
