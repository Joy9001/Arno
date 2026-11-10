"""Whole-ledger invariants: one checker every test and end-to-end run can call.

The rules are the "Financial invariants" list in `docs/arno/e2e-user-journeys.md`, derived
from the write paths in `ledger/movements.py` and `tests`' own view definitions. The two balance
checks come from `backup_restore.verify_database`, which the restore drill already relies on, so
there is one definition of "the ledger balances".

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
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from arno.backup_restore import verify_database
from arno.db.conn import Connection, connect, conninfo_to_dict
from arno.ledger.common import today
from arno.ledger.errors import InvalidLedgerError
from arno.ledger.movements import _reversal_state
from arno.ledger.schedule_payments import _capacity, payment_evidence, payment_status
from arno.tools import read as read_tools
from arno.tools import recaps as recap_tools

SYSTEM_KINDS = {"spending", "income", "equity"}


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str
    ids: tuple[str, ...] = ()


@dataclass
class Posting:
    transfer_id: str
    movement_id: str | None
    from_id: str | None
    from_kind: str | None
    to_id: str | None
    to_kind: str | None
    amount_paise: int
    label_movement_id: str | None
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
    reverses_id: str | None
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
    """How many individual assertions the last `check_ledger` call made."""
    return _checks_run


def _expect(ok: bool, code: str, detail: str, ids: tuple[str, ...] = ()) -> None:
    global _checks_run
    _checks_run += 1
    if not ok:
        _violations.append(Violation(code=code, detail=detail, ids=ids))


def _ids(rows: list[dict[str, Any]], key: str) -> tuple[str, ...]:
    return tuple(str(row[key]) for row in rows)


async def check_ledger(conn: Connection) -> list[Violation]:
    """Every financial invariant that can be checked from the database alone."""
    global _checks_run, _violations
    _checks_run = 0
    _violations = []

    movements, postings = await _load_ledger(conn)
    await _check_balances(conn)
    _check_transfer_shapes(movements, postings)
    _check_movement_postings(movements)
    await _check_reversals(conn, movements)
    await _check_cross_paths(conn)
    await _check_balance_view(conn)
    await _check_owed_view(conn)
    await _check_schedules(conn)
    await _check_imports(conn)
    return list(_violations)


async def _load_ledger(
    conn: Connection,
) -> tuple[dict[str, Movement], list[Posting]]:
    movement_rows = await conn.fetch(
        "SELECT id, date, kind, reverses_id FROM movements ORDER BY date, id"
    )
    movements = {
        str(row["id"]): Movement(
            id=str(row["id"]),
            date=row["date"],
            kind=str(row["kind"]),
            reverses_id=row["reverses_id"],
        )
        for row in movement_rows
    }
    rows = await conn.fetch(
        """
        SELECT
            t.id AS transfer_id,
            t.metadata ->> 'movement_id' AS movement_id,
            t.amount::bigint AS amount_paise,
            fa.id AS from_id, fa.kind AS from_kind, fa.name AS from_name,
            ta.id AS to_id, ta.kind AS to_kind, ta.name AS to_name,
            tl.movement_id AS label_movement_id,
            tl.category_id, c.name AS category_name,
            tl.payee_id, p.kind AS payee_kind, p.person_account_id AS payee_person_account_id
        FROM pgledger_transfers t
        LEFT JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        LEFT JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        LEFT JOIN transfer_labels tl ON tl.transfer_id = t.id
        LEFT JOIN categories c ON c.id = tl.category_id
        LEFT JOIN payees p ON p.id = tl.payee_id
        ORDER BY t.id
        """
    )
    postings = [
        Posting(
            transfer_id=str(row["transfer_id"]),
            movement_id=row["movement_id"],
            from_id=row["from_id"],
            from_kind=row["from_kind"],
            to_id=row["to_id"],
            to_kind=row["to_kind"],
            amount_paise=int(row["amount_paise"]),
            label_movement_id=row["label_movement_id"],
            category_id=row["category_id"],
            category_name=row["category_name"],
            payee_id=row["payee_id"],
            payee_kind=row["payee_kind"],
            payee_person_account_id=row["payee_person_account_id"],
        )
        for row in rows
    ]
    for posting in postings:
        movement = movements.get(str(posting.movement_id))
        if movement is not None:
            movement.postings.append(posting)
    return movements, postings


# --- a. balances ------------------------------------------------------------------------------


async def _check_balances(conn: Connection) -> None:
    """Reuse the restore drill's two ledger checks, then confirm entries match their transfer."""
    checks, mismatches = await verify_database(
        conn, manifest=None, dump_sha256=None, require_manifest=False
    )
    _expect(
        checks.get("ledger_transfers_balance", False),
        "ledger_transfer_unbalanced",
        "a transfer's entries do not sum to zero",
        await _unbalanced_transfer_ids(conn),
    )
    _expect(
        checks.get("account_balances_match_entries", False),
        "account_balance_mismatch",
        "pgledger_accounts.balance differs from the sum of that account's entries",
        await _drifted_account_ids(conn),
    )
    if mismatches:
        _expect(False, "backup_verification", "; ".join(mismatches))

    rows = await conn.fetch(
        """
        SELECT t.id, t.amount::bigint AS amount,
               count(e.id) AS entries,
               count(*) FILTER (
                   WHERE e.account_id = t.from_account_id AND e.amount = -t.amount
               ) AS from_ok,
               count(*) FILTER (
                   WHERE e.account_id = t.to_account_id AND e.amount = t.amount
               ) AS to_ok
        FROM pgledger_transfers t
        LEFT JOIN pgledger_entries e ON e.transfer_id = t.id
        GROUP BY t.id, t.amount
        """
    )
    for row in rows:
        identifier = str(row["id"])
        _expect(
            int(row["entries"]) == 2 and int(row["from_ok"]) == 1 and int(row["to_ok"]) == 1,
            "transfer_entries_wrong",
            f"transfer {identifier} needs exactly one -amount entry on the payer and one "
            f"+amount entry on the payee",
            (identifier,),
        )


