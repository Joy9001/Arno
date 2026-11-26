"""Whole-ledger and deterministic engine invariants.

The rules are the financial invariants in `docs/arno/specs/data-and-engine.md#financial-invariants`,
derived from `ledger/movements.py` and `tests`' own view definitions. The restore drill's own
checks (`backup_restore.verify_database`) run first, so there is one definition of "the ledger
balances".

Run it against a scratch database::

    uv run python -m tests.invariants

It refuses any database whose name does not end in `_test`. Each violation names the rule that
broke; the exit code is 1 when there is any.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from arno.backup_restore import verify_database
from arno.db.conn import Connection, connect, conninfo_to_dict, quote_ident
from arno.engine.affordability import Hypothetical, afford
from arno.engine.money import allocate, amortize
from arno.engine.projection import CardAccount, CashEvent, LiquidAccount, ProjectionInput, project
from arno.ledger.common import today
from arno.ledger.errors import InvalidLedgerError
from arno.ledger.schedule_payments import _capacity, payment_evidence, payment_status
from arno.tools import recaps as recap_tools

SYSTEM_KINDS = {"spending", "income", "equity"}


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str
    ids: tuple[str, ...] = ()


@dataclass
class Posting:
    """One `movement_legs` row with its two accounts resolved."""

    transfer_id: str
    movement_id: str
    from_id: str
    from_kind: str
    to_id: str
    to_kind: str
    amount_paise: int
    category_id: str | None
    category_name: str | None
    payee_id: str | None
    payee_kind: str | None
    payee_person_account_id: str | None


@dataclass
class Movement:
    id: str
    date: date
    kind: str
    postings: list[Posting] = field(default_factory=list)

    @property
    def total_paise(self) -> int:
        return sum(posting.amount_paise for posting in self.postings)

    @property
    def consumption_paise(self) -> int:
        into = sum(
            posting.amount_paise for posting in self.postings if posting.to_kind == "spending"
        )
        out = sum(
            posting.amount_paise for posting in self.postings if posting.from_kind == "spending"
        )
        return into - out


_checks_run = 0
_violations: list[Violation] = []


def checks_run() -> int:
    """How many individual assertions the last invariant check made."""
    return _checks_run


def _expect(ok: bool, code: str, detail: str, ids: tuple[str, ...] = ()) -> None:
    global _checks_run
    _checks_run += 1
    if not ok:
        _violations.append(Violation(code=code, detail=detail, ids=ids))


def _ids(rows: list[dict[str, Any]], key: str) -> tuple[str, ...]:
    return tuple(str(row[key]) for row in rows)


def _reset_checks() -> None:
    global _checks_run, _violations
    _checks_run = 0
    _violations = []


async def check_ledger(conn: Connection) -> list[Violation]:
    """Every financial invariant that can be checked from the database alone."""
    _reset_checks()
    movements = await _load_ledger(conn)
    await _check_double_entry(conn, movements)
    _check_movement_postings(movements)
    await _check_money_types(conn)
    await _check_cross_paths(conn)
    await _check_account_identity(conn)
    await _check_legs_view(conn)
    await _check_balance_view(conn)
    await _check_owed_view(conn)
    await _check_schedules(conn)
    await _check_imports(conn)
    await _check_undo_log(conn)
    return list(_violations)


def check_engine() -> list[Violation]:
    """Check conservation and agreement between the pure money and projection paths."""
    _reset_checks()
    principal = 600_000
    loan = amortize(principal, 12, 12)
    rows = loan["schedule"]
    _expect(
        sum(row["principal_paise"] for row in rows) == principal and rows[-1]["balance_paise"] == 0,
        "engine_loan_principal",
        "amortization must account for every paisa of principal and close at zero",
    )
    _expect(
        loan["total_paid"]["paise"] == principal + loan["total_interest"]["paise"],
        "engine_loan_total",
        "total loan payments must reconcile with principal and interest",
    )
    _expect(
        sum(allocate(101, (1, 1, 1))) == 101,
        "engine_allocation",
        "largest-remainder allocation must conserve paise",
    )

    day = date(2026, 1, 10)
    liquid = LiquidAccount(id="cash", name="Cash", balance_paise=100_000)
    inputs = ProjectionInput(
        as_of=day,
        floor_paise=10_000,
        liquid=(liquid,),
        cards=(CardAccount("card", "Card", 5, 10, 30_000, 100_000),),
        events=(CashEvent(day + timedelta(days=1), -20_000, "Rent", "obligation"),),
    )
    result = project(inputs, runs=1)
    _expect(
        [point["median_paise"] for point in result["curve"][:2]] == [70_000, 50_000],
        "engine_cash_conservation",
        "the opening card bill and the next day's rent must leave cash on their due days",
    )
    _expect(
        all(
            point["p10_paise"] <= point["median_paise"] <= point["p90_paise"]
            for point in result["curve"]
        ),
        "engine_band_order",
        "each projection band must be ordered P10, median, P90",
    )
    short = project(replace(inputs, liquid=(replace(liquid, balance_paise=29_999),)), runs=1)
    _expect(
        not short["obligations_met"],
        "engine_day_zero_obligation",
        "a cash shortfall on a card bill due today must fail the obligation gate",
    )
    safe = result["safe_to_spend"]
    _expect(
        safe is not None and safe["amount_paise"] == 40_000,
        "engine_safe_amount",
        "safe cash spending must leave room for both obligations and the floor",
    )
    at_limit = afford(inputs, Hypothetical(40_000, day), runs=1)
    over_limit = afford(inputs, Hypothetical(40_001, day), runs=1)
    _expect(
        at_limit.get("verdict") == "yes" and over_limit.get("verdict") != "yes",
        "engine_affordability_limit",
        "the verdict as asked must agree with the safe cash spending boundary",
    )
    return list(_violations)


async def _load_ledger(conn: Connection) -> dict[str, Movement]:
    movement_rows = await conn.fetch("SELECT id, date, kind FROM movements ORDER BY date, id")
    movements = {
        str(row["id"]): Movement(id=str(row["id"]), date=row["date"], kind=str(row["kind"]))
        for row in movement_rows
    }
    rows = await conn.fetch(
        """
        SELECT
            l.transfer_id, l.movement_id, l.amount_paise,
            fa.id AS from_id, fa.kind AS from_kind,
            ta.id AS to_id, ta.kind AS to_kind,
            l.category_id, c.name AS category_name,
            l.payee_id, p.kind AS payee_kind, p.person_account_id AS payee_person_account_id
        FROM movement_legs l
        JOIN accounts fa ON fa.id = l.from_account_id
        JOIN accounts ta ON ta.id = l.to_account_id
        LEFT JOIN categories c ON c.id = l.category_id
        LEFT JOIN payees p ON p.id = l.payee_id
        ORDER BY l.transfer_id
        """
    )
    for row in rows:
        movement = movements.get(str(row["movement_id"]))
        if movement is not None:
            movement.postings.append(
                Posting(
                    transfer_id=str(row["transfer_id"]),
                    movement_id=str(row["movement_id"]),
                    from_id=str(row["from_id"]),
                    from_kind=str(row["from_kind"]),
                    to_id=str(row["to_id"]),
                    to_kind=str(row["to_kind"]),
                    amount_paise=int(row["amount_paise"]),
                    category_id=row["category_id"],
                    category_name=row["category_name"],
                    payee_id=row["payee_id"],
                    payee_kind=row["payee_kind"],
                    payee_person_account_id=row["payee_person_account_id"],
                )
            )
    return movements


# --- a. double entry --------------------------------------------------------------------------


async def _check_double_entry(conn: Connection, movements: dict[str, Movement]) -> None:
    """Every leg moves a positive amount between two accounts, so the books sum to zero."""
    checks = await verify_database(conn)
    failed = sorted(name for name, ok in checks.items() if not ok)
    _expect(
        not failed,
        "restore_checks_failed",
        f"backup_restore.verify_database failed: {', '.join(failed)}",
    )

    for movement in movements.values():
        _expect(
            bool(movement.postings),
            "movement_without_leg",
            f"movement {movement.id} ({movement.kind}) has no legs",
            (movement.id,),
        )

    bad_legs = await conn.fetch(
        """
        SELECT transfer_id FROM movement_legs
        WHERE amount_paise <= 0 OR from_account_id = to_account_id
        ORDER BY transfer_id
        """
    )
    _expect(
        not bad_legs,
        "leg_not_positive_between_two_accounts",
        "every leg needs amount_paise > 0 and from_account_id <> to_account_id",
        _ids(bad_legs, "transfer_id"),
    )

    total = await conn.fetchval("SELECT coalesce(sum(balance_paise), 0)::bigint FROM v_accounts")
    _expect(
        int(total or 0) == 0,
        "ledger_not_zero_sum",
        f"v_accounts.balance_paise sums to {int(total or 0)} across all accounts, not 0",
    )

    drifted = await conn.fetch(
        """
        SELECT a.id, a.balance_paise::bigint AS view_balance,
               (coalesce(i.total, 0) - coalesce(o.total, 0))::bigint AS leg_balance
        FROM v_accounts a
        LEFT JOIN (
            SELECT to_account_id, sum(amount_paise) AS total FROM movement_legs
            GROUP BY to_account_id
        ) i ON i.to_account_id = a.id
        LEFT JOIN (
            SELECT from_account_id, sum(amount_paise) AS total FROM movement_legs
            GROUP BY from_account_id
        ) o ON o.from_account_id = a.id
        WHERE a.balance_paise <> coalesce(i.total, 0) - coalesce(o.total, 0)
        ORDER BY a.id
        """
    )
    for row in drifted:
        _expect(
            False,
            "account_balance_mismatch",
            f"v_accounts.balance_paise is {int(row['view_balance'])} for {row['id']}, but its "
            f"legs sum to {int(row['leg_balance'])} (in minus out)",
            (str(row["id"]),),
        )


async def _check_money_types(conn: Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name LIKE '%paise%'
          AND data_type NOT IN ('smallint', 'integer', 'bigint')
        ORDER BY table_name, column_name
        """
    )
    _expect(
        not rows,
        "money_column_not_integer",
        "money is integer paise; not integer: "
        + ", ".join(f"{r['table_name']}.{r['column_name']} ({r['data_type']})" for r in rows),
    )


