"""The ledger invariants checker must pass on real ledgers and catch planted breakage.

The demo persona proves the normal write paths agree. The random sequence drives the agent's real
write tools and re-checks the whole ledger after every operation, so a correction or undo that
leaves the books in a state no single number notices still fails. The planted cases prove each
family of rule actually fires: a checker that never fails is worthless.
"""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from typing import Any

from arno.ledger.accounts import create_account
from arno.ledger.common import today as ledger_today
from arno.ledger.errors import LedgerError
from arno.ledger.movements import record_expense
from arno.ledger.payees import create_payee
from arno.ledger.schedules import create_schedule
from arno.tools import write as write_tools
from tests.invariants import Violation, check_ledger

CATEGORIES = ("Food & Dining", "Groceries", "Transport", "Shopping")


def _clean(violations: list[Violation]) -> None:
    assert violations == [], "\n".join(
        f"{violation.code}: {violation.detail} ids={violation.ids}" for violation in violations
    )


async def test_demo_persona_has_no_violations(demo_ledger):
    _clean(await check_ledger(demo_ledger))


async def test_undo_keeps_the_reversed_pair_categorised(ledger):
    bank = await create_account(
        ledger, kind="bank", name="Undo bank", opening_balance_paise=10_000_000
    )
    expense = await write_tools.record_movement(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": "100.00",
            "account": bank["name"],
            "description": "Undo me",
            "category": "Food & Dining",
            "date": ledger_today().isoformat(),
        },
        message_id=None,
        conversation_id=None,
        not_duplicate=True,
    )
    await write_tools.undo(
        ledger, undo_id=expense["undo_id"], conversation_id=None, message_id=None
    )
    _clean(await check_ledger(ledger))


# --- a seeded random sequence through the real write tools ------------------------------------


