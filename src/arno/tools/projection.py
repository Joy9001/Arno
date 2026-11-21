"""The `project` read tool: load the ledger into the engine and return a citable projection.

The engine itself is pure. This module is its only database seam: it reads balances as of a date,
expands schedules with the same holiday-aware rules the ledger uses, learns spend history and card
shares, and hands back source IDs for every underlying account, schedule, goal, and category.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal, cast

from arno.db.conn import Connection
from arno.engine import series
from arno.engine.affordability import Hypothetical, afford, project_scenario, validate_hypothetical
from arno.engine.inputs import (
    CategoryRow,
    ConfiguredSchedule,
    LedgerSnapshot,
    Occurrence,
    build_inputs,
)
from arno.engine.money import MAX_PAISE, _add_months, money
from arno.engine.patterns import (
    MISSED_AFTER_DAYS,
    KnownSchedule,
    family_interval_days,
    is_covered,
    rule_interval_days,
)
from arno.engine.projection import (
    BASE_HORIZON_DAYS,
    DEFAULT_RUNS,
    DEFAULT_SEED,
    MAX_HORIZON_DAYS,
    RARE_WINDOW_DAYS,
    BalanceAge,
    CardAccount,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    MedianLow,
    Projection,
    ProjectionInput,
    SafeToSpend,
    Scenario,
    SeriesForecast,
    SourceRef,
    SpendRow,
)
from arno.engine.projection import (
    project as run_projection,
)
from arno.engine.scenario import (
    MAX_CHANGES,
    AddRecurring,
    ChangeAmount,
    OneTime,
    PauseSchedule,
    ScenarioChangeSpec,
    StopIncome,
    scenario_horizon_days,
)
from arno.india.workdays import calendar_for
from arno.ledger import schedules as schedules_mod
from arno.ledger.common import balance_anchor, has_known_balance, ledger_balances_at
from arno.ledger.common import today as ledger_today
from arno.ledger.settings import read_settings
from arno.tools.mark import mark


# shortcut: max 2 concurrent off-loop projections and 90d fast-path cap on home/risk, revisit if
# multiuser hosting is unparked or home needs multi-year goal horizons
@lru_cache(maxsize=8)
def _compute_slots(loop: asyncio.AbstractEventLoop) -> asyncio.Semaphore:
    return asyncio.Semaphore(2)


async def run_compute[**P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    """Keep the worker's slot until it finishes, even if the request is cancelled."""
    slots = _compute_slots(asyncio.get_running_loop())
    await slots.acquire()
    worker = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))

    def release(done: asyncio.Task[R]) -> None:
        slots.release()
        if not done.cancelled():
            done.exception()

    worker.add_done_callback(release)
    return await asyncio.shield(worker)


async def _balance_at(conn: Connection, ledger_account_id: str, as_of: date) -> int:
    return (await ledger_balances_at(conn, [ledger_account_id], as_of))[ledger_account_id]


async def _horizon_for_loading(
    conn: Connection,
    as_of: date,
    ask_horizon_days: int | None,
    *,
    horizon_limit_days: int,
    known_then: bool,
) -> int:
    requested = max(BASE_HORIZON_DAYS, ask_horizon_days or 0)
    if requested >= horizon_limit_days:
        return horizon_limit_days
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
    return min(horizon_limit_days, max(requested, furthest))


async def _last_movements(
    conn: Connection, ledger_account_ids: list[str], as_of: date
) -> dict[str, date | None]:
    """The date of each account's last movement on or before ``as_of``."""
    if not ledger_account_ids:
        return {}
    # Same `ARRAY(SELECT unnest(...))` generic-plan idiom as `ledger_balances_at`.
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
    balances = await ledger_balances_at(conn, ledger_ids, as_of)
    last_dates = await _last_movements(conn, ledger_ids, as_of)
    unbilled = await schedules_mod.card_unbilled(conn, rows, as_of)
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
                "unbilled_paise": max(0, min(unbilled[ledger_id], outstanding)),
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
    balances = await ledger_balances_at(conn, ledger_ids, as_of)
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
    balances = await ledger_balances_at(conn, list(ledger_by_account.values()), as_of)
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