# --- b. posting rules -------------------------------------------------------------------------


def _check_movement_postings(movements: dict[str, Movement]) -> None:
    for movement in movements.values():
        if not movement.postings:
            continue
        postings = movement.postings
        ids = (movement.id,)
        kinds = {posting.to_kind for posting in postings} | {
            posting.from_kind for posting in postings
        }
        total = movement.total_paise
        consumption = movement.consumption_paise
        touches_spending = "spending" in kinds

        if movement.kind == "expense":
            _expect(
                len(postings) == 1
                and postings[0].to_kind == "spending"
                and postings[0].from_kind not in SYSTEM_KINDS
                and consumption == total,
                "expense_postings",
                f"expense {movement.id} must pay exactly {total} paise from one real account "
                f"into Spending; got "
                f"{[(p.from_kind, p.to_kind, p.amount_paise) for p in postings]}",
                ids,
            )
        elif movement.kind == "income":
            _expect(
                len(postings) == 1
                and postings[0].from_kind == "income"
                and postings[0].to_kind not in SYSTEM_KINDS
                and not touches_spending,
                "income_postings",
                f"income {movement.id} must come from Income into one real account and touch no "
                f"spending; got {[(p.from_kind, p.to_kind) for p in postings]}",
                ids,
            )
        elif movement.kind == "transfer":
            _expect(
                "spending" not in kinds and "income" not in kinds,
                "transfer_postings",
                f"transfer {movement.id} touches Spending or Income; own-account transfers, card "
                f"repayments and goal contributions are not consumption ({sorted(kinds, key=str)})",
                ids,
            )
        elif movement.kind == "split":
            payer = {posting.from_id for posting in postings}
            people = [posting for posting in postings if posting.to_kind == "person"]
            my_share = sum(
                posting.amount_paise for posting in postings if posting.to_kind == "spending"
            )
            _expect(
                len(payer) == 1
                and all(posting.to_kind in {"spending", "person"} for posting in postings)
                and len(people) > 0
                and consumption == my_share,
                "split_postings",
                f"split {movement.id} must pay from one account, count only my share "
                f"({my_share}) as consumption and put the rest on person accounts",
                ids,
            )
            for posting in people:
                _expect(
                    posting.payee_id is not None
                    and posting.payee_person_account_id == posting.to_id,
                    "split_share_without_person",
                    f"split {movement.id} sends {posting.amount_paise} paise to "
                    f"{posting.to_id} without a person payee owning that account",
                    (movement.id, posting.to_id or ""),
                )
        elif movement.kind == "loan":
            destinations = {posting.to_kind for posting in postings}
            sources = {posting.from_id for posting in postings}
            _expect(
                len(sources) == 1
                and destinations <= {"loan", "spending"}
                and consumption
                == sum(
                    posting.amount_paise for posting in postings if posting.to_kind == "spending"
                ),
                "loan_postings",
                f"loan payment {movement.id} must pay one account into the loan (principal) and "
                f"Spending (interest only); got {[(p.to_kind, p.amount_paise) for p in postings]}",
                ids,
            )
        elif movement.kind == "refund":
            _expect(
                len(postings) == 1
                and postings[0].from_kind == "spending"
                and postings[0].to_kind not in SYSTEM_KINDS
                and consumption == -total,
                "refund_postings",
                f"refund {movement.id} must move {total} paise out of Spending into one real "
                f"account; got {[(p.from_kind, p.to_kind) for p in postings]}",
                ids,
            )
        elif movement.kind == "settlement":
            _expect(
                len(postings) == 1
                and (postings[0].from_kind == "person") != (postings[0].to_kind == "person")
                and not touches_spending
                and consumption == 0,
                "settlement_postings",
                f"settlement {movement.id} must move money between one person and one real "
                f"account, never Spending; got {[(p.from_kind, p.to_kind) for p in postings]}",
                ids,
            )
        elif movement.kind == "adjustment":
            _expect(
                len(postings) == 1
                and (postings[0].from_kind == "equity") != (postings[0].to_kind == "equity")
                and "spending" not in kinds
                and "income" not in kinds,
                "adjustment_postings",
                f"adjustment {movement.id} must move between Equity and one real account and is "
                f"never consumption; got {[(p.from_kind, p.to_kind) for p in postings]}",
                ids,
            )
            _expect(
                consumption == 0,
                "adjustment_is_consumption",
                f"adjustment {movement.id} moves {consumption} paise through Spending",
                ids,
            )