class _Books:
    """A small, randomisable ledger over the agent's real write functions."""

    def __init__(self, conn, rng: random.Random) -> None:
        self.conn = conn
        self.rng = rng
        self.accounts: dict[str, str] = {}
        self.records: list[dict[str, Any]] = []
        self.kinds: list[str] = []
        self.linked: set[str] = set()
        self.schedule_id: str = ""
        self.due_date: date = date.min

    async def setup(self) -> None:
        self.accounts["bank"] = (
            await create_account(
                self.conn, kind="bank", name="Random bank", opening_balance_paise=50_000_000
            )
        )["id"]
        self.accounts["second"] = (
            await create_account(
                self.conn, kind="bank", name="Second bank", opening_balance_paise=10_000_000
            )
        )["id"]
        self.accounts["card"] = (
            await create_account(
                self.conn,
                kind="card",
                name="Random card",
                credit_limit_paise=2_000_000,
                statement_day=5,
                due_day=25,
            )
        )["id"]
        self.accounts["loan"] = (
            await create_account(
                self.conn,
                kind="loan",
                name="Random loan",
                opening_balance_paise=-10_000_000,
                rate_bps=1200,
            )
        )["id"]
        self.accounts["asset"] = (
            await create_account(self.conn, kind="asset", name="Random fund")
        )["id"]
        for person in ("Ravi", "Amit"):
            await create_payee(self.conn, display_name=person, kind="person")
        self.due_date = ledger_today() - timedelta(days=30)
        self.schedule_id = (
            await create_schedule(
                self.conn,
                kind="obligation",
                subtype="other",
                name="Random bill",
                amount_paise=100_000,
                account_id=self.accounts["bank"],
                category_id=await self._category("Utilities"),
                rule="FREQ=DAILY;COUNT=1",
                starts=self.due_date,
            )
        )["id"]

    def _date(self) -> date:
        return self.rng.choice(
            [ledger_today() - timedelta(days=days) for days in (0, 1, 3, 7, 15, 30, 31, 45, 60)]
        )

    def _rupees(self, low: int, high: int) -> str:
        return f"{self.rng.randint(low, high) / 100:.2f}"

    async def _category(self, name: str) -> str | None:
        row = await self.conn.fetchrow("SELECT id FROM categories WHERE name = $1", name)
        return str(row["id"]) if row else None

    async def _record(self, movement: dict[str, Any]) -> dict[str, Any]:
        result = await write_tools.record_movement(
            self.conn,
            movement,
            message_id=None,
            conversation_id=None,
            not_duplicate=True,
        )
        self.records.append(
            {
                "id": result["movement_id"],
                "kind": movement["kind"],
                "undo_id": result["undo_id"],
                "amount_paise": self._amount_paise(result),
                "active": True,
            }
        )
        self.kinds.append(movement["kind"])
        return result

    def _amount_paise(self, result: dict[str, Any]) -> int:
        return sum(int(transfer["amount_paise"]) for transfer in result.get("transfers", []))

    async def record_expense(self) -> None:
        await self._record(
            {
                "kind": "expense",
                "amount_rupees": self._rupees(100, 500_000),
                "account": self.rng.choice(["Random bank", "Second bank", "Random card"]),
                "description": "Random expense",
                "payee": self.rng.choice(["Cafe Random", "Book Store"]),
                "category": self.rng.choice(CATEGORIES),
                "date": self._date().isoformat(),
            }
        )

    async def record_income(self) -> None:
        await self._record(
            {
                "kind": "income",
                "amount_rupees": self._rupees(100, 500_000),
                "account": self.rng.choice(["Random bank", "Second bank"]),
                "description": "Random income",
                "payee": "Acme Random",
                "date": self._date().isoformat(),
            }
        )

    async def record_transfer(self) -> None:
        source, target = self.rng.choice(
            [
                ("Random bank", "Second bank"),
                ("Second bank", "Random bank"),
                ("Random bank", "Random card"),
                ("Second bank", "Random card"),
            ]
        )
        await self._record(
            {
                "kind": "transfer",
                "amount_rupees": self._rupees(100, 500_000),
                "from_account": source,
                "to_account": target,
                "description": "Random transfer",
                "date": self._date().isoformat(),
            }
        )

    async def record_split(self) -> None:
        mine = self.rng.randint(1000, 50_000)
        ravi = self.rng.randint(1000, 50_000)
        amit = self.rng.randint(1000, 50_000)
        await self._record(
            {
                "kind": "split",
                "total_rupees": f"{(mine + ravi + amit) / 100:.2f}",
                "my_share_rupees": f"{mine / 100:.2f}",
                "people": [
                    {"name": "Ravi", "amount_rupees": f"{ravi / 100:.2f}"},
                    {"name": "Amit", "amount_rupees": f"{amit / 100:.2f}"},
                ],
                "account": self.rng.choice(["Random bank", "Second bank"]),
                "description": "Random split",
                "payee": "Cafe Random",
                "category": self.rng.choice(CATEGORIES),
                "date": self._date().isoformat(),
            }
        )

    async def record_loan(self) -> None:
        await self._record(
            {
                "kind": "loan",
                "amount_rupees": self._rupees(1_000, 100_000),
                "account": self.rng.choice(["Random bank", "Second bank"]),
                "loan": "Random loan",
                "description": "Random EMI",
                "date": self._date().isoformat(),
            }
        )

    async def record_refund(self) -> None:
        await self._record(
            {
                "kind": "refund",
                "amount_rupees": self._rupees(100, 50_000),
                "account": self.rng.choice(["Random bank", "Random card"]),
                "description": "Random refund",
                "payee": "Cafe Random",
                "category": self.rng.choice(CATEGORIES),
                "date": self._date().isoformat(),
            }
        )

    async def record_settlement(self) -> None:
        await self._record(
            {
                "kind": "settlement",
                "amount_rupees": self._rupees(100, 50_000),
                "account": self.rng.choice(["Random bank", "Second bank"]),
                "payee": self.rng.choice(["Ravi", "Amit"]),
                "direction": self.rng.choice(["they_paid", "i_paid"]),
                "description": "Random settlement",
                "date": self._date().isoformat(),
            }
        )

    async def correct(self) -> None:
        candidates = [record for record in self.records if record["active"]]
        if not candidates:
            return
        record = self.rng.choice(candidates)
        if record["kind"] in {"expense", "income", "transfer", "refund", "settlement"} and (
            self.rng.random() < 0.6
        ):
            changes = {"amount_rupees": self._rupees(100, 200_000)}
        else:
            changes = {"category": self.rng.choice(CATEGORIES)}
        result = await write_tools.correct_movement(
            self.conn, movement_id=record["id"], changes=changes, message_id=None
        )
        record["active"] = False
        self.records.append(
            {
                "id": result["movement"]["id"],
                "kind": result["movement"]["kind"],
                "undo_id": result["undo_id"],
                "amount_paise": int(result["movement"]["amount_paise"]),
                "active": True,
            }
        )

    async def undo(self) -> None:
        candidates = [record for record in self.records if record["active"]]
        if not candidates:
            return
        record = self.rng.choice(candidates)
        await write_tools.undo(
            self.conn, undo_id=record["undo_id"], conversation_id=None, message_id=None
        )
        record["active"] = False

    async def match_schedule(self) -> None:
        candidates = [
            record
            for record in self.records
            if record["active"] and record["kind"] == "expense" and record["id"] not in self.linked
        ]
        if not candidates:
            return
        record = self.rng.choice(candidates)
        await write_tools.schedule_payment(
            self.conn,
            schedule=self.schedule_id,
            due_date=self.due_date,
            movement_id=record["id"],
            action="link",
        )
        self.linked.add(record["id"])