async def _spend_rows(
    conn: Connection,
    as_of: date,
    *,
    excluded_movement_ids: Sequence[str] = (),
) -> list[SpendRow]:
    """Positive spend over the long window, by day, category, and paying card."""
    rows = await conn.fetch(
        """
        SELECT v.date, v.category_id,
               CASE WHEN fa.kind = 'card' THEN fa.id
                    WHEN ta.kind = 'card' THEN ta.id END AS card_id,
               sum(v.amount_paise)::bigint AS amount_paise
        FROM v_spending v
        JOIN movements m ON m.id = v.movement_id
        JOIN pgledger_transfers t ON t.id = v.transfer_id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        JOIN v_movement_state ms ON ms.movement_id = v.movement_id
        WHERE v.date BETWEEN $1 AND $2
          AND v.amount_paise > 0
          AND ms.is_effective
          AND NOT m.one_off
          AND NOT EXISTS (
              SELECT 1 FROM schedule_payments sp
              WHERE sp.movement_id = v.movement_id
          )
          AND NOT (v.movement_id = ANY($3::text[]))
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """,
        as_of - timedelta(days=RARE_WINDOW_DAYS),
        as_of - timedelta(days=1),
        sorted(excluded_movement_ids),
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


def _detection_from_row(row: dict[str, Any]) -> series.Detection:
    jitter = tuple((int(pair[0]), float(pair[1])) for pair in row["jitter"])
    samples = tuple(int(value) for value in row["samples"])
    return series.Detection(
        rule=series.Rule(
            family=str(row["family"]),
            rrule=str(row["rrule"]),
            starts=cast(date, row["starts"]),
            holiday_adjust=str(row["holiday_adjust"]),
            description=str(row["description"]),
        ),
        confidence=float(row["confidence"]),
        rule_posterior=float(row["confidence"]),
        skip=float(row["skip"]),
        jitter=jitter,
        end=0.0,
        alive=float(row["alive"]),
        matched=tuple(str(movement_id) for movement_id in row["matched_movement_ids"]),
        amounts=series.Amounts(
            kind=str(row["amount_kind"]),
            level_paise=int(row["level_paise"]),
            low_paise=int(row["low_paise"]),
            high_paise=int(row["high_paise"]),
            coverage=0.0,
            samples=samples,
        ),
        next_dates=(),
    )


async def _series_forecasts(
    conn: Connection,
    *,
    as_of: date,
    end: date,
    known_then: bool,
    schedules: Sequence[KnownSchedule],
    category_reducible: dict[str, bool],
) -> tuple[tuple[SeriesForecast, ...], set[str]]:
    """Projected unconfirmed spend detections and their matched movement IDs."""
    rows = await conn.fetch(
        """
        SELECT d.*, p.display_name AS payee_name, p.outside_text AS payee_outside_text
        FROM series_detections d
        JOIN payees p ON p.id = d.payee_id
        WHERE d.kind = 'spend'
          AND d.confidence >= $1
          AND p.active
          AND (NOT $2 OR d.as_of <= $3)
        ORDER BY p.display_name, d.id
        """,
        series.PROJECT_CONFIDENCE,
        known_then,
        as_of,
    )
    if not rows:
        return (), set()

    cal = calendar_for(as_of - timedelta(days=7), end + timedelta(days=7))
    forecasts: list[SeriesForecast] = []
    matched_ids: set[str] = set()
    for row in rows:
        payee_name = str(row["payee_name"])
        category_id = str(row["category_id"]) if row["category_id"] else None
        categories = {category_id} if category_id is not None else set()
        level_paise = int(row["level_paise"])
        interval_days = family_interval_days(str(row["family"]))
        if is_covered(
            normalized=series.normalize(payee_name),
            categories=categories,
            amount_paise=level_paise,
            interval_days=interval_days,
            schedules=schedules,
        ):
            continue

        detection = _detection_from_row(row)
        expected = tuple(series.upcoming(detection, after=as_of, until=end, calendar=cal))
        amounts = (
            (level_paise,)
            if row["amount_kind"] in ("fixed", "stepped")
            else tuple(int(value) for value in row["samples"])
        )
        display_name = cast(
            str,
            mark(payee_name) if row["payee_outside_text"] else payee_name,
        )
        forecasts.append(
            SeriesForecast(
                id=str(row["id"]),
                name=display_name,
                expected=expected,
                jitter=detection.jitter,
                skip=detection.skip,
                alive=detection.alive,
                amounts=amounts,
                card_id=str(row["card_id"]) if row["card_id"] else None,
                category_id=category_id,
                reducible=category_reducible.get(category_id, False) if category_id else False,
            )
        )
        matched_ids.update(str(movement_id) for movement_id in row["matched_movement_ids"])

    return tuple(forecasts), matched_ids


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
    horizon_limit_days: int = MAX_HORIZON_DAYS,
) -> LedgerSnapshot:
    """Read everything the projection needs from the ledger as of ``as_of``.

    ``known_then`` asks for the ledger as it stood on that day: an account, schedule, or goal
    created later is left out. The backtest uses it; a projection requested "as of" an earlier
    date from today's data does not.
    """
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        return await _load_snapshot(
            conn,
            as_of=as_of or ledger_today(),
            horizon_days=horizon_days,
            horizon_limit_days=horizon_limit_days,
            known_then=known_then,
        )