# --- d. cross-path spending -------------------------------------------------------------------


async def _check_cross_paths(conn: Connection) -> None:
    months = [
        str(row["month"])
        for row in await conn.fetch(
            "SELECT DISTINCT to_char(date, 'YYYY-MM') AS month FROM movements ORDER BY month"
        )
    ]
    for month in months:
        start = date.fromisoformat(f"{month}-01")
        end = _month_end(start)

        from_view = await _spending_from_view(conn, start, end)
        from_summary = await _spending_from_summary(conn, month)
        from_recap = await _spending_from_recap(conn, start, end)
        from_legs = await _spending_from_legs(conn, start, end)
        paths = {
            "v_spending": from_view,
            "summarize_spending": from_summary,
            "recap spending_rows": from_recap,
            "v_legs": from_legs,
        }
        for category in sorted(set().union(*paths.values())):
            values = {name: amounts.get(category, 0) for name, amounts in paths.items()}
            _expect(
                len(set(values.values())) == 1,
                "cross_path_spending_mismatch",
                f"{month}/{category}: " + ", ".join(f"{k}={v}" for k, v in values.items()),
            )
        # The recap list's one grouped query agrees with the per-category sums.
        period = recap_tools.month_period(start, today())
        listed = (await recap_tools.period_totals(conn, [period]))[period.id]
        _expect(
            (listed["paise"] if listed else 0) == sum(from_view.values()),
            "recap_list_total_mismatch",
            f"{month}: recap list total {listed} != v_spending sum {sum(from_view.values())}",
        )


