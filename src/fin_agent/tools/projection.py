"""The `project` read tool: load the ledger into the engine and return a citable projection.

The engine itself is pure. This module is its only database seam: it reads balances as of a date,
expands schedules with the same holiday-aware rules the ledger uses, learns spend history and card
shares, and hands back source IDs for every underlying account, schedule, goal, and category.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import psycopg

from fin_agent.engine.affordability import Hypothetical, afford
from fin_agent.engine.money import money
from fin_agent.engine.projection import (
    DEFAULT_RUNS,
    DEFAULT_SEED,
    HISTORY_DAYS,
    MIN_REAL_DAYS,
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    SpendCategory,
)
from fin_agent.engine.projection import (
    project as run_projection,
)
from fin_agent.ledger import schedules as schedules_mod
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools.mark import mark


def _balance_at(conn: psycopg.Connection, ledger_account_id: str, as_of: date) -> int:
    row = conn.execute(
        """
        SELECT coalesce(sum(e.amount), 0)::bigint AS balance_paise
        FROM pgledger_entries e
        JOIN pgledger_transfers t ON t.id = e.transfer_id
        LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE e.account_id = %s
          AND coalesce(m.date, (t.event_at AT TIME ZONE 'Asia/Kolkata')::date) <= %s
        """,
        (ledger_account_id, as_of),
    ).fetchone()
    return int(row["balance_paise"]) if row else 0


def _last_activity(conn: psycopg.Connection, ledger_account_id: str, as_of: date) -> date | None:
    row = conn.execute(
        """
        SELECT max(coalesce(m.date, (t.event_at AT TIME ZONE 'Asia/Kolkata')::date)) AS as_of
        FROM pgledger_transfers t
        LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        WHERE (t.from_account_id = %s OR t.to_account_id = %s)
          AND coalesce(m.date, (t.event_at AT TIME ZONE 'Asia/Kolkata')::date) <= %s
        """,
        (ledger_account_id, ledger_account_id, as_of),
    ).fetchone()
    return row["as_of"] if row and row["as_of"] else None


def _settings(conn: psycopg.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    return dict(row) if row is not None else {"floor_paise": None, "default_horizon_days": 90}


def _horizon_for_loading(
    conn: psycopg.Connection, as_of: date, ask_horizon_days: int | None
) -> int:
    rows = conn.execute(
        "SELECT deadline FROM goals WHERE status = 'active' AND deadline IS NOT NULL"
    ).fetchall()
    furthest = max(((row["deadline"] - as_of).days for row in rows), default=0)
    return min(3_650, max(90, ask_horizon_days or 0, furthest))


def _card_rows(conn: psycopg.Connection, as_of: date) -> tuple[list[dict[str, Any]], list[str]]:
    rows = list(
        conn.execute(
            """
            SELECT * FROM accounts
            WHERE kind = 'card' AND active
            ORDER BY name
            """
        ).fetchall()
    )
    cards: list[dict[str, Any]] = []
    gaps: list[str] = []
    for row in rows:
        balance = _balance_at(conn, row["ledger_account_id"], as_of)
        outstanding = max(-balance, 0)
        cards.append(
            {
                **dict(row),
                "outstanding_paise": outstanding,
                "available_credit_paise": max((row["credit_limit_paise"] or 0) - outstanding, 0),
                "as_of": row["statement_balance_as_of"]
                or _last_activity(conn, row["ledger_account_id"], as_of),
            }
        )
        if row["statement_balance_as_of"] is None:
            gaps.append(f"{row['name']} has no statement date; its outstanding is from the ledger")
    return cards, gaps


def _liquid_rows(conn: psycopg.Connection, as_of: date) -> tuple[list[dict[str, Any]], list[str]]:
    rows = list(
        conn.execute(
            """
            SELECT * FROM accounts
            WHERE liquid AND active
            ORDER BY name
            """
        ).fetchall()
    )
    accounts: list[dict[str, Any]] = []
    gaps: list[str] = []
    for row in rows:
        balance = _balance_at(conn, row["ledger_account_id"], as_of)
        activity = _last_activity(conn, row["ledger_account_id"], as_of)
        stated = row["statement_balance_as_of"]
        as_of_date = stated if stated is not None and stated <= as_of else activity
        known = stated is not None or activity is not None
        age = (as_of - as_of_date).days if as_of_date is not None else None
        accounts.append(
            {
                **dict(row),
                "balance_paise": balance,
                "known": known,
                "as_of_date": as_of_date,
                "age_days": age,
            }
        )
        if not known:
            gaps.append(
                f"{row['name']} has no known balance as of {as_of.isoformat()}; it counts as zero"
            )
        elif age is not None and age > 30:
            gaps.append(f"{row['name']} balance is {age} days old as of {as_of.isoformat()}")
    return accounts, gaps


def _goals(conn: psycopg.Connection, as_of: date) -> list[dict[str, Any]]:
    rows = list(
        conn.execute(
            "SELECT * FROM goals WHERE status = 'active' ORDER BY priority, deadline NULLS LAST"
        ).fetchall()
    )
    goals: list[dict[str, Any]] = []
    for row in rows:
        account = conn.execute(
            "SELECT * FROM accounts WHERE id = %s", (row["account_id"],)
        ).fetchone()
        saved = _balance_at(conn, account["ledger_account_id"], as_of) if account else 0
        goals.append(
            {
                **dict(row),
                "saved_paise": max(saved, 0),
            }
        )
    return goals


def _spend_categories(
    conn: psycopg.Connection,
    *,
    as_of: date,
    covered_category_ids: set[str],
) -> tuple[list[SpendCategory], list[dict[str, Any]], list[str]]:
    window_start = as_of - timedelta(days=HISTORY_DAYS)
    window_end = as_of - timedelta(days=1)
    categories = list(
        conn.execute("SELECT * FROM categories WHERE active ORDER BY name").fetchall()
    )
    baselines = {
        row["category_id"]: row for row in conn.execute("SELECT * FROM baselines").fetchall()
    }
    spend_rows = conn.execute(
        """
        SELECT tl.category_id, v.date, sum(v.amount_paise)::bigint AS amount_paise
        FROM v_spending v
        JOIN transfer_labels tl ON tl.transfer_id = v.transfer_id
        WHERE v.date BETWEEN %s AND %s
          AND v.amount_paise > 0
          AND tl.category_id IS NOT NULL
        GROUP BY tl.category_id, v.date
        """,
        (window_start, window_end),
    ).fetchall()
    daily: dict[str, dict[date, int]] = {}
    for row in spend_rows:
        daily.setdefault(str(row["category_id"]), {})[row["date"]] = int(row["amount_paise"])

    gaps: list[str] = []
    uncategorized = conn.execute(
        """
        SELECT coalesce(sum(v.amount_paise), 0)::bigint AS amount_paise
        FROM v_spending v
        WHERE v.date BETWEEN %s AND %s
          AND v.amount_paise > 0
          AND v.category_id IS NULL
        """,
        (window_start, window_end),
    ).fetchone()
    if uncategorized and int(uncategorized["amount_paise"]) > 0:
        gaps.append(
            "uncategorized spending is not simulated as variable spend; label those rows in the "
            "ledger"
        )

    card_rows = conn.execute(
        """
        SELECT tl.category_id,
               CASE WHEN fa.kind = 'card' THEN fa.id ELSE ta.id END AS card_id,
               sum(v.amount_paise)::bigint AS amount_paise
        FROM v_spending v
        JOIN pgledger_transfers t ON t.id = v.transfer_id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        JOIN transfer_labels tl ON tl.transfer_id = v.transfer_id
        WHERE v.date BETWEEN %s AND %s
          AND v.amount_paise > 0
          AND tl.category_id IS NOT NULL
          AND (fa.kind = 'card' OR ta.kind = 'card')
        GROUP BY tl.category_id, 2
        """,
        (window_start, window_end),
    ).fetchall()
    card_choice: dict[str, tuple[str, int]] = {}
    card_counts: dict[str, int] = {}
    for row in card_rows:
        category_id = str(row["category_id"])
        amount = int(row["amount_paise"])
        card_counts[category_id] = card_counts.get(category_id, 0) + 1
        current = card_choice.get(category_id)
        if current is None or amount > current[1]:
            card_choice[category_id] = (str(row["card_id"]), amount)

    result: list[SpendCategory] = []
    baseline_rows: list[dict[str, Any]] = []
    days = [window_start + timedelta(days=offset) for offset in range(HISTORY_DAYS)]
    for category in categories:
        category_id = str(category["id"])
        if category_id in covered_category_ids:
            gaps.append(
                f"{category['name']} is covered by an obligation and is not simulated as "
                "variable spend"
            )
            continue
        by_day = daily.get(category_id, {})
        real_days = sum(1 for amount in by_day.values() if amount > 0)
        day_label = f"{real_days} real spending day" + ("" if real_days == 1 else "s")
        baseline = baselines.get(category_id)
        if real_days < MIN_REAL_DAYS and baseline is None:
            if by_day:
                gaps.append(
                    f"{category['name']} has only {day_label} and no setup "
                    "estimate, so it is left out"
                )
            continue
        if baseline is not None:
            baseline_per_day = int(baseline["amount_per_day_paise"])
            source = str(baseline["source"])
        else:
            baseline_per_day = round(sum(by_day.values()) / HISTORY_DAYS) if by_day else 0
            source = "computed"
        history = tuple((day, max(0, int(by_day.get(day, 0)))) for day in days)
        total_spend = sum(by_day.values())
        chosen = card_choice.get(category_id)
        share = round(chosen[1] / total_spend, 4) if chosen is not None and total_spend > 0 else 0.0
        card_id = chosen[0] if chosen is not None and share > 0 else None
        if card_counts.get(category_id, 0) > 1:
            gaps.append(
                f"{category['name']} was charged to more than one card; its variable spend uses "
                "the card with the largest share"
            )
        if real_days < MIN_REAL_DAYS:
            gaps.append(
                f"{category['name']} has only {day_label}; its "
                f"{money(baseline_per_day)['text']}/day {source} estimate is used flat"
            )
        elif share == 0:
            gaps.append(
                f"{category['name']} has no card history; its variable spend is treated as cash"
            )
        result.append(
            SpendCategory(
                id=category_id,
                name=str(category["name"]),
                baseline_per_day_paise=baseline_per_day,
                baseline_source=source,
                reducible=bool(category["reducible"]),
                card_share=share,
                card_id=card_id,
                history=history,
                covered_by_obligation=False,
            )
        )
        baseline_rows.append(
            {
                "category": category["name"],
                "id": category_id,
                "source": source,
                "amount_per_day_paise": baseline_per_day,
                "real_days": real_days,
                "card_share": share,
            }
        )
    return result, baseline_rows, gaps


def _owed_events(
    conn: psycopg.Connection, *, as_of: date, end: date
) -> tuple[list[CashEvent], list[dict[str, Any]], list[str]]:
    rows = conn.execute(
        """
        SELECT p.id AS payee_id, p.display_name, p.outside_text, tl.expected_date,
               t.amount::bigint AS amount_paise,
               CASE WHEN t.to_account_id = a.ledger_account_id THEN 1 ELSE -1 END AS direction
        FROM transfer_labels tl
        JOIN pgledger_transfers t ON t.id = tl.transfer_id
        JOIN accounts a ON a.kind = 'person'
        JOIN payees p ON p.person_account_id = a.id
        WHERE tl.expected_date IS NOT NULL
          AND (t.from_account_id = a.ledger_account_id OR t.to_account_id = a.ledger_account_id)
        ORDER BY tl.expected_date, p.display_name
        """
    ).fetchall()
    events: list[CashEvent] = []
    sources: list[dict[str, Any]] = []
    gaps: list[str] = []
    for row in rows:
        expected = row["expected_date"]
        if expected > end:
            continue
        when = expected if expected > as_of else as_of + timedelta(days=1)
        if expected <= as_of:
            gaps.append(
                f"{row['display_name']} expected {expected.isoformat()}; it is counted on "
                f"{when.isoformat()} as still owed"
            )
        amount = int(row["amount_paise"])
        signed = amount if int(row["direction"]) > 0 else -amount
        kind = "owed_receipt" if signed > 0 else "owed_payment"
        display_name = mark(row["display_name"]) if row["outside_text"] else row["display_name"]
        events.append(
            CashEvent(
                date=when,
                amount_paise=signed,
                name=f"{display_name} {'repayment' if signed > 0 else 'payment'}",
                kind=kind,
                source_id=str(row["payee_id"]),
            )
        )
        sources.append(
            {
                "kind": "payee",
                "id": row["payee_id"],
                "name": display_name,
                "detail": f"{kind.replace('_', ' ')} on {when.isoformat()}",
            }
        )
    return events, sources, gaps


def load_inputs(
    conn: psycopg.Connection,
    *,
    as_of: date | None = None,
    horizon_days: int | None = None,
) -> ProjectionInput:
    """Build the pure engine's inputs from the ledger as of ``as_of``."""
    as_of = as_of or ledger_today()
    settings = _settings(conn)
    floor = int(settings["floor_paise"] or 0)
    end = as_of + timedelta(days=_horizon_for_loading(conn, as_of, horizon_days))

    liquid_rows, gaps = _liquid_rows(conn, as_of)
    card_rows, card_gaps = _card_rows(conn, as_of)
    gaps.extend(card_gaps)

    goal_rows = _goals(conn, as_of)
    goal_by_schedule = {
        row["contribution_schedule_id"]: row for row in goal_rows if row["contribution_schedule_id"]
    }
    card_by_account = {row["id"]: row for row in card_rows}

    events: list[CashEvent] = []
    schedule_sources: dict[str, dict[str, Any]] = {}
    covered_categories: set[str] = set()
    for row in conn.execute("SELECT * FROM schedules WHERE active ORDER BY name").fetchall():
        if row["starts"] > end:
            continue
        if row["ends"] is not None and row["ends"] < as_of:
            continue
        amount = row["amount_paise"]
        if not amount:
            continue
        if row["kind"] in {"obligation", "instalment"} and row["category_id"]:
            covered_categories.add(str(row["category_id"]))
        occurrences = schedules_mod.expand_schedule(conn, row, as_of + timedelta(days=1), end)
        if not occurrences:
            continue
        schedule_sources[str(row["id"])] = {
            "kind": "schedule",
            "id": row["id"],
            "name": mark(row["name"]) if row["outside_text"] else row["name"],
            "detail": f"{row['kind']} · {row['rule']}",
            "amount_paise": int(amount),
        }
        display_name = mark(row["name"]) if row["outside_text"] else row["name"]
        for occurrence in occurrences:
            when = date.fromisoformat(occurrence["date"])
            kind = row["kind"]
            if kind.startswith("income"):
                events.append(
                    CashEvent(
                        date=when,
                        amount_paise=int(amount),
                        name=display_name,
                        kind=kind,
                        source_id=str(row["id"]),
                        confirmed=bool(row["confirmed"]),
                    )
                )
            elif kind in {"obligation", "instalment"}:
                card = card_by_account.get(row["account_id"])
                events.append(
                    CashEvent(
                        date=when,
                        amount_paise=-int(amount),
                        name=display_name,
                        kind=kind,
                        source_id=str(row["id"]),
                        card_id=card["id"] if card else None,
                    )
                )
            elif kind == "goal_contribution":
                goal = goal_by_schedule.get(row["id"])
                events.append(
                    CashEvent(
                        date=when,
                        amount_paise=-int(amount),
                        name=display_name,
                        kind=kind,
                        source_id=str(row["id"]),
                        goal_id=goal["id"] if goal else None,
                    )
                )
                if goal is None:
                    gaps.append(
                        f"{row['name']} is not linked to a goal; it still leaves liquid cash"
                    )

    owed_events, owed_sources, owed_gaps = _owed_events(conn, as_of=as_of, end=end)
    events.extend(owed_events)
    gaps.extend(owed_gaps)

    categories, baseline_rows, category_gaps = _spend_categories(
        conn, as_of=as_of, covered_category_ids=covered_categories
    )
    gaps.extend(category_gaps)

    income_count = sum(1 for event in events if event.kind.startswith("income") and event.confirmed)
    obligation_count = sum(1 for event in events if event.kind in {"obligation", "instalment"})
    setup_gaps: list[str] = []
    if not liquid_rows:
        setup_gaps.append("no liquid account")
    if floor <= 0:
        setup_gaps.append("no floor set; the projection uses zero")
    if not income_count:
        setup_gaps.append("no confirmed income schedule")
    if not obligation_count:
        setup_gaps.append("no obligations")
    if not goal_rows:
        setup_gaps.append("no active goals")
    if not baseline_rows:
        setup_gaps.append("no spending baselines or history")
    if not any(row["known"] for row in liquid_rows):
        setup_gaps.append("no liquid account has a known balance")

    liquid = tuple(
        LiquidAccount(
            id=row["id"],
            name=row["name"],
            balance_paise=int(row["balance_paise"]),
            as_of=row["as_of_date"],
            statement_age_days=row["age_days"],
            kind=row["kind"],
        )
        for row in liquid_rows
    )
    cards = tuple(
        CardAccount(
            id=row["id"],
            name=row["name"],
            statement_day=int(row["statement_day"]),
            due_day=int(row["due_day"]),
            outstanding_paise=int(row["outstanding_paise"]),
            credit_limit_paise=int(row["credit_limit_paise"] or 0),
        )
        for row in card_rows
    )
    goals = tuple(
        GoalPlan(
            id=row["id"],
            name=row["name"],
            target_paise=int(row["target_paise"]),
            saved_paise=int(row["saved_paise"]),
            deadline=row["deadline"],
            contribution_schedule_id=row["contribution_schedule_id"],
            priority=int(row["priority"]),
        )
        for row in goal_rows
    )

    balance_ages = [
        {
            "account": row["name"],
            "id": row["id"],
            "kind": row["kind"],
            "as_of": row["as_of_date"].isoformat() if row["as_of_date"] else None,
            "age_days": row["age_days"],
            "balance_paise": int(row["balance_paise"]),
            "known": row["known"],
        }
        for row in liquid_rows
    ] + [
        {
            "account": row["name"],
            "id": row["id"],
            "kind": "card",
            "as_of": row["as_of"].isoformat() if row["as_of"] else None,
            "age_days": (as_of - row["as_of"]).days if row["as_of"] else None,
            "balance_paise": -int(row["outstanding_paise"]),
            "known": row["as_of"] is not None,
        }
        for row in card_rows
    ]

    sources: list[dict[str, Any]] = []
    for row in liquid_rows:
        sources.append(
            {
                "kind": "account",
                "id": row["id"],
                "name": row["name"],
                "detail": (
                    f"liquid · balance as of {row['as_of_date'].isoformat()}"
                    if row["as_of_date"]
                    else "liquid · balance unknown"
                ),
                "amount_paise": int(row["balance_paise"]),
            }
        )
    for row in card_rows:
        sources.append(
            {
                "kind": "card",
                "id": row["id"],
                "name": row["name"],
                "detail": (
                    f"statement day {row['statement_day']} · due day {row['due_day']} · "
                    f"outstanding {row['outstanding_paise']} paise"
                ),
                "amount_paise": int(row["outstanding_paise"]),
            }
        )
    sources.extend(schedule_sources.values())
    for row in goal_rows:
        sources.append(
            {
                "kind": "goal",
                "id": row["id"],
                "name": row["name"],
                "detail": (
                    f"saved {row['saved_paise']} of {row['target_paise']} paise"
                    + (f" · due {row['deadline'].isoformat()}" if row["deadline"] else "")
                ),
                "amount_paise": int(row["target_paise"]),
            }
        )
    for row in baseline_rows:
        days = int(row["real_days"])
        origin = (
            "from your history" if row["source"] == "computed" else "from your setup estimate"
        )
        detail = f"{money(int(row['amount_per_day_paise']))['text']}/day {origin}"
        if days:
            detail += f" · {days} real day" + ("" if days == 1 else "s")
        sources.append(
            {
                "kind": "category",
                "id": row["id"],
                "name": row["category"],
                "detail": detail,
                "amount_paise": int(row["amount_per_day_paise"]),
            }
        )
    sources.extend(owed_sources)
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for source in sources:
        unique[(source["kind"], source["id"])] = source
    outside = any(
        row["outside_text"]
        for row in conn.execute("SELECT outside_text FROM schedules WHERE active")
    ) or any(row["outside_text"] for row in conn.execute("SELECT outside_text FROM payees"))

    return ProjectionInput(
        as_of=as_of,
        floor_paise=floor,
        liquid=liquid,
        events=tuple(sorted(events, key=lambda event: (event.date, event.name))),
        cards=cards,
        goals=goals,
        categories=tuple(categories),
        ask_horizon_days=horizon_days,
        data_quality={
            "balance_ages": balance_ages,
            "baseline_sources": baseline_rows,
            "gaps": gaps,
            "setup_gaps": setup_gaps,
            "outside_text": outside,
        },
        sources=tuple(unique.values()),
    )