async def _load_snapshot(
    conn: Connection,
    *,
    as_of: date,
    horizon_days: int | None,
    horizon_limit_days: int,
    known_then: bool,
) -> LedgerSnapshot:
    settings = await read_settings(conn)
    end = as_of + timedelta(
        days=await _horizon_for_loading(
            conn, as_of, horizon_days, horizon_limit_days=horizon_limit_days, known_then=known_then
        )
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
    tomorrow = as_of + timedelta(days=1)
    recent_due = as_of - timedelta(days=MISSED_AFTER_DAYS)
    expanded_by_id = await schedules_mod.expand_schedules(
        conn,
        schedule_rows,
        tomorrow,
        end,
        as_of=as_of,
        starts_by_id={
            str(row["id"]): recent_due
            for row in schedule_rows
            if row["kind"] in {"obligation", "instalment"}
        },
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
            unbilled_paise=int(row["unbilled_paise"]),
            emi_rate_bps=row["emi_rate_bps"],
            emi_fee_paise=row["emi_fee_paise"],
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
    category_rows = await _category_rows(conn)
    known_schedules = [
        KnownSchedule(
            id=str(row["id"]),
            name=str(row["name"]),
            normalized=series.normalize(str(row["name"])),
            amount_paise=int(row["amount_paise"]) if row["amount_paise"] else None,
            category_id=str(row["category_id"]) if row["category_id"] else None,
            interval_days=rule_interval_days(row["rule"]),
            confirmed=bool(row["confirmed"]),
        )
        for row in schedule_rows
        if row["confirmed"]
    ]
    series_forecasts, excluded_movement_ids = await _series_forecasts(
        conn,
        as_of=as_of,
        end=end,
        known_then=known_then,
        schedules=known_schedules,
        category_reducible={row.id: row.reducible for row in category_rows},
    )

    scheduled_categories = {
        (item.schedule_id, item.category_id)
        for item in occurrences
        if item.kind in {"obligation", "instalment"}
        and item.category_id is not None
        and (item.remaining_paise or 0) > 0
    }
    linked = set()
    if scheduled_categories:
        linked = {
            (str(row["schedule_id"]), str(row["category_id"]))
            for row in await conn.fetch(
                """
                SELECT DISTINCT o.schedule_id, o.category_id
                FROM schedule_occurrences o
                JOIN schedule_payments p ON p.occurrence_id = o.id
                JOIN movements m ON m.id = p.movement_id
                JOIN v_movement_state ms ON ms.movement_id = m.id
                WHERE o.schedule_id = ANY($1::text[]) AND ms.is_effective
                  AND m.date BETWEEN $2 AND $3
                """,
                sorted({schedule_id for schedule_id, _ in scheduled_categories}),
                as_of - timedelta(days=RARE_WINDOW_DAYS),
                as_of,
            )
        }
    # Keep variable spend only where all projected schedules in the category are attributed.
    unattributed = {category_id for _, category_id in scheduled_categories - linked}

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
        categories=tuple(category_rows),
        spend=tuple(
            await _spend_rows(conn, as_of, excluded_movement_ids=sorted(excluded_movement_ids))
        ),
        history_start=await conn.fetchval(
            "SELECT min(date) FROM movements WHERE date <= $1", as_of
        ),
        series=series_forecasts,
        attributed_categories=frozenset(
            category_id
            for _, category_id in scheduled_categories
            if category_id not in unattributed
        ),
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
    horizon_limit_days: int = MAX_HORIZON_DAYS,
) -> ProjectionInput:
    """Build the pure engine's inputs from the ledger as of ``as_of``."""
    snapshot = await load_snapshot(
        conn,
        as_of=as_of,
        horizon_days=horizon_days,
        known_then=known_then,
        horizon_limit_days=horizon_limit_days,
    )
    return replace(build_inputs(snapshot), horizon_limit_days=horizon_limit_days)


def _hypothetical(payload: dict[str, Any] | Hypothetical | None) -> Hypothetical | None:
    if payload is None or isinstance(payload, Hypothetical):
        return payload
    accepted = (
        "amount_paise, date, method, emi_months, account, emi_rate_percent, emi_fee_paise, "
        "and no_cost"
    )
    unknown = set(payload) - {
        "amount_paise",
        "date",
        "method",
        "emi_months",
        "account",
        "emi_rate_percent",
        "emi_fee_paise",
        "no_cost",
    }
    if unknown:
        raise ValueError(f"hypothetical takes {accepted}, not {', '.join(sorted(unknown))}")
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
        emi_rate_bps=(
            round(float(payload["emi_rate_percent"]) * 100)
            if payload.get("emi_rate_percent") is not None
            else None
        ),
        emi_fee_paise=(
            int(payload["emi_fee_paise"]) if payload.get("emi_fee_paise") is not None else None
        ),
        no_cost=bool(payload.get("no_cost")),
    )