def _month_end(start: date) -> date:
    if start.month == 12:
        return date(start.year + 1, 1, 1) - date.resolution
    return date(start.year, start.month + 1, 1) - date.resolution


async def _spending_from_view(conn: Connection, start: date, end: date) -> dict[str, int]:
    rows = await conn.fetch(
        """
        SELECT coalesce(c.name, 'Uncategorized') AS category,
               sum(CASE WHEN ml.to_account_id = sp.id
                        THEN ml.amount_paise ELSE -ml.amount_paise END)::bigint AS amount
        FROM movement_legs ml
        JOIN movements m ON m.id = ml.movement_id
        JOIN accounts sp ON sp.kind = 'spending'
        LEFT JOIN categories c ON c.id = ml.category_id
        WHERE m.date BETWEEN $1 AND $2
          AND (ml.from_account_id = sp.id OR ml.to_account_id = sp.id)
        GROUP BY 1
        """,
        start,
        end,
    )
    return {str(row["category"]): int(row["amount"]) for row in rows}


async def _spending_from_summary(conn: Connection, month: str) -> dict[str, int]:
    summary = await recap_tools.summarize_spending(conn, group_by="category", period=month)
    return {str(group["key"]): int(group["amount"]["paise"]) for group in summary["groups"]}


async def _spending_from_recap(conn: Connection, start: date, end: date) -> dict[str, int]:
    rows = await recap_tools.spending_rows(conn, start, end)
    amounts: dict[str, int] = defaultdict(int)
    for row in rows:
        amounts[str(row["category"])] += int(row["amount_paise"])
    return {key: value for key, value in amounts.items() if value}


