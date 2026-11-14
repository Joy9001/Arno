"""The `project` read tool: load the ledger into the engine and return a citable projection.

The engine itself is pure. This module is its only database seam: it reads balances as of a date,
expands schedules with the same holiday-aware rules the ledger uses, learns spend history and card
shares, and hands back source IDs for every underlying account, schedule, goal, and category.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

from arno.db.conn import Connection
from arno.engine.affordability import Hypothetical, afford
from arno.engine.inputs import (
    CategoryRow,
    ConfiguredSchedule,
    LedgerSnapshot,
    Occurrence,
    SpendRow,
    build_inputs,
)
from arno.engine.money import _add_months, money
from arno.engine.projection import (
    DEFAULT_RUNS,
    DEFAULT_SEED,
    HISTORY_DAYS,
    BalanceAge,
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    MedianLow,
    Projection,
    ProjectionInput,
    SourceRef,
)
from arno.engine.projection import (
    project as run_projection,
)
from arno.ledger import schedules as schedules_mod
from arno.ledger.common import balance_anchor, has_known_balance
from arno.ledger.common import today as ledger_today
from arno.ledger.settings import read_settings
from arno.tools.mark import mark


async def _balances_at(
    conn: Connection, ledger_account_ids: list[str], as_of: date
) -> dict[str, int]:
    if not ledger_account_ids:
        return {}
    # Routing `unnest($1::text[])` through a subquery creates an InitPlan so Postgres caches a
    # generic plan while still using the `pgledger_entries (account_id)` index.
    rows = await conn.fetch(
        """
        SELECT e.account_id, coalesce(sum(e.amount), 0)::bigint AS balance_paise
        FROM pgledger_entries e
        JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        WHERE e.account_id = ANY(ARRAY(SELECT unnest($1::text[])))
          AND m.date <= $2
        GROUP BY e.account_id
        """,
        ledger_account_ids,
        as_of,
    )
    by_id = {str(row["account_id"]): int(row["balance_paise"]) for row in rows}
    return {account_id: by_id.get(account_id, 0) for account_id in ledger_account_ids}


async def _balance_at(conn: Connection, ledger_account_id: str, as_of: date) -> int:
    return (await _balances_at(conn, [ledger_account_id], as_of))[ledger_account_id]


async def _horizon_for_loading(
    conn: Connection, as_of: date, ask_horizon_days: int | None, *, known_then: bool
) -> int:
    rows = await conn.fetch(
        """
        SELECT deadline FROM goals
        WHERE status = 'active' AND deadline IS NOT NULL
          AND (NOT $2 OR (created_at AT TIME ZONE 'Asia/Kolkata')::date <= $1)
        """,
        as_of,
        known_then,
    )
    furthest = max(((row["deadline"] - as_of).days for row in rows), default=0)
    return min(3_650, max(90, ask_horizon_days or 0, furthest))


async def _last_movements(
    conn: Connection, ledger_account_ids: list[str], as_of: date
) -> dict[str, date | None]:
    """The date of each account's last movement on or before ``as_of``."""
    if not ledger_account_ids:
        return {}
    # Same `ARRAY(SELECT unnest(...))` generic-plan idiom as `_balances_at`.
    rows = await conn.fetch(
        """
        SELECT e.account_id, max(m.date) AS last_date
        FROM pgledger_entries e
        JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
        JOIN movements m ON m.id = ml.movement_id
        WHERE e.account_id = ANY(ARRAY(SELECT unnest($1::text[])))
          AND m.date <= $2
        GROUP BY e.account_id
        """,
        ledger_account_ids,
        as_of,
    )
    by_id = {str(row["account_id"]): row["last_date"] for row in rows}
    return {account_id: by_id.get(account_id) for account_id in ledger_account_ids}


async def _last_movement(conn: Connection, ledger_account_id: str, as_of: date) -> date | None:
    """The date of the account's last movement on or before ``as_of``."""
    return (await _last_movements(conn, [ledger_account_id], as_of))[ledger_account_id]