async def _unbalanced_transfer_ids(conn: Connection) -> tuple[str, ...]:
    rows = await conn.fetch(
        """
        SELECT e.transfer_id
        FROM pgledger_entries e
        GROUP BY e.transfer_id
        HAVING sum(e.amount) <> 0
        """
    )
    return _ids(rows, "transfer_id")


async def _drifted_account_ids(conn: Connection) -> tuple[str, ...]:
    rows = await conn.fetch(
        """
        SELECT a.id
        FROM pgledger_accounts a
        LEFT JOIN (
            SELECT account_id, sum(amount) AS total FROM pgledger_entries GROUP BY account_id
        ) e ON e.account_id = a.id
        WHERE coalesce(e.total, 0) <> a.balance
        """
    )
    return _ids(rows, "id")


# --- b. posting rules -------------------------------------------------------------------------


def _check_transfer_shapes(movements: dict[str, Movement], postings: list[Posting]) -> None:
    for posting in postings:
        ids = (posting.transfer_id,)
        _expect(
            posting.from_id is not None and posting.to_id is not None,
            "transfer_without_account",
            f"transfer {posting.transfer_id} has a ledger side with no accounts row",
            ids,
        )
        _expect(
            posting.movement_id is not None,
            "transfer_without_movement",
            f"transfer {posting.transfer_id} carries no movement_id in metadata",
            ids,
        )
        if posting.movement_id is not None:
            _expect(
                posting.movement_id in movements,
                "transfer_movement_missing",
                f"transfer {posting.transfer_id} points at movement {posting.movement_id}, "
                "which does not exist",
                ids,
            )
        if posting.label_movement_id is not None:
            _expect(
                posting.label_movement_id == posting.movement_id,
                "label_movement_mismatch",
                f"label on transfer {posting.transfer_id} belongs to movement "
                f"{posting.label_movement_id}, not {posting.movement_id}",
                ids,
            )
    for movement in movements.values():
        _expect(
            bool(movement.postings),
            "movement_without_transfer",
            f"movement {movement.id} ({movement.kind}) has no transfers",
            (movement.id,),
        )


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
        elif movement.kind == "reversal":
            _expect(
                movement.reverses_id is not None,
                "reversal_without_original",
                f"reversal {movement.id} does not name the movement it reverses",
                ids,
            )


# --- c. reversals -----------------------------------------------------------------------------