async def _spending_from_legs(conn: Connection, start: date, end: date) -> dict[str, int]:
    """The agent's own view: v_legs, signed toward the spending side."""
    rows = await conn.fetch(
        """
        SELECT coalesce(category_name, 'Uncategorized') AS category,
               sum(CASE WHEN to_account_kind = 'spending' THEN amount_paise
                        ELSE -amount_paise END)::bigint AS amount
        FROM v_legs
        WHERE date BETWEEN $1 AND $2
          AND (to_account_kind = 'spending' OR from_account_kind = 'spending')
        GROUP BY 1
        """,
        start,
        end,
    )
    return {str(row["category"]): int(row["amount"]) for row in rows if int(row["amount"])}


# --- e. views agree with the ledger -----------------------------------------------------------


async def _check_account_identity(conn: Connection) -> None:
    bad_names = await conn.fetch(
        """
        SELECT id
        FROM accounts
        WHERE (kind IN ('person', 'goal')) IS DISTINCT FROM (name IS NULL)
        ORDER BY id
        """
    )
    _expect(
        not bad_names,
        "account_name_kind_mismatch",
        "accounts.name must be NULL if and only if kind is person or goal",
        _ids(bad_names, "id"),
    )

    bad_goals = await conn.fetch(
        """
        SELECT g.id, g.account_id, a.kind AS account_kind
        FROM goals g
        JOIN accounts a ON a.id = g.account_id
        WHERE a.kind IS DISTINCT FROM 'goal'
        ORDER BY g.id
        """
    )
    _expect(
        not bad_goals,
        "goal_account_kind_mismatch",
        "every goal's account_id must reference an account with kind = 'goal'",
        _ids(bad_goals, "id"),
    )

    bad_payees = await conn.fetch(
        """
        SELECT p.id, p.person_account_id, a.kind AS account_kind
        FROM payees p
        JOIN accounts a ON a.id = p.person_account_id
        WHERE a.kind IS DISTINCT FROM 'person'
        ORDER BY p.id
        """
    )
    _expect(
        not bad_payees,
        "payee_account_kind_mismatch",
        "every payee's person_account_id must reference an account with kind = 'person'",
        _ids(bad_payees, "id"),
    )


async def _check_legs_view(conn: Connection) -> None:
    transfer_count = await conn.fetchval("SELECT count(*) FROM movement_legs")
    legs_count = await conn.fetchval("SELECT count(*) FROM v_legs")
    _expect(
        int(legs_count or 0) == int(transfer_count or 0),
        "legs_view_count_mismatch",
        f"v_legs has {legs_count} rows, but movement_legs has {transfer_count}",
    )

    mismatches = await conn.fetch(
        """
        WITH from_legs AS (
            SELECT
                to_char(date, 'YYYY-MM') AS month,
                sum(
                    CASE
                        WHEN to_account_kind = 'spending' THEN amount_paise
                        WHEN from_account_kind = 'spending' THEN -amount_paise
                        ELSE 0
                    END
                )::bigint AS net_paise
            FROM v_legs
            WHERE from_account_kind = 'spending' OR to_account_kind = 'spending'
            GROUP BY 1
        ),
        from_spending AS (
            SELECT
                to_char(date, 'YYYY-MM') AS month,
                sum(amount_paise)::bigint AS net_paise
            FROM v_spending
            GROUP BY 1
        )
        SELECT
            coalesce(l.month, s.month) AS month,
            coalesce(l.net_paise, 0)::bigint AS legs_net,
            coalesce(s.net_paise, 0)::bigint AS spending_net
        FROM from_legs l
        FULL OUTER JOIN from_spending s ON s.month = l.month
        WHERE coalesce(l.net_paise, 0) <> coalesce(s.net_paise, 0)
        ORDER BY 1
        """
    )
    for row in mismatches:
        _expect(
            False,
            "legs_spending_mismatch",
            f"{row['month']}: v_legs net spending={int(row['legs_net'])} differs from "
            f"v_spending={int(row['spending_net'])}",
            (str(row["month"]),),
        )