async def _run_sequence(
    conn, *, seed: int, count: int, include_undo: bool, min_applied: int
) -> tuple[_Books, int]:
    rng = random.Random(seed)
    books = _Books(conn, rng)
    await books.setup()
    _clean(await check_ledger(conn))

    sweep = (
        books.record_expense,
        books.record_income,
        books.record_transfer,
        books.record_split,
        books.record_loan,
        books.record_refund,
        books.record_settlement,
    )
    for operation in sweep:
        await operation()
        _clean(await check_ledger(conn))

    operations = [
        books.record_expense,
        books.record_income,
        books.record_transfer,
        books.record_split,
        books.record_loan,
        books.record_refund,
        books.record_settlement,
        books.correct,
        books.correct,
        books.match_schedule,
    ]
    if include_undo:
        operations.append(books.undo)
    applied = 0
    for index in range(count):
        operation = rng.choice(operations)
        try:
            await operation()
            applied += 1
        except LedgerError, write_tools.ToolInputError:
            # Some writes are legitimately refused after an undo or correction; the invariant
            # under test is that nothing half-applied.
            continue
        violations = await check_ledger(conn)
        assert violations == [], "\n".join(
            [f"after operation {index} ({operation.__name__})"]
            + [f"{violation.code}: {violation.detail}" for violation in violations]
        )

    assert applied > min_applied
    assert set(books.kinds) == {
        "expense",
        "income",
        "transfer",
        "split",
        "loan",
        "refund",
        "settlement",
    }
    return books, applied


async def test_random_operations_keep_the_invariants(ledger):
    await _run_sequence(ledger, seed=20261109, count=200, include_undo=True, min_applied=150)


async def test_random_operations_without_undo_keep_the_invariants(ledger):
    await _run_sequence(ledger, seed=20261110, count=80, include_undo=False, min_applied=60)


# --- planted violations: every family of check must fire --------------------------------------