def _parse_change_date(value: Any, *, field: str, today: date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if value == "today":
        return today
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a YYYY-MM-DD date")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be a YYYY-MM-DD date") from exc


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    if value > MAX_PAISE:
        raise ValueError(f"{field} must be at most {MAX_PAISE} paise")
    return value


def _nonempty_str(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _parse_raw_changes(
    raw_changes: Sequence[dict[str, Any]], *, as_of: date
) -> list[dict[str, Any]]:
    if isinstance(raw_changes, (str, bytes)) or not isinstance(raw_changes, Sequence):
        raise ValueError("changes must be a list of 1 to 12 items")
    if not 1 <= len(raw_changes) <= MAX_CHANGES:
        raise ValueError(f"changes must contain 1 to {MAX_CHANGES} items")
    allowed_fields: dict[str, set[str]] = {
        "stop_income": {"kind", "start", "until", "schedule"},
        "pause_schedule": {"kind", "schedule", "start", "until"},
        "change_amount": {"kind", "schedule", "amount_paise", "start", "until"},
        "add_recurring": {
            "kind",
            "name",
            "amount_paise",
            "direction",
            "start",
            "until",
            "every",
            "goal",
        },
        "one_time": {"kind", "name", "amount_paise", "direction", "date"},
    }
    parsed: list[dict[str, Any]] = []
    max_offset = MAX_HORIZON_DAYS - 30
    for item in raw_changes:
        if not isinstance(item, dict):
            raise ValueError("each change must be an object with a kind")
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in allowed_fields:
            supported = ", ".join(sorted(allowed_fields))
            raise ValueError(f"change kind must be one of {supported}")
        unknown = set(item) - allowed_fields[kind]
        if unknown:
            raise ValueError(f"{kind} does not accept {', '.join(sorted(unknown))}")

        if kind == "one_time":
            name = _nonempty_str(item.get("name"), field="one_time.name")
            amount_paise = _positive_int(item.get("amount_paise"), field="one_time.amount_paise")
            direction_raw = _nonempty_str(item.get("direction"), field="one_time.direction")
            if direction_raw not in ("out", "in"):
                raise ValueError("one_time.direction must be 'out' or 'in'")
            when = _parse_change_date(item.get("date"), field="one_time.date", today=as_of)
            if when < as_of:
                raise ValueError(
                    f"change date {when.isoformat()} cannot be before {as_of.isoformat()}"
                )
            if (when - as_of).days > max_offset:
                raise ValueError("change date is beyond the projection horizon")
            parsed.append(
                {
                    "kind": kind,
                    "name": name,
                    "amount_paise": amount_paise,
                    "direction": direction_raw,
                    "date": when,
                }
            )
            continue

        start = _parse_change_date(item.get("start"), field=f"{kind}.start", today=as_of)
        if start < as_of:
            raise ValueError(
                f"change start {start.isoformat()} cannot be before {as_of.isoformat()}"
            )
        if (start - as_of).days > max_offset:
            raise ValueError("change date is beyond the projection horizon")
        raw_until = item.get("until")
        until = (
            _parse_change_date(raw_until, field=f"{kind}.until", today=as_of)
            if raw_until is not None
            else None
        )
        if until is not None:
            if until < start:
                raise ValueError(
                    f"change until {until.isoformat()} must be on or after start "
                    f"{start.isoformat()}"
                )
            if (until - as_of).days > max_offset:
                raise ValueError("change date is beyond the projection horizon")

        if kind == "stop_income":
            raw_sched = item.get("schedule")
            schedule = (
                _nonempty_str(raw_sched, field="stop_income.schedule")
                if raw_sched is not None
                else None
            )
            parsed.append({"kind": kind, "start": start, "until": until, "schedule": schedule})
        elif kind == "pause_schedule":
            schedule = _nonempty_str(item.get("schedule"), field="pause_schedule.schedule")
            parsed.append({"kind": kind, "schedule": schedule, "start": start, "until": until})
        elif kind == "change_amount":
            schedule = _nonempty_str(item.get("schedule"), field="change_amount.schedule")
            amount_paise = _positive_int(
                item.get("amount_paise"), field="change_amount.amount_paise"
            )
            parsed.append(
                {
                    "kind": kind,
                    "schedule": schedule,
                    "amount_paise": amount_paise,
                    "start": start,
                    "until": until,
                }
            )
        else:
            name = _nonempty_str(item.get("name"), field="add_recurring.name")
            amount_paise = _positive_int(
                item.get("amount_paise"), field="add_recurring.amount_paise"
            )
            direction_raw = _nonempty_str(item.get("direction"), field="add_recurring.direction")
            if direction_raw not in ("out", "in"):
                raise ValueError("add_recurring.direction must be 'out' or 'in'")
            raw_every = item.get("every")
            every_raw = "month" if raw_every is None else raw_every
            if not isinstance(every_raw, str) or every_raw not in ("month", "week", "year"):
                raise ValueError("add_recurring.every must be 'month', 'week', or 'year'")
            raw_goal = item.get("goal")
            goal = (
                _nonempty_str(raw_goal, field="add_recurring.goal")
                if raw_goal is not None
                else None
            )
            if goal is not None and direction_raw != "out":
                raise ValueError("goal contributions must have direction 'out'")
            parsed.append(
                {
                    "kind": kind,
                    "name": name,
                    "amount_paise": amount_paise,
                    "direction": direction_raw,
                    "start": start,
                    "until": until,
                    "every": every_raw,
                    "goal": goal,
                }
            )
    return parsed


def _raw_horizon_days(
    as_of: date, parsed: Sequence[dict[str, Any]], *, ask_horizon_days: int | None
) -> int:
    specs: list[ScenarioChangeSpec] = []
    for item in parsed:
        kind = item["kind"]
        if kind == "one_time":
            specs.append(OneTime(name="x", amount_paise=1, direction="out", date=item["date"]))
        else:
            specs.append(StopIncome(start=item["start"], until=item["until"]))
    return scenario_horizon_days(as_of, specs, ask_horizon_days=ask_horizon_days)


def _display_name(name: str, outside_text: bool) -> str:
    return cast(str, mark(name) if outside_text else name)


def _candidate_names(rows: Sequence[Any], name_key: str, outside_key: str | None) -> str:
    if not rows:
        return "none"
    return ", ".join(
        _display_name(str(row[name_key]), bool(row[outside_key]) if outside_key else False)
        for row in rows
    )


async def _resolve_changes(
    conn: Connection,
    inputs: ProjectionInput,
    parsed: Sequence[dict[str, Any]],
    *,
    as_of: date,
    known_then: bool,
) -> tuple[list[ScenarioChangeSpec], set[str]]:
    schedule_rows = await conn.fetch(
        """
        SELECT id, name, kind, outside_text
        FROM schedules
        WHERE active
          AND (NOT $2 OR (created_at AT TIME ZONE 'Asia/Kolkata')::date <= $1)
        ORDER BY name, id
        """,
        as_of,
        known_then,
    )
    income_schedules = [row for row in schedule_rows if str(row["kind"]).startswith("income")]
    income_source_ids = {str(row["id"]) for row in income_schedules}

    series_rows: list[Any] = []
    if inputs.series:
        series_rows = list(
            await conn.fetch(
                """
                SELECT d.id, p.display_name, p.outside_text
                FROM series_detections d
                JOIN payees p ON p.id = d.payee_id
                WHERE d.id = ANY(ARRAY(SELECT unnest($1::text[])))
                ORDER BY p.display_name, d.id
                """,
                [item.id for item in inputs.series],
            )
        )

    goal_rows: list[Any] = []
    if any(item.get("goal") is not None for item in parsed):
        goal_rows = list(
            await conn.fetch(
                """
                SELECT id, name
                FROM goals
                WHERE status = 'active'
                  AND (NOT $2 OR (created_at AT TIME ZONE 'Asia/Kolkata')::date <= $1)
                ORDER BY name, id
                """,
                as_of,
                known_then,
            )
        )

    resolved: list[ScenarioChangeSpec] = []
    for item in parsed:
        kind = item["kind"]
        if kind == "stop_income":
            target = item["schedule"]
            if target is None:
                resolved.append(StopIncome(start=item["start"], until=item["until"]))
                continue
            key = target.lower()
            matches = [row for row in income_schedules if str(row["name"]).strip().lower() == key]
            if len(matches) == 1:
                row = matches[0]
                resolved.append(
                    StopIncome(
                        start=item["start"],
                        until=item["until"],
                        source_id=str(row["id"]),
                        label_name=_display_name(str(row["name"]), bool(row["outside_text"])),
                    )
                )
            elif len(matches) > 1:
                cands = _candidate_names(matches, "name", "outside_text")
                raise ValueError(f"multiple income schedules match {target!r}: {cands}")
            else:
                non_income = [
                    row for row in schedule_rows if str(row["name"]).strip().lower() == key
                ]
                cands = _candidate_names(income_schedules, "name", "outside_text")
                if non_income:
                    raise ValueError(
                        f"{target!r} is not an income schedule; active income schedules: {cands}"
                    )
                raise ValueError(
                    f"unknown income schedule {target!r}; active income schedules: {cands}"
                )

        elif kind == "pause_schedule":
            target = item["schedule"]
            key = target.lower()
            sched_matches = [
                row for row in schedule_rows if str(row["name"]).strip().lower() == key
            ]
            if len(sched_matches) == 1:
                row = sched_matches[0]
                resolved.append(
                    PauseSchedule(
                        start=item["start"],
                        until=item["until"],
                        source_id=str(row["id"]),
                        label_name=_display_name(str(row["name"]), bool(row["outside_text"])),
                    )
                )
            elif len(sched_matches) > 1:
                cands = _candidate_names(sched_matches, "name", "outside_text")
                raise ValueError(f"multiple schedules match {target!r}: {cands}")
            else:
                series_matches = [
                    row for row in series_rows if str(row["display_name"]).strip().lower() == key
                ]
                if len(series_matches) == 1:
                    row = series_matches[0]
                    resolved.append(
                        PauseSchedule(
                            start=item["start"],
                            until=item["until"],
                            series_id=str(row["id"]),
                            label_name=_display_name(
                                str(row["display_name"]), bool(row["outside_text"])
                            ),
                        )
                    )
                elif len(series_matches) > 1:
                    cands = _candidate_names(series_matches, "display_name", "outside_text")
                    raise ValueError(f"multiple recurring charges match {target!r}: {cands}")
                else:
                    sched_cands = _candidate_names(schedule_rows, "name", "outside_text")
                    series_cands = _candidate_names(series_rows, "display_name", "outside_text")
                    raise ValueError(
                        f"unknown schedule or recurring charge {target!r}; "
                        f"active schedules: {sched_cands}; "
                        f"projected recurring charges: {series_cands}"
                    )

        elif kind == "change_amount":
            target = item["schedule"]
            key = target.lower()
            matches = [row for row in schedule_rows if str(row["name"]).strip().lower() == key]
            if len(matches) == 1:
                row = matches[0]
                resolved.append(
                    ChangeAmount(
                        source_id=str(row["id"]),
                        amount_paise=item["amount_paise"],
                        start=item["start"],
                        until=item["until"],
                        label_name=_display_name(str(row["name"]), bool(row["outside_text"])),
                    )
                )
            elif len(matches) > 1:
                cands = _candidate_names(matches, "name", "outside_text")
                raise ValueError(f"multiple schedules match {target!r}: {cands}")
            else:
                cands = _candidate_names(schedule_rows, "name", "outside_text")
                raise ValueError(f"unknown schedule {target!r}; active schedules: {cands}")

        elif kind == "add_recurring":
            goal_id: str | None = None
            goal_name: str | None = None
            target_goal = item["goal"]
            if target_goal is not None:
                key = target_goal.lower()
                goal_matches = [row for row in goal_rows if str(row["name"]).strip().lower() == key]
                if len(goal_matches) == 1:
                    goal_id = str(goal_matches[0]["id"])
                    goal_name = str(goal_matches[0]["name"])
                elif len(goal_matches) > 1:
                    cands = _candidate_names(goal_matches, "name", None)
                    raise ValueError(f"multiple goals match {target_goal!r}: {cands}")
                else:
                    cands = _candidate_names(goal_rows, "name", None)
                    raise ValueError(f"unknown goal {target_goal!r}; active goals: {cands}")
            direction: Literal["out", "in"] = "out" if item["direction"] == "out" else "in"
            every: Literal["month", "week", "year"] = (
                "month"
                if item["every"] == "month"
                else "week"
                if item["every"] == "week"
                else "year"
            )
            resolved.append(
                AddRecurring(
                    name=item["name"],
                    amount_paise=item["amount_paise"],
                    direction=direction,
                    start=item["start"],
                    until=item["until"],
                    every=every,
                    goal_id=goal_id,
                    goal_name=goal_name,
                )
            )

        else:
            direction = "out" if item["direction"] == "out" else "in"
            resolved.append(
                OneTime(
                    name=item["name"],
                    amount_paise=item["amount_paise"],
                    direction=direction,
                    date=item["date"],
                )
            )
    return resolved, income_source_ids


async def project(
    conn: Connection,
    *,
    horizon_days: int | None = None,
    hypothetical: dict[str, Any] | Hypothetical | None = None,
    changes: Sequence[dict[str, Any]] | None = None,
    as_of: date | None = None,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    known_then: bool = False,
    horizon_limit_days: int = MAX_HORIZON_DAYS,
) -> Projection:
    """Return the projection computed from the loaded ledger inputs."""
    _, result = await project_with_inputs(
        conn,
        horizon_days=horizon_days,
        hypothetical=hypothetical,
        changes=changes,
        as_of=as_of,
        runs=runs,
        seed=seed,
        known_then=known_then,
        horizon_limit_days=horizon_limit_days,
    )
    return result


async def project_with_inputs(
    conn: Connection,
    *,
    horizon_days: int | None = None,
    hypothetical: dict[str, Any] | Hypothetical | None = None,
    changes: Sequence[dict[str, Any]] | None = None,
    as_of: date | None = None,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    known_then: bool = False,
    horizon_limit_days: int = MAX_HORIZON_DAYS,
) -> tuple[ProjectionInput, Projection]:
    """The full projection or affordability result, including the daily curve and sources."""
    base = as_of or ledger_today()
    purchase = _hypothetical(hypothetical)
    parsed_changes = _parse_raw_changes(changes, as_of=base) if changes is not None else None
    load_horizon = horizon_days
    if purchase is not None:
        validate_hypothetical(base, purchase)
        last = purchase.date
        if purchase.method == "emi":
            last = _add_months(purchase.date, (purchase.emi_months or 3) - 1)
        if purchase.method in {"card", "emi"}:
            # A card bill lands up to two statement cycles after the charge or instalment.
            last += timedelta(days=62)
        needed = max(0, (last - base).days + 1)
        load_horizon = max(load_horizon or 0, needed)
    if parsed_changes is not None:
        load_horizon = max(
            load_horizon or 0,
            _raw_horizon_days(base, parsed_changes, ask_horizon_days=horizon_days),
        )
    inputs = await load_inputs(
        conn,
        as_of=base,
        horizon_days=load_horizon,
        known_then=known_then,
        horizon_limit_days=horizon_limit_days,
    )
    if parsed_changes is not None:
        specs, income_source_ids = await _resolve_changes(
            conn, inputs, parsed_changes, as_of=base, known_then=known_then
        )
        result = await run_compute(
            project_scenario,
            inputs,
            specs,
            hypothetical=purchase,
            income_source_ids=income_source_ids,
            runs=runs,
            seed=seed,
        )
    elif purchase is not None:
        result = await run_compute(afford, inputs, purchase, runs=runs, seed=seed)
    else:
        result = await run_compute(run_projection, inputs, runs=runs, seed=seed)
    return inputs, result


def _money_low(row: MedianLow | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {"date": row["date"], "amount": money(int(row["paise"]))}


def _safe_to_spend(row: SafeToSpend | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "amount": money(int(row["amount_paise"])),
        "binding": row["binding"],
        "binding_date": row["binding_date"],
    }


def _scenario(row: Scenario) -> dict[str, Any]:
    baseline = row["baseline"]
    payload: dict[str, Any] = {
        "changes": [
            {
                "kind": item["kind"],
                "label": item["label"],
                "occurrences": item["occurrences"],
                "amount": money(int(item["amount_paise"])),
            }
            for item in row["changes"]
        ],
        "assumptions": list(row["assumptions"]),
        "runway_days": row["runway_days"],
        "baseline": {
            "floor_probability": baseline["floor_probability"],
            "median_low": _money_low(baseline["median_low"]),
            "first_floor_crossing": baseline["first_floor_crossing"],
            "safe_to_spend": _safe_to_spend(baseline["safe_to_spend"]),
        },
    }
    if "holds" in row:
        payload["holds"] = row["holds"]
    if "reason" in row:
        payload["reason"] = row["reason"]
    return payload


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
        "safe_to_spend": _safe_to_spend(result["safe_to_spend"]),
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
        baseline = result.get("baseline")
        payload["baseline"] = (
            {**baseline, "median_low": _money_low(baseline["median_low"])}
            if baseline is not None
            else None
        )
    scenario = result.get("scenario")
    if scenario is not None:
        payload["scenario"] = _scenario(scenario)
    return payload


def card(result: Projection, request: dict[str, Any] | None = None) -> dict[str, Any]:
    """The UI metadata card: the full curve, sources, verdict, and every citation.

    ``request`` echoes the arguments the projection was asked with, so the card can
    download a workbook that recomputes exactly this request.
    """
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
        "safe_to_spend": _safe_to_spend(result["safe_to_spend"]),
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
    scenario = result.get("scenario")
    if scenario is not None:
        payload["scenario"] = _scenario(scenario)
    if request is not None:
        payload["request"] = request
    return payload


__all__ = ["card", "load_inputs", "load_snapshot", "project", "summary"]