async def _check_balance_view(conn: Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT a.id, a.name,
               a.balance_paise::bigint AS account_balance,
               coalesce(last_day.balance_paise, 0)::bigint AS view_balance,
               coalesce(today_day.balance_paise, 0)::bigint AS today_view_balance
        FROM v_accounts a
        LEFT JOIN LATERAL (
            SELECT v.balance_paise
            FROM v_balances v
            WHERE v.account_id = a.id
            ORDER BY v.as_of DESC
            LIMIT 1
        ) last_day ON true
        LEFT JOIN LATERAL (
            SELECT v.balance_paise
            FROM v_balances v
            WHERE v.account_id = a.id
              AND v.as_of <= (now() AT TIME ZONE 'Asia/Kolkata')::date
            ORDER BY v.as_of DESC
            LIMIT 1
        ) today_day ON true
        """
    )
    for row in rows:
        _expect(
            int(row["account_balance"]) == int(row["view_balance"]),
            "balance_view_mismatch",
            f"v_balances ends at {int(row['view_balance'])} for {row['name']}, but "
            f"v_accounts.balance_paise is {int(row['account_balance'])}",
            (str(row["id"]),),
        )
        _expect(
            int(row["account_balance"]) == int(row["today_view_balance"]),
            "account_balance_today_mismatch",
            f"v_accounts.balance_paise is {int(row['account_balance'])} for {row['name']}, "
            f"but v_balances as of today IST says {int(row['today_view_balance'])}",
            (str(row["id"]),),
        )


async def _check_owed_view(conn: Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT a.id, a.name, p.id AS payee_id, p.display_name,
               coalesce(v.balance_paise, 0)::bigint AS view_balance,
               coalesce(sum(CASE
                   WHEN ml.to_account_id = a.id THEN ml.amount_paise
                   ELSE -ml.amount_paise
               END), 0)::bigint AS ledger_balance,
               count(*) FILTER (
                   WHERE m.kind NOT IN ('split', 'settlement', 'transfer')
               ) AS unexpected_rows
        FROM v_accounts a
        JOIN payees p ON p.person_account_id = a.id
        LEFT JOIN movement_legs ml ON ml.to_account_id = a.id OR ml.from_account_id = a.id
        LEFT JOIN movements m ON m.id = ml.movement_id
        LEFT JOIN v_owed v ON v.account_id = a.id
        GROUP BY a.id, a.name, p.id, p.display_name, v.balance_paise
        """
    )
    for row in rows:
        _expect(
            int(row["view_balance"]) == int(row["ledger_balance"]),
            "owed_view_mismatch",
            f"v_owed says {int(row['view_balance'])} for {row['display_name']}, but their "
            f"account sums to {int(row['ledger_balance'])}",
            (str(row["id"]),),
        )
        _expect(
            int(row["unexpected_rows"]) == 0,
            "owed_from_unexpected_kind",
            f"{row['display_name']}'s balance is driven by a movement kind other than split, "
            "settlement, or transfer",
            (str(row["payee_id"]),),
        )

    unlinked = await conn.fetch(
        """
        SELECT a.id
        FROM accounts a
        LEFT JOIN payees p ON p.person_account_id = a.id
        WHERE a.kind = 'person' AND p.id IS NULL
        """
    )
    for row in unlinked:
        _expect(
            False,
            "person_account_without_payee",
            f"person account {row['id']} is not owned by a payee",
            (str(row["id"]),),
        )


# --- f. schedule payment evidence -------------------------------------------------------------