def _signature(postings: list[Posting]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            posting.from_id or "",
            posting.to_id or "",
            posting.amount_paise,
            posting.category_id or "",
        )
        for posting in postings
    )


def _opposite(postings: list[Posting]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            posting.to_id or "",
            posting.from_id or "",
            posting.amount_paise,
            posting.category_id or "",
        )
        for posting in postings
    )


async def _check_reversals(conn: Connection, movements: dict[str, Movement]) -> None:
    children: dict[str, list[str]] = defaultdict(list)
    for movement in movements.values():
        if movement.reverses_id:
            children[str(movement.reverses_id)].append(movement.id)

    for movement in movements.values():
        if movement.kind != "reversal":
            continue
        original = movements.get(str(movement.reverses_id))
        _expect(
            original is not None,
            "reversal_original_missing",
            f"reversal {movement.id} points at a missing movement",
            (movement.id,),
        )
        if original is None:
            continue
        _expect(
            _signature(movement.postings) == _opposite(original.postings),
            "reversal_not_opposite",
            f"reversal {movement.id} is not the exact opposite of {original.id}",
            (movement.id, original.id),
        )

    roots = list(children)
    states = await _reversal_state(conn, roots)
    chain_by_root = await _reversal_chains(conn, roots)
    for root, state in states.items():
        if not state["reversed"]:
            continue
        chain = [root, *chain_by_root.get(root, [])]
        net = await conn.fetchval(
            "SELECT coalesce(sum(amount_paise), 0)::bigint FROM v_spending "
            "WHERE movement_id = ANY($1::text[])",
            chain,
        )
        assert net is not None  # coalesce(sum(...), 0) always returns one value
        _expect(
            int(net) == 0,
            "reversed_pair_not_net_zero",
            f"the reversed movement {root} and its reversals move {int(net)} paise through "
            "Spending; effective results must net to zero",
            tuple(chain),
        )
        rows = await conn.fetch(
            """
            SELECT e.account_id, sum(e.amount)::bigint AS net
            FROM pgledger_entries e
            JOIN pgledger_transfers t ON t.id = e.transfer_id
            WHERE t.metadata ->> 'movement_id' = ANY($1::text[])
            GROUP BY e.account_id
            HAVING sum(e.amount) <> 0
            """,
            chain,
        )
        for row in rows:
            _expect(
                False,
                "reversed_pair_not_net_zero",
                f"reversed chain {root} leaves {int(row['net'])} paise on account "
                f"{row['account_id']}; v_balances and v_owed must exclude the pair's effect",
                (root, str(row["account_id"])),
            )


async def _reversal_chains(conn: Connection, roots: list[str]) -> dict[str, list[str]]:
    if not roots:
        return {}
    rows = await conn.fetch(
        """
        WITH RECURSIVE descendants AS (
            SELECT m.id AS root, m.id AS node FROM movements m WHERE m.id = ANY($1::text[])
            UNION ALL
            SELECT d.root, child.id
            FROM descendants d JOIN movements child ON child.reverses_id = d.node
        )
        SELECT root, node FROM descendants WHERE root <> node
        """,
        roots,
    )
    result: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        result[str(row["root"])].append(str(row["node"]))
    return result


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
        from_movements, listed_ids, reversal_kinds = await _spending_from_query(
            conn, month, start, end
        )
        effective = await read_tools._reversed_ids(conn)
        paths = {
            "v_spending": from_view,
            "summarize_spending": from_summary,
            "recap spending_rows": from_recap,
            "query_movements": from_movements,
        }
        for category in sorted(set().union(*paths.values())):
            values = {name: amounts.get(category, 0) for name, amounts in paths.items()}
            _expect(
                len(set(values.values())) == 1,
                "cross_path_spending_mismatch",
                f"{month}/{category}: " + ", ".join(f"{k}={v}" for k, v in values.items()),
            )
        _expect(
            not reversal_kinds,
            "reversal_in_effective_rows",
            f"query_movements for {month} returned reversal movements: {sorted(reversal_kinds)}",
        )
        _expect(
            not (set(listed_ids) & set(effective)),
            "reversed_movement_in_effective_rows",
            f"query_movements for {month} returned movements a live reversal cancels",
            tuple(sorted(set(listed_ids) & set(effective))),
        )