async def _broken_transfer(
    conn,
    *,
    kind: str,
    from_account_id: str,
    to_account_id: str,
    amount_paise: int,
    reverses_id: str | None = None,
    when: date | None = None,
) -> str:
    """Insert a balanced movement whose shape breaks one kind's posting rule."""
    when = when or ledger_today()
    from_row = await conn.fetchrow(
        "SELECT ledger_account_id FROM accounts WHERE id = $1", from_account_id
    )
    to_row = await conn.fetchrow(
        "SELECT ledger_account_id FROM accounts WHERE id = $1", to_account_id
    )
    movement_id = f"mov_test_{uuid.uuid4().hex}"
    transfer_id = f"pglt_test_{uuid.uuid4().hex}"
    await conn.execute(
        """
        INSERT INTO movements (id, date, description, kind, reverses_id)
        VALUES ($1, $2, $3, $4, $5)
        """,
        movement_id,
        when,
        "Planted broken movement",
        kind,
        reverses_id,
    )
    await conn.execute(
        """
        INSERT INTO pgledger_transfers
            (id, from_account_id, to_account_id, amount, created_at, event_at)
        VALUES ($1, $2, $3, $4, now(), now())
        """,
        transfer_id,
        from_row["ledger_account_id"],
        to_row["ledger_account_id"],
        amount_paise,
    )
    await conn.execute(
        """
        INSERT INTO movement_legs (transfer_id, movement_id)
        VALUES ($1, $2)
        """,
        transfer_id,
        movement_id,
    )
    for ledger_id, signed in (
        (from_row["ledger_account_id"], -amount_paise),
        (to_row["ledger_account_id"], amount_paise),
    ):
        account = await conn.fetchrow(
            "SELECT balance, version FROM pgledger_accounts WHERE id = $1 FOR UPDATE", ledger_id
        )
        previous = int(account["balance"])
        version = int(account["version"]) + 1
        await conn.execute(
            """
            INSERT INTO pgledger_entries
                (account_id, transfer_id, amount, account_previous_balance,
                 account_current_balance, account_version, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            """,
            ledger_id,
            transfer_id,
            signed,
            previous,
            previous + signed,
            version,
        )
        await conn.execute(
            "UPDATE pgledger_accounts SET balance = $1, version = $2, updated_at = now() "
            "WHERE id = $3",
            previous + signed,
            version,
            ledger_id,
        )
    return movement_id


async def _in_savepoint(conn, mutate) -> list[Violation]:
    transaction = conn.transaction()
    await transaction.start()
    try:
        await mutate()
        return await check_ledger(conn)
    finally:
        await transaction.rollback()


def _codes(violations: list[Violation]) -> set[str]:
    return {violation.code for violation in violations}