async def _check_schedules(conn: Connection) -> None:
    as_of = today()
    payments = await conn.fetch(
        """
        SELECT p.id, p.occurrence_id, p.movement_id, p.amount_paise,
               o.id AS occurrence_row, o.schedule_id, o.due_date,
               m.id AS movement_row, m.date AS movement_date
        FROM schedule_payments p
        LEFT JOIN schedule_occurrences o ON o.id = p.occurrence_id
        LEFT JOIN movements m ON m.id = p.movement_id
        ORDER BY o.due_date, p.id
        """
    )
    for payment in payments:
        ids = (str(payment["id"]), str(payment["movement_id"]))
        _expect(
            int(payment["amount_paise"]) > 0,
            "schedule_payment_not_positive",
            f"schedule payment {payment['id']} allocates {int(payment['amount_paise'])} paise",
            ids,
        )
        if payment["occurrence_row"] is None or payment["movement_row"] is None:
            _expect(
                False,
                "schedule_payment_missing_row",
                f"schedule payment {payment['id']} points at an occurrence or movement that is "
                "gone",
                ids,
            )
            continue
        occurrence = await conn.fetchrow(
            "SELECT * FROM schedule_occurrences WHERE id = $1", payment["occurrence_id"]
        )
        movement = await conn.fetchrow(
            "SELECT * FROM movements WHERE id = $1", payment["movement_id"]
        )
        assert occurrence is not None and movement is not None  # the join above found both
        try:
            capacity = await _capacity(conn, occurrence, movement)
        except InvalidLedgerError as exc:
            _expect(
                False,
                "schedule_payment_capacity",
                f"schedule payment {payment['id']} no longer pays through this occurrence: {exc}",
                ids,
            )
            continue
        _expect(
            int(payment["amount_paise"]) <= capacity,
            "schedule_payment_capacity",
            f"schedule payment {payment['id']} allocates {int(payment['amount_paise'])} paise "
            f"but the movement only pays {capacity}",
            ids,
        )

    per_movement = await conn.fetch(
        """
        SELECT p.movement_id, sum(p.amount_paise)::bigint AS allocated
        FROM schedule_payments p
        GROUP BY p.movement_id
        """
    )
    for row in per_movement:
        movement = await conn.fetchrow("SELECT * FROM movements WHERE id = $1", row["movement_id"])
        if movement is None:
            continue  # already reported as schedule_payment_missing_row
        occurrences = await conn.fetch(
            """
            SELECT o.*, p.amount_paise AS allocated_paise
            FROM schedule_payments p JOIN schedule_occurrences o ON o.id = p.occurrence_id
            WHERE p.movement_id = $1
            """,
            row["movement_id"],
        )
        capacities = []
        for occurrence in occurrences:
            try:
                capacities.append(await _capacity(conn, occurrence, movement))
            except InvalidLedgerError:
                capacities.append(0)
        _expect(
            int(row["allocated"]) <= sum(capacities),
            "schedule_over_allocated",
            f"movement {row['movement_id']} has {int(row['allocated'])} paise allocated across "
            f"occurrences but pays only {sum(capacities)}",
            (str(row["movement_id"]),),
        )

    occurrences = await conn.fetch(
        "SELECT * FROM schedule_occurrences ORDER BY schedule_id, due_date"
    )
    by_schedule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        by_schedule[str(occurrence["schedule_id"])].append(occurrence)
    for schedule_id, rows in by_schedule.items():
        start = min(row["due_date"] for row in rows)
        end = max(row["due_date"] for row in rows)
        evidence = await payment_evidence(conn, schedule_id, start, end, as_of=as_of)
        effective_totals: dict[date, int] = defaultdict(int)
        for payment in payments:
            if str(payment["schedule_id"]) == schedule_id and payment["movement_date"] <= as_of:
                effective_totals[payment["due_date"]] += int(payment["amount_paise"])
        for occurrence in rows:
            day = occurrence["due_date"]
            expected = occurrence["amount_paise"]
            paid = int(evidence.get(day, {}).get("paid_paise", 0))
            effective = effective_totals[day]
            status = payment_status(expected, paid, day, as_of)
            if effective == 0:
                expected_status = "unmatched" if day < as_of else "scheduled"
            elif expected is None:
                expected_status = "recorded"
            else:
                expected_status = "partial" if effective < expected else "paid"
            ids = (str(occurrence["id"]),)
            _expect(
                paid == effective,
                "schedule_payment_evidence",
                f"occurrence {occurrence['id']} reports {paid} paid, expected {effective}",
                ids,
            )
            _expect(
                status == expected_status,
                "schedule_payment_status",
                f"occurrence {occurrence['id']} is {status}, expected {expected_status}",
                ids,
            )


# --- g. imports -------------------------------------------------------------------------------


async def _check_imports(conn: Connection) -> None:
    # A review import may point at a movement it matched: logging a payment in chat and then
    # finding it on the statement merges into the existing record, which is the point. What a
    # non-committed import must never do is create one: a line that this import booked carries
    # its own `stmt:<line>` idempotency key and its capture.
    rows = await conn.fetch(
        """
        SELECT sl.id, sl.import_id, i.status
        FROM statement_lines sl
        JOIN imports i ON i.id = sl.import_id
        WHERE i.status <> 'committed' AND sl.movement_id IS NOT NULL
          AND (
              EXISTS (
                  SELECT 1 FROM movements m
                  WHERE m.id = sl.movement_id AND m.idempotency_key = 'stmt:' || sl.id
              )
              OR EXISTS (
                  SELECT 1 FROM captures c
                  WHERE c.movement_id = sl.movement_id
                    AND c.kind = 'statement_line' AND c.ref = sl.id
              )
          )
        """
    )
    for row in rows:
        _expect(
            False,
            "movement_from_uncommitted_import",
            f"statement line {row['id']} on a {row['status']} import points at a movement",
            (str(row["id"]), str(row["import_id"])),
        )

    rows = await conn.fetch(
        """
        SELECT id, coalesce(check_status, 'missing') AS check_status
        FROM imports
        WHERE status = 'committed' AND coalesce(check_status, '') <> 'VERIFIED'
        """
    )
    for row in rows:
        _expect(
            False,
            "unverified_import_committed",
            f"import {row['id']} is committed but its check result is {row['check_status']}",
            (str(row["id"]),),
        )


# --- h. undo log ------------------------------------------------------------------------------