def _month_end(start: date) -> date:
    if start.month == 12:
        return date(start.year + 1, 1, 1) - date.resolution
    return date(start.year, start.month + 1, 1) - date.resolution


async def _spending_from_view(conn: Connection, start: date, end: date) -> dict[str, int]:
    rows = await conn.fetch(
        """
        SELECT coalesce(c.name, 'Uncategorized') AS category,
               sum(CASE WHEN t.to_account_id = sp.ledger_account_id
                        THEN t.amount ELSE -t.amount END)::bigint AS amount
        FROM pgledger_transfers t
        JOIN movements m ON m.id = t.metadata ->> 'movement_id'
        JOIN accounts sp ON sp.kind = 'spending'
        LEFT JOIN transfer_labels tl ON tl.transfer_id = t.id
        LEFT JOIN categories c ON c.id = tl.category_id
        WHERE m.date BETWEEN $1 AND $2
          AND (t.from_account_id = sp.ledger_account_id
               OR t.to_account_id = sp.ledger_account_id)
        GROUP BY 1
        """,
        start,
        end,
    )
    return {str(row["category"]): int(row["amount"]) for row in rows}


async def _spending_from_summary(conn: Connection, month: str) -> dict[str, int]:
    summary = await read_tools.summarize_spending(conn, group_by="category", period=month)
    return {str(group["key"]): int(group["amount"]["paise"]) for group in summary["groups"]}


async def _spending_from_recap(conn: Connection, start: date, end: date) -> dict[str, int]:
    rows = await recap_tools.spending_rows(conn, start, end)
    amounts: dict[str, int] = defaultdict(int)
    for row in rows:
        amounts[str(row["category"])] += int(row["amount_paise"])
    return {key: value for key, value in amounts.items() if value}


async def _spending_from_query(
    conn: Connection, month: str, start: date, end: date
) -> tuple[dict[str, int], set[str], set[str]]:
    amounts: dict[str, int] = defaultdict(int)
    listed: set[str] = set()
    reversal_kinds: set[str] = set()
    cursor: str | None = None
    while True:
        page = await read_tools.query_movements(
            conn, filters={"period": month}, limit=200, cursor=cursor
        )
        for row in page["rows"]:
            listed.add(str(row["id"]))
            if row["kind"] == "reversal":
                reversal_kinds.add(str(row["id"]))
            for transfer in row["transfers"]:
                if transfer["from_kind"] == "spending":
                    amounts[str(transfer.get("category_name") or "Uncategorized")] -= int(
                        transfer["amount_paise"]
                    )
                if transfer["to_kind"] == "spending":
                    amounts[str(transfer.get("category_name") or "Uncategorized")] += int(
                        transfer["amount_paise"]
                    )
        cursor = page["next_cursor"]
        if not cursor:
            break
    return (
        {key: value for key, value in amounts.items() if value},
        listed,
        reversal_kinds,
    )


# --- e. views agree with the ledger -----------------------------------------------------------