async def test_each_invariant_is_caught(ledger):
    bank = await create_account(
        ledger, kind="bank", name="Case bank", opening_balance_paise=10_000_000
    )
    card = await create_account(
        ledger,
        kind="card",
        name="Case card",
        credit_limit_paise=2_000_000,
        statement_day=5,
        due_day=25,
    )
    asset = await create_account(ledger, kind="asset", name="Case fund")
    person = await create_payee(ledger, display_name="Ravi", kind="person")
    expense = await write_tools.record_movement(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": "100.00",
            "account": "Case bank",
            "description": "Case expense",
            "category": "Food & Dining",
            "date": ledger_today().isoformat(),
        },
        message_id=None,
        conversation_id=None,
        not_duplicate=True,
    )
    expense_id = expense["movement_id"]
    spending = await ledger.fetchrow("SELECT id FROM accounts WHERE kind = 'spending'")
    income = await ledger.fetchrow("SELECT id FROM accounts WHERE kind = 'income'")
    equity = await ledger.fetchrow("SELECT id FROM accounts WHERE kind = 'equity'")
    person_account = str(person["person_account_id"])

    async def unbalanced():
        transfer = await ledger.fetchrow(
            "SELECT transfer_id AS id FROM movement_legs WHERE movement_id = $1",
            expense_id,
        )
        await ledger.execute(
            "UPDATE pgledger_entries SET amount = amount + 1 WHERE transfer_id = $1",
            transfer["id"],
        )

    async def expense_shape():
        await _broken_transfer(
            ledger,
            kind="expense",
            from_account_id=bank["id"],
            to_account_id=str(income["id"]),
            amount_paise=1_000,
        )

    async def income_shape():
        await _broken_transfer(
            ledger,
            kind="income",
            from_account_id=bank["id"],
            to_account_id=str(asset["id"]),
            amount_paise=1_000,
        )

    async def transfer_shape():
        await _broken_transfer(
            ledger,
            kind="transfer",
            from_account_id=bank["id"],
            to_account_id=str(spending["id"]),
            amount_paise=1_000,
        )

    async def split_shape():
        await _broken_transfer(
            ledger,
            kind="split",
            from_account_id=bank["id"],
            to_account_id=str(asset["id"]),
            amount_paise=1_000,
        )

    async def loan_shape():
        await _broken_transfer(
            ledger,
            kind="loan",
            from_account_id=bank["id"],
            to_account_id=str(asset["id"]),
            amount_paise=1_000,
        )

    async def refund_shape():
        await _broken_transfer(
            ledger,
            kind="refund",
            from_account_id=bank["id"],
            to_account_id=str(asset["id"]),
            amount_paise=1_000,
        )

    async def settlement_shape():
        await _broken_transfer(
            ledger,
            kind="settlement",
            from_account_id=bank["id"],
            to_account_id=str(asset["id"]),
            amount_paise=1_000,
        )

    async def adjustment_shape():
        await _broken_transfer(
            ledger,
            kind="adjustment",
            from_account_id=bank["id"],
            to_account_id=str(asset["id"]),
            amount_paise=1_000,
        )

    async def reversal_shape():
        await _broken_transfer(
            ledger,
            kind="reversal",
            from_account_id=bank["id"],
            to_account_id=str(spending["id"]),
            amount_paise=10_000,
            reverses_id=expense_id,
        )

    async def reversed_pair_net():
        reversal = await _broken_transfer(
            ledger,
            kind="reversal",
            from_account_id=str(spending["id"]),
            to_account_id=bank["id"],
            amount_paise=10_000,
            reverses_id=expense_id,
        )
        reversal_transfer = await ledger.fetchrow(
            "SELECT transfer_id AS id FROM movement_legs WHERE movement_id = $1", reversal
        )
        account = await ledger.fetchrow(
            "SELECT balance, version FROM pgledger_accounts WHERE id = "
            "(SELECT ledger_account_id FROM accounts WHERE id = $1) FOR UPDATE",
            spending["id"],
        )
        version = int(account["version"]) + 1
        await ledger.execute(
            """
            INSERT INTO pgledger_entries
                (account_id, transfer_id, amount, account_previous_balance,
                 account_current_balance, account_version, created_at)
            VALUES ((SELECT ledger_account_id FROM accounts WHERE id = $1), $2, 500, $3, $4,
                    $5, now())
            """,
            spending["id"],
            reversal_transfer["id"],
            int(account["balance"]),
            int(account["balance"]) + 500,
            version,
        )
        await ledger.execute(
            "UPDATE pgledger_accounts SET balance = balance + 500, version = $1 WHERE id = ("
            "SELECT ledger_account_id FROM accounts WHERE id = $2)",
            version,
            spending["id"],
        )

    async def cross_path():
        old_when = ledger_today() - timedelta(days=41)
        new_when = ledger_today() - timedelta(days=1)
        movement = await write_tools.record_movement(
            ledger,
            {
                "kind": "expense",
                "amount_rupees": "100.00",
                "account": "Case bank",
                "description": "Cross-path expense",
                "date": old_when.isoformat(),
            },
            message_id=None,
            conversation_id=None,
            not_duplicate=True,
        )
        await write_tools.correct_movement(
            ledger,
            movement_id=movement["movement_id"],
            changes={"delete": True},
            message_id=None,
        )
        reversal = await ledger.fetchrow(
            "SELECT id FROM movements WHERE reverses_id = $1", movement["movement_id"]
        )
        await ledger.execute(
            "UPDATE movements SET date = $1 WHERE id = $2", new_when, reversal["id"]
        )

    async def balance_view():
        await ledger.execute(
            "UPDATE pgledger_accounts SET balance = balance + 1 WHERE id = "
            "(SELECT ledger_account_id FROM accounts WHERE id = $1)",
            bank["id"],
        )

    async def owed_view():
        await _broken_transfer(
            ledger,
            kind="expense",
            from_account_id=bank["id"],
            to_account_id=person_account,
            amount_paise=1_000,
        )

    async def schedule_capacity():
        due = ledger_today() - timedelta(days=30)
        schedule = await create_schedule(
            ledger,
            kind="obligation",
            subtype="other",
            name="Case bill",
            amount_paise=10_000,
            account_id=bank["id"],
            rule="FREQ=DAILY;COUNT=1",
            starts=due,
        )
        occurrence = await ledger.fetchrow(
            """
            INSERT INTO schedule_occurrences
                (schedule_id, due_date, name, kind, amount_paise, account_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            schedule["id"],
            due,
            "Case bill",
            "obligation",
            10_000,
            bank["id"],
        )
        await ledger.execute(
            """
            INSERT INTO schedule_payments (occurrence_id, movement_id, amount_paise)
            VALUES ($1, $2, $3)
            """,
            occurrence["id"],
            expense_id,
            20_000,
        )

    async def uncommitted_import():
        import_row = await ledger.fetchrow(
            """
            INSERT INTO imports (file_hash, parser, account_id, status)
            VALUES ($1, 'synthetic', $2, 'review') RETURNING *
            """,
            f"case-{uuid.uuid4().hex}",
            bank["id"],
        )
        line = await ledger.fetchrow(
            """
            INSERT INTO statement_lines
                (import_id, date, description_raw, amount_paise, content_hash)
            VALUES ($1, $2, 'Case line', 10_000, $3)
            RETURNING *
            """,
            import_row["id"],
            ledger_today(),
            uuid.uuid4().hex,
        )
        # A line pointing at a movement it matched is fine (that is how a certain match shows in
        # review); a movement the import wrote for its own line before commit is the violation.
        written = await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=10_000,
            description="Written for a review line",
            date=ledger_today(),
            idempotency_key=f"stmt:{line['id']}",
        )
        await ledger.execute(
            "UPDATE statement_lines SET movement_id = $1 WHERE id = $2",
            written["id"],
            line["id"],
        )

    async def unverified_import():
        await ledger.execute(
            """
            INSERT INTO imports (file_hash, parser, account_id, status)
            VALUES ($1, 'synthetic', $2, 'committed')
            """,
            f"case-{uuid.uuid4().hex}",
            bank["id"],
        )

    async def person_without_payee():
        await create_account(ledger, kind="person", name="Ghost person")

    async def movement_without_transfer():
        await ledger.execute(
            """
            INSERT INTO movements (id, date, description, kind)
            VALUES ($1, $2, 'Empty movement', 'expense')
            """,
            f"mov_test_{uuid.uuid4().hex}",
            ledger_today(),
        )

    cases = (
        (unbalanced, {"ledger_transfer_unbalanced", "account_balance_mismatch"}),
        (expense_shape, {"expense_postings"}),
        (income_shape, {"income_postings"}),
        (transfer_shape, {"transfer_postings"}),
        (split_shape, {"split_postings"}),
        (loan_shape, {"loan_postings"}),
        (refund_shape, {"refund_postings"}),
        (settlement_shape, {"settlement_postings"}),
        (adjustment_shape, {"adjustment_postings"}),
        (reversal_shape, {"reversal_not_opposite"}),
        (reversed_pair_net, {"reversed_pair_not_net_zero"}),
        (cross_path, {"cross_path_spending_mismatch"}),
        (balance_view, {"balance_view_mismatch"}),
        (owed_view, {"owed_from_unexpected_kind"}),
        (schedule_capacity, {"schedule_payment_capacity"}),
        (uncommitted_import, {"movement_from_uncommitted_import"}),
        (unverified_import, {"unverified_import_committed"}),
        (person_without_payee, {"person_account_without_payee"}),
        (movement_without_transfer, {"movement_without_transfer"}),
    )
    assert card["id"]
    assert equity["id"]
    for mutate, expected in cases:
        violations = await _in_savepoint(ledger, mutate)
        missing = expected - _codes(violations)
        assert not missing, (
            f"{mutate.__name__} did not fire {sorted(missing)}; got {sorted(_codes(violations))}"
        )

    # The rolled-back cases left the baseline ledger untouched.
    _clean(await check_ledger(ledger))