async def _check_undo_log(conn: Connection) -> None:
    bad_shape = await conn.fetch(
        """
        SELECT g.id
        FROM undo_log g
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements(g.snapshot) AS t(e)
            WHERE NOT (
                jsonb_typeof(t.e) = 'object'
                AND t.e ?& ARRAY['table', 'key', 'before', 'after']
                AND jsonb_typeof(t.e -> 'table') = 'string'
                AND jsonb_typeof(t.e -> 'key') = 'string'
                AND jsonb_typeof(t.e -> 'before') IN ('object', 'null')
                AND jsonb_typeof(t.e -> 'after') IN ('object', 'null')
            )
        )
        ORDER BY g.id
        """
    )
    _expect(
        not bad_shape,
        "undo_snapshot_shape",
        "every undo_log.snapshot must be an array of {table, key, before, after} objects",
        _ids(bad_shape, "id"),
    )

    backwards = await conn.fetch(
        "SELECT id FROM undo_log WHERE undone_at IS NOT NULL AND undone_at < created_at ORDER BY id"
    )
    _expect(
        not backwards,
        "undo_undone_before_created",
        "undo_log.undone_at must be null or not earlier than created_at",
        _ids(backwards, "id"),
    )
    if bad_shape:
        return

    # A live group's created rows must still exist unless a later live group deleted them.
    created = await conn.fetch(
        """
        SELECT g.id AS group_id, t.e ->> 'table' AS table_name, t.e ->> 'key' AS row_key
        FROM undo_log g
        CROSS JOIN LATERAL jsonb_array_elements(g.snapshot) AS t(e)
        WHERE g.undone_at IS NULL
          AND jsonb_typeof(t.e -> 'before') = 'null'
          AND jsonb_typeof(t.e -> 'after') = 'object'
          AND NOT EXISTS (
              SELECT 1
              FROM undo_log h
              CROSS JOIN LATERAL jsonb_array_elements(h.snapshot) AS u(e)
              WHERE h.undone_at IS NULL AND h.id <> g.id AND h.created_at >= g.created_at
                AND u.e ->> 'table' = t.e ->> 'table' AND u.e ->> 'key' = t.e ->> 'key'
                AND jsonb_typeof(u.e -> 'before') = 'object'
                AND jsonb_typeof(u.e -> 'after') = 'null'
          )
        """
    )
    by_table: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in created:
        by_table[str(row["table_name"])].append((str(row["row_key"]), str(row["group_id"])))
    key_columns = {
        str(row["table_name"]): str(row["column_name"])
        for row in await conn.fetch(
            """
            SELECT c.table_name, c.column_name
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
              AND c.column_name = CASE WHEN c.table_name = 'movement_legs'
                                       THEN 'transfer_id' ELSE 'id' END
            """
        )
    }
    for table, entries in by_table.items():
        key_column = key_columns.get(table)
        _expect(
            key_column is not None,
            "undo_unknown_table",
            f"undo_log names table {table!r}, which does not exist or has no id column",
            tuple(group_id for _, group_id in entries),
        )
        if key_column is None:
            continue
        present = {
            str(row["key"])
            for row in await conn.fetch(
                f"SELECT {quote_ident(key_column)}::text AS key FROM {quote_ident(table)} "  # noqa: S608
                f"WHERE {quote_ident(key_column)}::text = ANY($1::text[])",
                [key for key, _ in entries],
            )
        }
        for key, group_id in entries:
            _expect(
                key in present,
                "undo_created_row_missing",
                f"undo group {group_id} created {table} row {key}, which is gone and no later "
                "live group deleted it",
                (group_id, key),
            )


# --- entry point ------------------------------------------------------------------------------


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set; point it at a scratch database ending in _test")
    name = str(conninfo_to_dict(dsn).get("dbname", ""))
    if not name.endswith("_test"):
        raise SystemExit(f"Refusing to check {name!r}: its name must end in _test")
    return dsn


async def _run() -> int:
    async with connect(_dsn()) as conn:
        violations = await check_ledger(conn)
    ledger_checks = checks_run()
    violations.extend(check_engine())
    total_checks = ledger_checks + checks_run()
    for violation in violations:
        where = f" ids={','.join(violation.ids)}" if violation.ids else ""
        print(f"{violation.code}: {violation.detail}{where}")
    if violations:
        print(f"FAILED: {len(violations)} violation(s) in {total_checks} checks")
        return 1
    print(f"OK: {total_checks} checks")
    return 0


def main() -> None:
    # The project's CLI runs every async command through `asyncio.run`; on Windows that picks
    # the event loop it supports without any further policy setup.
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    sys.exit(main())