async def _check_balance_view(conn: Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT a.id, a.name, pg.balance::bigint AS ledger_balance,
               coalesce(last_day.balance_paise, 0)::bigint AS view_balance
        FROM accounts a
        JOIN pgledger_accounts pg ON pg.id = a.ledger_account_id
        LEFT JOIN LATERAL (
            SELECT v.balance_paise
            FROM v_balances v
            WHERE v.account_id = a.id
            ORDER BY v.as_of DESC
            LIMIT 1
        ) last_day ON true
        """
    )
    for row in rows:
        _expect(
            int(row["ledger_balance"]) == int(row["view_balance"]),
            "balance_view_mismatch",
            f"v_balances ends at {int(row['view_balance'])} for {row['name']}, but pgledger "
            f"says {int(row['ledger_balance'])}",
            (str(row["id"]),),
        )


async def _check_owed_view(conn: Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT a.id, a.name, p.id AS payee_id, p.display_name,
               coalesce(v.balance_paise, 0)::bigint AS view_balance,
               coalesce(sum(CASE
                   WHEN t.to_account_id = a.ledger_account_id THEN t.amount
                   ELSE -t.amount
               END), 0)::bigint AS ledger_balance,
               count(*) FILTER (
                   WHERE m.kind NOT IN ('split', 'settlement', 'transfer', 'reversal')
               ) AS unexpected_rows
        FROM accounts a
        JOIN payees p ON p.person_account_id = a.id
        LEFT JOIN pgledger_entries e ON e.account_id = a.ledger_account_id
        LEFT JOIN pgledger_transfers t ON t.id = e.transfer_id
        LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
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
            "settlement, transfer, or reversal",
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
    payments = await conn.fetch(
        """
        SELECT p.id, p.occurrence_id, p.movement_id, p.amount_paise,
               o.schedule_id, o.due_date, o.amount_paise AS expected_paise,
               o.account_id, m.kind AS movement_kind, m.date AS movement_date
        FROM schedule_payments p
        JOIN schedule_occurrences o ON o.id = p.occurrence_id
        JOIN movements m ON m.id = p.movement_id
        ORDER BY o.due_date, p.id
        """
    )
    for payment in payments:
        ids = (str(payment["id"]), str(payment["movement_id"]))
        _expect(
            payment["movement_kind"] != "reversal",
            "schedule_payment_on_reversal",
            f"schedule payment {payment['id']} is allocated to reversal {payment['movement_id']}",
            ids,
        )
        if payment["movement_kind"] == "reversal":
            continue
        occurrence = await conn.fetchrow(
            "SELECT * FROM schedule_occurrences WHERE id = $1", payment["occurrence_id"]
        )
        movement = await conn.fetchrow(
            "SELECT * FROM movements WHERE id = $1", payment["movement_id"]
        )
        if occurrence is None or movement is None:
            _expect(
                False,
                "schedule_payment_missing_row",
                f"schedule payment {payment['id']} points at an occurrence or movement that is "
                "gone",
                ids,
            )
            continue
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
        JOIN movements m ON m.id = p.movement_id AND m.kind <> 'reversal'
        GROUP BY p.movement_id
        """
    )
    for row in per_movement:
        movement = await conn.fetchrow("SELECT * FROM movements WHERE id = $1", row["movement_id"])
        if movement is None:
            _expect(
                False,
                "schedule_payment_missing_row",
                f"movement {row['movement_id']} has allocations but no movements row",
                (str(row["movement_id"]),),
            )
            continue
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
        evidence = await payment_evidence(conn, schedule_id, start, end)
        payments_by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for payment in payments:
            if str(payment["schedule_id"]) == schedule_id:
                payments_by_day[payment["due_date"]].append(payment)
        for occurrence in rows:
            day = occurrence["due_date"]
            paid = evidence.get(day, {}).get("paid_paise", 0)
            expected = occurrence["amount_paise"]
            status = payment_status(
                int(expected) if expected is not None else None, int(paid), day, today()
            )
            active = payments_by_day[day]
            if status == "paid":
                _expect(
                    bool(active) and int(paid) >= int(expected),
                    "schedule_paid_without_payment",
                    f"occurrence {occurrence['id']} is paid but has no covering payment",
                    (str(occurrence["id"]),),
                )
            if active and expected is not None and int(paid) >= int(expected):
                _expect(
                    status == "paid",
                    "schedule_payment_not_paid",
                    f"occurrence {occurrence['id']} covers its {int(expected)} paise but is "
                    f"{status}",
                    (str(occurrence["id"]),),
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
        SELECT id, coalesce(check_result ->> 'status', 'missing') AS check_status
        FROM imports
        WHERE status = 'committed' AND coalesce(check_result ->> 'status', '') <> 'VERIFIED'
        """
    )
    for row in rows:
        _expect(
            False,
            "unverified_import_committed",
            f"import {row['id']} is committed but its check result is {row['check_status']}",
            (str(row["id"]),),
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
    for violation in violations:
        where = f" ids={','.join(violation.ids)}" if violation.ids else ""
        print(f"{violation.code}: {violation.detail}{where}")
    if violations:
        print(f"FAILED: {len(violations)} violation(s) in {checks_run()} checks")
        return 1
    print(f"OK: {checks_run()} checks")
    return 0


def main() -> None:
    # The project's CLI runs every async command through `asyncio.run`; on Windows that picks
    # the event loop it supports without any further policy setup.
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    sys.exit(main())