def _hypothetical(payload: dict[str, Any] | Hypothetical | None) -> Hypothetical | None:
    if payload is None or isinstance(payload, Hypothetical):
        return payload
    unknown = set(payload) - {"amount_paise", "date", "method", "emi_months", "account"}
    if unknown:
        raise ValueError(
            f"hypothetical takes amount_paise, date, method, emi_months, and account, not "
            f"{', '.join(sorted(unknown))}"
        )
    if "amount_paise" not in payload or "date" not in payload:
        raise ValueError("a hypothetical needs amount_paise and date")
    return Hypothetical(
        amount_paise=int(payload["amount_paise"]),
        date=(
            ledger_today()
            if payload["date"] == "today"
            else payload["date"]
            if isinstance(payload["date"], date)
            else date.fromisoformat(str(payload["date"]))
        ),
        method=str(payload.get("method") or "account"),
        emi_months=int(payload["emi_months"]) if payload.get("emi_months") else None,
        account=payload.get("account"),
    )


def project(
    conn: psycopg.Connection,
    *,
    horizon_days: int | None = None,
    hypothetical: dict[str, Any] | Hypothetical | None = None,
    as_of: date | None = None,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """The full projection or affordability result, including the daily curve and sources."""
    purchase = _hypothetical(hypothetical)
    load_horizon = horizon_days
    if purchase is not None:
        needed = max(0, (purchase.date - (as_of or ledger_today())).days + 1)
        load_horizon = max(load_horizon or 0, needed)
    inputs = load_inputs(conn, as_of=as_of, horizon_days=load_horizon)
    if purchase is not None:
        return afford(inputs, purchase, runs=runs, seed=seed)
    return run_projection(inputs, runs=runs, seed=seed)


def _money_low(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {"date": row["date"], "amount": money(int(row["paise"]))}


def summary(result: dict[str, Any]) -> dict[str, Any]:
    """The compact model-facing payload: numbers, citations, and data quality, no curve."""
    payload: dict[str, Any] = {
        "as_of": result["as_of"],
        "horizon_days": result["horizon_days"],
        "floor": money(result["floor_paise"]),
        "floor_set": int(result["floor_paise"] or 0) > 0,
        "floor_probability": result["floor_probability"],
        "floor_threshold": result["floor_threshold"],
        "median_low": _money_low(result["median_low"]),
        "p10_low": money(result["p10_low_paise"]),
        "first_floor_crossing": result["first_floor_crossing"],
        "goals": [
            {
                **goal,
                "target": money(goal["target_paise"]),
                "saved": money(goal["saved_paise"]),
                "remaining": money(goal["remaining_paise"]),
            }
            for goal in result["goals"]
        ],
        "obligations_met": result["obligations_met"],
        "data_quality": result["data_quality"],
        "source_ids": result["source_ids"],
        "outside_text": bool(result["data_quality"].get("outside_text")),
        "optimistic": (
            {
                "floor_probability": result["optimistic"]["floor_probability"],
                "median_low": _money_low(
                    {
                        "date": result["optimistic"]["median_low"]["date"],
                        "paise": result["optimistic"]["median_low"]["paise"],
                    }
                ),
                "income": result["optimistic"]["income"],
            }
            if result.get("optimistic")
            else None
        ),
    }
    if result.get("verdict"):
        payload["verdict"] = result["verdict"]
        payload["verdict_reason"] = result["verdict_reason"]
        payload["tradeoffs"] = result["tradeoffs"]
        payload["goal_slips"] = result.get("goal_slips", [])
        payload["hypothetical"] = result["hypothetical"]
        payload["baseline"] = result.get("baseline")
    return payload


def card(result: dict[str, Any]) -> dict[str, Any]:
    """The UI metadata card: the full curve, sources, verdict, and every citation."""
    payload: dict[str, Any] = {
        "card": "projection",
        "title": "Projection",
        "as_of": result["as_of"],
        "horizon_days": result["horizon_days"],
        "end": result["end"],
        "floor": money(result["floor_paise"]),
        "floor_set": int(result["floor_paise"] or 0) > 0,
        "floor_probability": result["floor_probability"],
        "floor_threshold": result["floor_threshold"],
        "median_low": _money_low(result["median_low"]),
        "p10_low": money(result["p10_low_paise"]),
        "first_floor_crossing": result["first_floor_crossing"],
        "curve": result["curve"],
        "goals": [
            {
                **goal,
                "target": money(goal["target_paise"]),
                "saved": money(goal["saved_paise"]),
                "remaining": money(goal["remaining_paise"]),
            }
            for goal in result["goals"]
        ],
        "obligations_met": result["obligations_met"],
        "data_quality": result["data_quality"],
        "sources": result["sources"],
        "source_ids": result["source_ids"],
        "optimistic": result.get("optimistic"),
        "runs": result["runs"],
        "seed": result["seed"],
    }
    if result.get("verdict"):
        payload.update(
            {
                "verdict": result["verdict"],
                "verdict_reason": result["verdict_reason"],
                "tradeoffs": result["tradeoffs"],
                "goal_slips": result.get("goal_slips", []),
                "hypothetical": result["hypothetical"],
                "baseline": result.get("baseline"),
            }
        )
    return payload


__all__ = ["card", "load_inputs", "project", "summary"]