# An account is known on a day when its row existed then, or when the ledger already held an entry
# for it: a backfill is imported after the history it covers, so `created_at` alone hid a
# backfilled account from the whole backtest.
_KNOWN_ON_DAY = """
          AND (NOT $2 OR (created_at AT TIME ZONE 'Asia/Kolkata')::date <= $1
               OR EXISTS (
                   SELECT 1 FROM pgledger_entries e
                   JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
                   JOIN movements m ON m.id = ml.movement_id
                   WHERE e.account_id = v_accounts.ledger_account_id
                     AND m.date <= $1
               ))
"""


async def _card_rows(
    conn: Connection, as_of: date, *, known_then: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = await conn.fetch(
        f"""
        SELECT * FROM v_accounts
        WHERE kind = 'card' AND active{_KNOWN_ON_DAY}
        ORDER BY name
        """,  # noqa: S608 - the fragment is a constant of this module
        as_of,
        known_then,
    )
    ledger_ids = [str(row["ledger_account_id"]) for row in rows]
    balances = await _balances_at(conn, ledger_ids, as_of)
    last_dates = await _last_movements(conn, ledger_ids, as_of)
    cards: list[dict[str, Any]] = []
    gaps: list[str] = []
    for row in rows:
        ledger_id = str(row["ledger_account_id"])
        balance = balances[ledger_id]
        outstanding = max(-balance, 0)
        stated = row["statement_balance_as_of"]
        known = has_known_balance(row) and stated is not None and stated <= as_of
        anchor = balance_anchor(row, last_dates[ledger_id])
        cards.append(
            {
                **dict(row),
                "outstanding_paise": outstanding,
                # A credit balance (an overpayment) is usable, the same rule as the account
                # payload; an unknown card claims no credit at all.
                "credit_paise": max(balance, 0) if known else 0,
                "available_credit_paise": (
                    max((row["credit_limit_paise"] or 0) + balance, 0) if known else None
                ),
                "as_of": anchor if known else None,
                "known": known,
            }
        )
        if not known:
            gaps.append(
                f"{row['name']} has no statement balance as of {as_of.isoformat()}; "
                "its outstanding is from the ledger"
            )
    return cards, gaps


async def _liquid_rows(
    conn: Connection, as_of: date, *, known_then: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = await conn.fetch(
        f"""
        SELECT * FROM v_accounts
        WHERE liquid AND active{_KNOWN_ON_DAY}
        ORDER BY name
        """,  # noqa: S608 - the fragment is a constant of this module
        as_of,
        known_then,
    )
    ledger_ids = [str(row["ledger_account_id"]) for row in rows]
    balances = await _balances_at(conn, ledger_ids, as_of)
    last_dates = await _last_movements(conn, ledger_ids, as_of)
    accounts: list[dict[str, Any]] = []
    gaps: list[str] = []
    for row in rows:
        ledger_id = str(row["ledger_account_id"])
        balance = balances[ledger_id]
        stated = row["statement_balance_as_of"]
        # A statement dated after as_of says nothing about the balance on as_of.
        known = has_known_balance(row) and (stated is None or stated <= as_of)
        # The age of the balance comes from ledger.common, the one definition the context uses.
        anchor = balance_anchor(row, last_dates[ledger_id])
        as_of_date = anchor if known else None
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
                f"{row['name']} has no stated balance as of {as_of.isoformat()}; the projection "
                "uses its recorded net change, not a verified bank balance"
            )
        elif age is not None and age > 30:
            gaps.append(f"{row['name']} balance is {age} days old as of {as_of.isoformat()}")
    return accounts, gaps


async def _goals(conn: Connection, as_of: date, *, known_then: bool) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT * FROM goals
        WHERE status = 'active'
          AND (NOT $2 OR (created_at AT TIME ZONE 'Asia/Kolkata')::date <= $1)
        ORDER BY priority, deadline NULLS LAST
        """,
        as_of,
        known_then,
    )
    account_ids = [str(row["account_id"]) for row in rows if row.get("account_id")]
    ledger_by_account: dict[str, str] = {}
    if account_ids:
        account_rows = await conn.fetch(
            """
            SELECT id, ledger_account_id
            FROM accounts
            WHERE id = ANY(ARRAY(SELECT unnest($1::text[])))
            """,
            account_ids,
        )
        ledger_by_account = {str(acc["id"]): str(acc["ledger_account_id"]) for acc in account_rows}
    balances = await _balances_at(conn, list(ledger_by_account.values()), as_of)
    goals: list[dict[str, Any]] = []
    for row in rows:
        ledger_id = ledger_by_account.get(str(row["account_id"]))
        saved = balances.get(ledger_id, 0) if ledger_id else 0
        goals.append(
            {
                **dict(row),
                "saved_paise": max(saved, 0),
            }
        )
    return goals


async def _spend_rows(conn: Connection, as_of: date) -> list[SpendRow]:
    """Positive spend in the history window, by day, category, and paying card."""
    rows = await conn.fetch(
        """
        SELECT v.date, v.category_id,
               CASE WHEN fa.kind = 'card' THEN fa.id
                    WHEN ta.kind = 'card' THEN ta.id END AS card_id,
               sum(v.amount_paise)::bigint AS amount_paise
        FROM v_spending v
        JOIN pgledger_transfers t ON t.id = v.transfer_id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        JOIN v_movement_state ms ON ms.movement_id = v.movement_id
        WHERE v.date BETWEEN $1 AND $2
          AND v.amount_paise > 0
          AND ms.is_effective
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """,
        as_of - timedelta(days=HISTORY_DAYS),
        as_of - timedelta(days=1),
    )
    return [
        SpendRow(
            date=row["date"],
            category_id=str(row["category_id"]) if row["category_id"] else None,
            card_id=str(row["card_id"]) if row["card_id"] else None,
            amount_paise=int(row["amount_paise"]),
        )
        for row in rows
    ]


async def _category_rows(conn: Connection) -> list[CategoryRow]:
    baselines = {row["category_id"]: row for row in await conn.fetch("SELECT * FROM baselines")}
    rows: list[CategoryRow] = []
    for category in await conn.fetch("SELECT * FROM categories WHERE active ORDER BY name"):
        baseline = baselines.get(category["id"])
        rows.append(
            CategoryRow(
                id=str(category["id"]),
                name=str(category["name"]),
                reducible=bool(category["reducible"]),
                baseline_per_day_paise=(
                    int(baseline["amount_per_day_paise"]) if baseline is not None else None
                ),
                baseline_source=str(baseline["source"]) if baseline is not None else None,
            )
        )
    return rows


async def _owed_events(
    conn: Connection, *, as_of: date, end: date, known_then: bool
) -> tuple[list[CashEvent], list[SourceRef], list[str]]:
    # The person account's balance is what is still owed; a label's transfer amount is not.
    # Reading the label amount counted a repaid or reversed split as an expected receipt.
    rows = await conn.fetch(
        """
        SELECT p.id AS payee_id, p.display_name, p.outside_text,
               coalesce(sum(e.amount), 0)::bigint AS balance_paise,
               min(ml.expected_date) AS expected_date
        FROM accounts a
        JOIN payees p ON p.person_account_id = a.id
        LEFT JOIN pgledger_entries e ON e.account_id = a.ledger_account_id
        LEFT JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
        LEFT JOIN movements m ON m.id = ml.movement_id
        WHERE a.kind = 'person'
          AND (NOT $2 OR (a.created_at AT TIME ZONE 'Asia/Kolkata')::date <= $1)
          AND m.date <= $1
        GROUP BY p.id, p.display_name, p.outside_text
        HAVING coalesce(sum(e.amount), 0) <> 0 AND min(ml.expected_date) IS NOT NULL
        ORDER BY min(ml.expected_date), p.display_name
        """,
        as_of,
        known_then,
    )
    events: list[CashEvent] = []
    sources: list[SourceRef] = []
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
        balance = int(row["balance_paise"])
        signed = balance
        kind = "owed_receipt" if signed > 0 else "owed_payment"
        # payees.display_name is NOT NULL, so its mark is never null either.
        display_name = cast(
            str, mark(row["display_name"]) if row["outside_text"] else row["display_name"]
        )
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


async def load_snapshot(
    conn: Connection,
    *,
    as_of: date | None = None,
    horizon_days: int | None = None,
    known_then: bool = False,
) -> LedgerSnapshot:
    """Read everything the projection needs from the ledger as of ``as_of``.

    ``known_then`` asks for the ledger as it stood on that day: an account, schedule, or goal
    created later is left out. The backtest uses it; a projection requested "as of" an earlier
    date from today's data does not.
    """
    as_of = as_of or ledger_today()
    settings = await read_settings(conn)
    end = as_of + timedelta(
        days=await _horizon_for_loading(conn, as_of, horizon_days, known_then=known_then)
    )

    liquid_rows, gaps = await _liquid_rows(conn, as_of, known_then=known_then)
    card_rows, card_gaps = await _card_rows(conn, as_of, known_then=known_then)
    gaps.extend(card_gaps)

    goal_rows = await _goals(conn, as_of, known_then=known_then)
    goal_by_schedule = {
        row["contribution_schedule_id"]: row for row in goal_rows if row["contribution_schedule_id"]
    }
    card_by_account = {row["id"]: row for row in card_rows}

    occurrences: list[Occurrence] = []
    schedule_sources: dict[str, SourceRef] = {}
    schedule_rows = await conn.fetch(
        """
        SELECT * FROM schedules
        WHERE active
          AND (NOT $2 OR (created_at AT TIME ZONE 'Asia/Kolkata')::date <= $1)
        ORDER BY name
        """,
        as_of,
        known_then,
    )
    expanded_by_id = await schedules_mod.expand_schedules(
        conn, schedule_rows, as_of + timedelta(days=1), end, as_of=as_of
    )
    for row in schedule_rows:
        amount = row["amount_paise"]
        expanded = expanded_by_id[str(row["id"])]
        if not expanded:
            continue
        # schedules.name is NOT NULL, so its mark is never null either.
        display_name = cast(str, mark(row["name"]) if row["outside_text"] else row["name"])
        schedule_sources[str(row["id"])] = {
            "kind": "schedule",
            "id": row["id"],
            "name": display_name,
            "detail": f"{row['kind']} · {row['rule']}",
            "amount_paise": int(amount) if amount else None,
        }
        goal = goal_by_schedule.get(row["id"])
        for occurrence in expanded:
            card = card_by_account.get(occurrence["account_id"])
            # An occurrence's name comes from its schedule, which is NOT NULL.
            name = cast(
                str,
                mark(occurrence["name"]) if occurrence["outside_text"] else occurrence["name"],
            )
            occurrences.append(
                Occurrence(
                    schedule_id=str(row["id"]),
                    name=name,
                    kind=occurrence["kind"],
                    date=date.fromisoformat(occurrence["date"]),
                    amount_paise=occurrence["amount_paise"],
                    remaining_paise=occurrence["remaining_paise"],
                    category_id=(
                        str(occurrence["category_id"]) if occurrence["category_id"] else None
                    ),
                    card_id=card["id"] if card else None,
                    goal_id=goal["id"] if goal else None,
                    confirmed=bool(row["confirmed"]),
                    linked_goal=goal is not None,
                )
            )

    owed_events, owed_sources, owed_gaps = await _owed_events(
        conn, as_of=as_of, end=end, known_then=known_then
    )
    gaps.extend(owed_gaps)

    liquid = tuple(
        LiquidAccount(
            id=row["id"],
            name=row["name"],
            balance_paise=int(row["balance_paise"]),
            as_of=row["as_of_date"],
            balance_age_days=row["age_days"],
            kind=row["kind"],
            known=row["known"],
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
            known=row["known"],
            credit_paise=int(row["credit_paise"]),
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
    balance_ages: list[BalanceAge] = [
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
            "known": row["known"],
        }
        for row in card_rows
    ]
    outside = any(
        row["outside_text"]
        for row in await conn.fetch("SELECT outside_text FROM schedules WHERE active")
    ) or any(row["outside_text"] for row in await conn.fetch("SELECT outside_text FROM payees"))

    return LedgerSnapshot(
        as_of=as_of,
        floor_paise=int(settings["floor_paise"] or 0),
        liquid=liquid,
        cards=cards,
        goals=goals,
        occurrences=tuple(occurrences),
        schedules=tuple(
            ConfiguredSchedule(kind=row["kind"], confirmed=bool(row["confirmed"]), ends=row["ends"])
            for row in schedule_rows
        ),
        owed=tuple(owed_events),
        categories=tuple(await _category_rows(conn)),
        spend=tuple(await _spend_rows(conn, as_of)),
        ask_horizon_days=horizon_days,
        gaps=tuple(gaps),
        balance_ages=tuple(balance_ages),
        schedule_sources=tuple(schedule_sources.values()),
        owed_sources=tuple(owed_sources),
        outside_text=outside,
    )


async def load_inputs(
    conn: Connection,
    *,
    as_of: date | None = None,
    horizon_days: int | None = None,
    known_then: bool = False,
) -> ProjectionInput:
    """Build the pure engine's inputs from the ledger as of ``as_of``."""
    return build_inputs(
        await load_snapshot(conn, as_of=as_of, horizon_days=horizon_days, known_then=known_then)
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


async def project(
    conn: Connection,
    *,
    horizon_days: int | None = None,
    hypothetical: dict[str, Any] | Hypothetical | None = None,
    as_of: date | None = None,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    known_then: bool = False,
) -> Projection:
    """The full projection or affordability result, including the daily curve and sources."""
    purchase = _hypothetical(hypothetical)
    load_horizon = horizon_days
    if purchase is not None:
        base = as_of or ledger_today()
        last = purchase.date
        if purchase.method == "emi":
            last = _add_months(purchase.date, (purchase.emi_months or 3) - 1)
        if purchase.method in {"card", "emi"}:
            # A card bill lands up to two statement cycles after the charge or instalment.
            last += timedelta(days=62)
        needed = max(0, (last - base).days + 1)
        load_horizon = max(load_horizon or 0, needed)
    inputs = await load_inputs(conn, as_of=as_of, horizon_days=load_horizon, known_then=known_then)
    if purchase is not None:
        return afford(inputs, purchase, runs=runs, seed=seed)
    return run_projection(inputs, runs=runs, seed=seed)


def _money_low(row: MedianLow | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {"date": row["date"], "amount": money(int(row["paise"]))}


def summary(result: Projection) -> dict[str, Any]:
    """The compact model-facing payload: numbers, citations, and data quality, no curve."""
    optimistic = result["optimistic"]
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
                "remaining_after_plan": money(goal["remaining_after_plan_paise"]),
            }
            for goal in result["goals"]
        ],
        "obligations_met": result["obligations_met"],
        "data_quality": result["data_quality"],
        "source_ids": result["source_ids"],
        "outside_text": bool(result["data_quality"].get("outside_text")),
        "optimistic": (
            {
                "floor_probability": optimistic["floor_probability"],
                "median_low": _money_low(optimistic["median_low"]),
                "income": optimistic["income"],
            }
            if optimistic is not None
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


def card(result: Projection) -> dict[str, Any]:
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
                "remaining_after_plan": money(goal["remaining_after_plan_paise"]),
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


__all__ = ["card", "load_inputs", "load_snapshot", "project", "summary"]
