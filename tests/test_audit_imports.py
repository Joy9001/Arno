"""Audit regressions for statement import: commit, reconcile, undo, categorize, tasks.

Every test here is a bug found in the release audit and must fail before its fix.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import zipfile
from collections.abc import Callable
from datetime import date
from typing import cast

import pytest

from arno.db.conn import connect
from arno.importers.checks import check_statement
from arno.importers.commit import commit_import, reject_import, undo_import
from arno.importers.gate import run_gate
from arno.importers.layout import detect_fingerprint
from arno.importers.monty import ScriptContext
from arno.importers.periods import ledger_opening as opening_balance
from arno.importers.readers import Doc
from arno.importers.reconcile import insert_lines, reconcile, resolve_line
from arno.importers.task_status import reconcile as reconcile_task_imports
from arno.ledger import movements as movements_mod
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from arno.ledger.common import ledger_balance, system_account
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError
from tests.import_fixtures import SBI_PARSER, sbi_csv

PERIOD = (date(2026, 4, 1), date(2026, 4, 30))


async def _account(
    conn, *, name: str = "SBI savings", last4: str = "6789", opening_paise: int = 100000
) -> dict:
    created = await create_account(
        conn,
        kind="bank",
        name=name,
        institution="SBI",
        last4=last4,
        opening_balance_paise=opening_paise,
        opening_balance_as_of=date(2026, 3, 31),
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def _card(conn, *, last4: str = "4242", opening_paise: int | None = None) -> dict:
    created = await create_account(
        conn,
        kind="card",
        name=f"Slice card {last4}",
        institution="Slice",
        last4=last4,
        opening_balance_paise=opening_paise,
        opening_balance_as_of=date(2026, 3, 31) if opening_paise is not None else None,
        credit_limit_paise=500000,
        statement_day=25,
        due_day=5,
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def _attachment(conn, payload: bytes, name: str = "statement.csv") -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', $2, 'text/csv', $3, $4, $5)
        """,
        attachment_id,
        name,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


async def _review_import(
    conn,
    account: dict,
    rows: list[dict],
    *,
    closing: int | None = None,
    opening: int | None = None,
) -> str:
    """One import in `review`, verified against the ledger's own opening, lines reconciled."""
    if opening is None:
        # A statement for this account prints the balance the ledger held before its period, which
        # is the opening the pipeline would check the file against.
        previous = await opening_balance(conn, account["id"], PERIOD[0])
        opening = int(previous["paise"]) if previous else 0
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, check_result,
                             check_status, review, commit_groups)
        VALUES ($1, $2, 'review', $3, $4, $5, $6, $7, $8, 'VERIFIED', $9, '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
        PERIOD[0],
        PERIOD[1],
        opening,
        closing,
        {"opening_source": "manual", "opening_paise": opening},
        {"manual_opening_paise": opening},
    )
    await insert_lines(conn, import_id=import_id, account=account, parsed_account={"rows": rows})
    await reconcile(conn, import_id=import_id, account=account, period=PERIOD)
    return import_id


def _row(
    description: str,
    *,
    debit: int = 0,
    credit: int = 0,
    day: str = "2026-04-01",
    **extra: object,
) -> dict:
    return {
        "date": day,
        "description": description,
        "debit": debit,
        "credit": credit,
        "balance": None,
        **extra,
    }


# Commit: atomicity and double booking -------------------------------------------------------


async def test_a_failed_commit_writes_nothing_and_stays_in_review(ledger):
    account = await _account(ledger)
    import_id = await _review_import(
        ledger,
        account,
        [
            _row("SWIGGY", debit=45000, day="2026-04-01"),
            # A zero-amount line passes the checks and cannot be booked. It must not leave the
            # earlier lines of the same commit written.
            _row("SUMMARY ROW", debit=0, credit=0, day="2026-04-02"),
        ],
    )

    with pytest.raises(InvalidLedgerError):
        await commit_import(ledger, import_id)

    booked = "('expense', 'income', 'transfer', 'refund', 'split', 'loan', 'settlement')"
    assert await ledger.fetchval(f"SELECT count(*) FROM movements WHERE kind IN {booked}") == 0
    status = await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id)
    assert status == "review"
    lines = await ledger.fetch(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    assert all(line["match_state"] == "new" and line["movement_id"] is None for line in lines)
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 100000

    # The import is not wedged: ignoring the bad row lets the rest commit exactly once.
    zero = await ledger.fetchrow(
        "SELECT id FROM statement_lines WHERE import_id = $1 AND amount_paise = 0", import_id
    )
    await resolve_line(ledger, import_id=import_id, line_id=zero["id"], resolution="ignore")
    await commit_import(ledger, import_id)
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 1
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 100000 - 45000


async def test_committing_twice_books_once(ledger):
    account = await _account(ledger)
    import_id = await _review_import(ledger, account, [_row("SWIGGY", debit=45000)])

    first = await commit_import(ledger, import_id)
    second = await commit_import(ledger, import_id)

    assert first["status"] == "committed"
    assert second["already"] is True
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 1
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 100000 - 45000


async def test_two_concurrent_commits_book_once(ledger):
    account = await _account(ledger)
    import_id = await _review_import(ledger, account, [_row("SWIGGY", debit=45000)])
    dsn = os.environ["DATABASE_URL"]

    async def one() -> dict:
        async with connect(dsn) as conn:
            return await commit_import(conn, import_id)

    results = await asyncio.gather(one(), one())

    assert {result["status"] for result in results} == {"committed"}
    assert sum(1 for result in results if not result.get("already")) == 1
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 1
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 100000 - 45000


async def test_a_failed_undo_leaves_the_import_whole(ledger):
    account = await _account(ledger)
    import_id = await _review_import(
        ledger,
        account,
        [
            _row("SWIGGY", debit=45000, day="2026-04-01"),
            _row("BLINKIT", debit=20000, day="2026-04-02"),
        ],
    )
    await commit_import(ledger, import_id)
    lines = await ledger.fetch(
        "SELECT movement_id FROM statement_lines WHERE import_id = $1 ORDER BY date", import_id
    )
    # A later write touches the first movement, so undoing its group must conflict.
    await movements_mod.correct_movement(
        ledger, lines[0]["movement_id"], changes={"date": date(2026, 4, 5)}
    )

    with pytest.raises(ConflictLedgerError):
        await undo_import(ledger, import_id)

    # Nothing is half-reverted: the second line's movement is still effective.
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 100000 - 65000
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == (
        "committed"
    )
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'reversal'") == 0


# Commit: sign, account, and closing --------------------------------------------------------


async def test_a_closing_adjustment_is_visible_and_never_consumption(ledger):
    account = await _account(ledger)
    spending = await system_account(ledger, "spending")
    before = await ledger_balance(ledger, spending["ledger_account_id"])
    # The ledger holds one logged row the statement does not, and the statement's closing is
    # lower than the ledger, so commit writes a visible adjustment.
    await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=5000,
        description="logged, not on the statement",
        date=date(2026, 4, 10),
        capture={"kind": "chat", "ref": "msg_extra"},
    )
    import_id = await _review_import(
        ledger,
        account,
        [_row("SWIGGY", debit=45000, day="2026-04-03")],
        closing=45000,
    )

    # The difference is an adjustment the review screen states and the user confirms.
    result = await commit_import(ledger, import_id, adjustment_confirmed=True)

    assert result["adjustment_paise"] == -5000
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 45000
    # Spending rose by the statement row only; the adjustment touches equity, not spending.
    assert await ledger_balance(ledger, spending["ledger_account_id"]) == before + 5000 + 45000
    adjustment = await ledger.fetchrow(
        "SELECT * FROM movements WHERE kind = 'adjustment' ORDER BY created_at DESC"
    )
    assert adjustment["description"].startswith("Balance adjustment")
    to_spending = await ledger.fetchval(
        """
        SELECT count(*) FROM movement_legs ml
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        WHERE ml.movement_id = $1 AND t.to_account_id = $2
        """,
        adjustment["id"],
        spending["ledger_account_id"],
    )
    assert to_spending == 0


async def test_a_wrong_direction_merge_is_refused_before_any_write(ledger):
    account = await _account(ledger)
    income = await movements_mod.record_income(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="salary",
        date=date(2026, 4, 2),
        capture={"kind": "chat", "ref": "msg_in"},
    )
    import_id = await _review_import(
        ledger, account, [_row("SWIGGY", debit=45000, day="2026-04-03")]
    )
    line = await ledger.fetchrow("SELECT id FROM statement_lines WHERE import_id = $1", import_id)
    # The user picks a logged credit for a debit line: money would move the wrong way.
    await resolve_line(
        ledger,
        import_id=import_id,
        line_id=line["id"],
        resolution="merge",
        movement_id=income["id"],
    )

    with pytest.raises(ConflictLedgerError):
        await commit_import(ledger, import_id)

    movement = await ledger.fetchrow("SELECT * FROM movements WHERE id = $1", income["id"])
    assert str(movement["date"]) == "2026-04-02"
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM captures WHERE movement_id = $1 AND kind = 'statement_line'",
            income["id"],
        )
        == 0
    )
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "review"


# Reconcile: dedupe and near duplicates -----------------------------------------------------


async def test_the_same_row_on_two_accounts_is_not_a_duplicate(ledger):
    first = await _account(ledger, name="SBI savings", last4="6789")
    second = await _account(ledger, name="Axis savings", last4="1111")
    row = _row("SWIGGY", debit=45000, day="2026-04-01")
    original = await _review_import(ledger, first, [row])
    await commit_import(ledger, original)

    import_id = await _review_import(ledger, second, [row])

    line = await ledger.fetchrow(
        "SELECT match_state FROM statement_lines WHERE import_id = $1", import_id
    )
    assert line["match_state"] == "new"
    await commit_import(ledger, import_id)
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 2


async def test_a_near_duplicate_merges_and_two_identical_purchases_both_stay(ledger):
    account = await _account(ledger)
    logged = await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="swiggy",
        date=date(2026, 4, 2),
        capture={"kind": "chat", "ref": "msg_1"},
    )
    import_id = await _review_import(
        ledger,
        account,
        [
            # Different payee spelling, one day off: still the same movement.
            _row(
                "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
                debit=45000,
                day="2026-04-03",
            ),
            # A second, genuinely separate purchase of the same amount the same day.
            _row(
                "UPI/DR/123456789013/SWIGGY BANGALORE/ramesh@okaxis",
                debit=45000,
                day="2026-04-03",
            ),
        ],
    )

    states = [
        row["match_state"]
        for row in await ledger.fetch(
            "SELECT match_state FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
            import_id,
        )
    ]
    assert states == ["certain", "new"]
    await commit_import(ledger, import_id)
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 2
    assert (
        await ledger.fetchval("SELECT count(*) FROM captures WHERE movement_id = $1", logged["id"])
        == 2
    )


async def test_an_overlapping_reimport_of_the_same_account_ignores_the_second_copy(ledger):
    account = await _account(ledger)
    row = _row("SWIGGY", debit=45000)
    first = await _review_import(ledger, account, [row])
    await commit_import(ledger, first)

    overlapping = await _review_import(
        ledger, account, [row, _row("BLINKIT", debit=20000, day="2026-04-20")]
    )

    counts = (await ledger.fetchrow("SELECT counts FROM imports WHERE id = $1", overlapping))[
        "counts"
    ]
    assert counts["ignored"] == 1
    assert counts["new"] == 1


# Undo and reject ----------------------------------------------------------------------------


async def test_undo_removes_the_payee_the_import_created(ledger):
    account = await _account(ledger)
    import_id = await _review_import(
        ledger,
        account,
        [
            _row(
                "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
                debit=45000,
                counterparty="SWIGGY BANGALORE",
            )
        ],
    )
    await commit_import(ledger, import_id)
    payee = await ledger.fetchrow("SELECT * FROM payees WHERE display_name = 'SWIGGY BANGALORE'")
    assert payee is not None and payee["outside_text"] is True

    await undo_import(ledger, import_id)

    payee = await ledger.fetchrow("SELECT * FROM payees WHERE id = $1", payee["id"])
    assert payee["active"] is False
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 100000


async def test_a_rejected_import_leaves_balances_and_spending_alone(ledger):
    account = await _account(ledger)
    spending = await system_account(ledger, "spending")
    before = await ledger_balance(ledger, spending["ledger_account_id"])
    import_id = await _review_import(ledger, account, [_row("SWIGGY", debit=45000)])

    await reject_import(ledger, import_id, "not mine")

    booked = "('expense', 'income', 'transfer', 'refund', 'split', 'loan', 'settlement')"
    assert await ledger.fetchval(f"SELECT count(*) FROM movements WHERE kind IN {booked}") == 0
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 100000
    assert await ledger_balance(ledger, spending["ledger_account_id"]) == before
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == (
        "rejected"
    )


# The gate: replay order ----------------------------------------------------------------------


def _reversing_ids(monkeypatch) -> None:
    """Make generated ids sort newest-first, as two UUIDv7 values in one millisecond may."""
    state = {"n": 0}

    async def fake(_conn, _prefix):  # noqa: ANN001 - the tested signature
        state["n"] += 1
        return f"stl_{1000 - state['n']:04d}"

    monkeypatch.setattr("arno.importers.reconcile.new_id", fake)


async def test_the_gate_compares_rows_in_parser_order_not_id_order(ledger, monkeypatch):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    import_id = await new_id(ledger, "imp")
    attachment_id = await _attachment(ledger, sbi_csv())
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, account_id, institution,
                             fingerprint, period_start, period_end, commit_groups)
        VALUES ($1, 'hash-order', 'committed', $2, $3, 'SBI', $4, '2026-04-01', '2026-04-30',
                '[]'::jsonb)
        """,
        import_id,
        attachment_id,
        account["id"],
        fingerprint,
    )
    _reversing_ids(monkeypatch)
    await insert_lines(
        ledger,
        import_id=import_id,
        account=account,
        parsed_account={
            "rows": [
                {
                    "date": "2026-04-01",
                    "description": "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
                    "debit": 45000,
                    "credit": 0,
                    "balance": 24522890,
                },
                {
                    "date": "2026-04-05",
                    "description": "NEFT CR/ACME PAYROLL/SALARY APRIL",
                    "debit": 0,
                    "credit": 10000000,
                    "balance": 34522890,
                },
                {
                    "date": "2026-04-12",
                    "description": "UPI/DR/223456789012/AMAZON PAY INDIA/amazon@okhdfcbank",
                    "debit": 120000,
                    "credit": 0,
                    "balance": 34402890,
                },
            ]
        },
    )
    monkeypatch.undo()
    current = await new_id(ledger, "imp")
    attachment_id = await _attachment(ledger, sbi_csv())
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, account_id, institution,
                             fingerprint, period_start, period_end, commit_groups)
        VALUES ($1, 'hash-order-2', 'parsing', $2, $3, 'SBI', $4, '2026-04-01', '2026-04-30',
                '[]'::jsonb)
        """,
        current,
        attachment_id,
        account["id"],
        fingerprint,
    )

    result = await run_gate(
        doc=Doc(sbi_csv()),
        context=ScriptContext(
            period=("2026-04-01", "2026-04-30"),
            ledger_opening_paise=24567890,
            fingerprint=fingerprint,
            expected_fingerprint=fingerprint,
            allow_vision=False,
            import_id=current,
        ),
        code=SBI_PARSER,
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["checked"] == 1
    assert result["replay"]["mismatch"] is None


# The opening balance: the card's question has an answer -------------------------------------


async def _card_review(
    conn, card: dict, rows: list[dict], closing: int, *, evidenced: bool = False
) -> str:
    """A first card statement. By default the F12 shape: a parser zero with no evidence."""
    import_id = await new_id(conn, "imp")
    check = (
        {"status": "VERIFIED", "opening_source": "ledger", "opening_paise": 0}
        if evidenced
        else {
            "status": "VERIFIED",
            "opening_source": "printed",
            "opening_paise": 0,
            "closing_paise": closing,
            "chain_ok": None,
            "rows": len(rows),
        }
    )
    check_status = check.pop("status")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, check_result,
                             check_status, commit_groups)
        VALUES ($1, $2, 'review', $3, '2026-04-01', '2026-04-30', 0, $4, $5, $6,
                '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        card["id"],
        closing,
        check,
        check_status,
    )
    await insert_lines(conn, import_id=import_id, account=card, parsed_account={"rows": rows})
    await reconcile(conn, import_id=import_id, account=card, period=PERIOD)
    return import_id


async def test_a_stated_opening_unblocks_a_first_card_statement(ledger):
    from arno.importers.pipeline import set_manual_opening

    card = await _card(ledger)
    import_id = await _card_review(
        ledger, card, [_row("BLINKIT", debit=42800, day="2026-04-01")], closing=-42800
    )

    with pytest.raises(ConflictLedgerError, match="opening balance"):
        await commit_import(ledger, import_id)

    detail = await set_manual_opening(ledger, import_id=import_id, opening_paise=0)

    assert detail["status"] == "review"
    assert detail["check"] == "VERIFIED"
    assert detail["opening_source"] == "manual"
    assert detail["opening_evidenced"] is True
    assert detail["opening_required"] is False
    await commit_import(ledger, import_id)
    assert await ledger_balance(ledger, card["ledger_account_id"]) == -42800


async def test_an_opening_that_does_not_add_up_keeps_the_import_blocked(ledger):
    from arno.importers.pipeline import set_manual_opening

    card = await _card(ledger)
    import_id = await _card_review(
        ledger, card, [_row("BLINKIT", debit=42800, day="2026-04-01")], closing=-42800
    )

    detail = await set_manual_opening(ledger, import_id=import_id, opening_paise=-100000)

    assert detail["status"] == "needs_input"
    assert detail["check"] == "DISCREPANCY"
    assert detail["opening_evidenced"] is False
    with pytest.raises((ConflictLedgerError, InvalidLedgerError)):
        await commit_import(ledger, import_id)
    assert await ledger_balance(ledger, card["ledger_account_id"]) == 0


def test_the_opening_route_needs_a_session(client):
    assert client.post("/api/imports/imp_x/opening", json={"opening_paise": 0}).status_code == 401


# Periods, checks, and exact money -----------------------------------------------------------


def test_an_iso_period_header_is_read_as_a_period():
    from arno.importers.periods import derive_period

    doc = Doc(b"Statement Period: 2026-04-01 to 2026-04-30\n01/04/2026 SWIGGY 450.00\n")
    assert derive_period(doc) == {
        "start": "2026-04-01",
        "end": "2026-04-30",
        "source": "header",
    }


def test_periods_take_the_year_from_the_rows_across_a_year_rollover():
    from arno.importers.periods import derive_period

    doc = Doc(b"Cycle 25 DEC - 24 JAN\n26 Dec '25 UPI 450.00\n02 Jan '26 UPI 120.00\n")
    assert derive_period(doc) == {
        "start": "2025-12-25",
        "end": "2026-01-24",
        "source": "header",
    }
    february = Doc(b"Statement for February 2028\n")
    assert derive_period(february) == {
        "start": "2028-02-01",
        "end": "2028-02-29",
        "source": "month-name",
    }


def test_a_dropped_or_duplicated_line_breaks_the_running_balance():
    rows = [
        {"date": "2026-04-01", "debit": 100, "credit": 0, "balance": 900},
        {"date": "2026-04-02", "debit": 200, "credit": 0, "balance": 700},
        {"date": "2026-04-03", "debit": 300, "credit": 0, "balance": 400},
    ]
    assert check_statement(rows, 1000, 400)["status"] == "VERIFIED"

    dropped = check_statement([rows[0], rows[2]], 1000, 400)
    assert dropped["status"] == "DISCREPANCY"
    assert dropped["chain_breaks"] == [1]

    duplicated = check_statement([rows[0], rows[1], rows[1], rows[2]], 1000, 400)
    assert duplicated["status"] == "DISCREPANCY"
    assert duplicated["chain_breaks"] == [2]


def test_money_parses_indian_grouping_and_negatives_without_floats():
    from pydantic_monty import AsyncMonty  # noqa: F401 - the helper block runs as script text

    from arno.importers.helpers import PRELUDE

    namespace: dict[str, object] = {}
    exec(compile(PRELUDE, "<helpers>", "exec"), namespace)  # noqa: S102 - our own constant
    money = cast(Callable[[str], int | None], namespace["money"])
    assert money("1,23,456.78") == 12345678
    assert money("\u20b91,23,456.78") == 12345678
    assert money("(1,234.00)") == -123400
    assert money("-0.01") == -1
    assert money("450.5") == 45050
    assert money("450.155") is None
    assert money("\u2014") is None


# Own-account transfers: both statements, one movement ---------------------------------------


async def test_a_card_repayment_imported_from_both_statements_is_one_movement(ledger):
    bank = await _account(ledger, name="SBI savings", last4="6789")
    card = await _card(ledger, last4="4242", opening_paise=0)
    spending = await system_account(ledger, "spending")
    spending_before = await ledger_balance(ledger, spending["ledger_account_id"])

    bank_import = await _review_import(
        ledger,
        bank,
        [
            _row(
                "UPI/DR/123456789012/SLICE CARD/self",
                debit=42800,
                day="2026-04-05",
                counterparty="Slice Card",
            )
        ],
    )
    bank_line = await ledger.fetchrow(
        "SELECT id FROM statement_lines WHERE import_id = $1", bank_import
    )
    await ledger.execute(
        "UPDATE statement_lines SET match_detail = coalesce(match_detail, '{}'::jsonb) || $1 "
        "WHERE id = $2",
        {"suggested_transfer_account_id": card["id"], "transfer_confirmed": True},
        bank_line["id"],
    )
    await commit_import(ledger, bank_import)

    # The card's own statement holds the purchase and dates the same repayment a day later.
    card_import = await _card_review(
        ledger,
        card,
        [
            _row("BLINKIT", debit=42800, day="2026-04-02"),
            _row("PAYMENT RECEIVED - THANK YOU", credit=42800, day="2026-04-06"),
        ],
        0,
        evidenced=True,
    )
    card_lines = await ledger.fetch(
        """
        SELECT id, amount_paise, match_state, movement_id
        FROM statement_lines WHERE import_id = $1 ORDER BY date, id
        """,
        card_import,
    )
    credit = next(line for line in card_lines if line["amount_paise"] > 0)
    purchase = next(line for line in card_lines if line["amount_paise"] < 0)
    assert credit["match_state"] == "certain"
    assert purchase["match_state"] == "new"
    await commit_import(ledger, card_import)

    transfers = await ledger.fetch(
        """
        SELECT m.id, m.date FROM movements m
        WHERE m.kind = 'transfer'
        """
    )
    assert len(transfers) == 1
    assert str(transfers[0]["date"]) == "2026-04-06"
    assert await ledger_balance(ledger, bank["ledger_account_id"]) == 100000 - 42800
    assert await ledger_balance(ledger, card["ledger_account_id"]) == 0
    # The purchase is consumption once; the repayment is not.
    assert await ledger_balance(ledger, spending["ledger_account_id"]) == spending_before + 42800
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'income'") == 0


# Categorize: a user correction is never overwritten -----------------------------------------


async def test_a_user_category_survives_a_later_rule_run(ledger):
    from arno.importers import rules as rules_mod

    account = await _account(ledger)
    import_id = await _review_import(
        ledger,
        account,
        [
            _row(
                "UPI/DR/123456789012/SWIGGY/ramesh@okaxis",
                debit=45000,
                counterparty="Swiggy",
            )
        ],
    )
    await commit_import(ledger, import_id)
    line = await ledger.fetchrow(
        "SELECT movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    shopping = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Shopping'")
    await movements_mod.correct_movement(
        ledger, line["movement_id"], changes={"category_id": shopping}
    )
    food = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    await ledger.execute(
        """
        INSERT INTO category_rules (match, pattern, category_id, source)
        VALUES ('contains', 'swiggy', $1, 'user')
        """,
        food,
    )

    await rules_mod.apply_rules(ledger, import_id)

    kept = await ledger.fetchval(
        """
        SELECT category_id FROM movement_legs
        WHERE movement_id = $1 AND category_id IS NOT NULL
        """,
        line["movement_id"],
    )
    assert kept == shopping


async def test_a_user_rule_beats_a_later_llm_answer_for_the_same_payee(ledger):
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from arno.agent.categorizer import CategorizerDeps, save_categories

    account = await _account(ledger)
    import_id = await _review_import(
        ledger,
        account,
        [
            _row(
                "UPI/DR/123456789012/SWIGGY/ramesh@okaxis",
                debit=45000,
                counterparty="Swiggy",
            )
        ],
    )
    await commit_import(ledger, import_id)
    # The user states the category while the categorizer job is still queued.
    shopping = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Shopping'")
    await ledger.execute(
        """
        INSERT INTO category_rules (match, pattern, category_id, source)
        VALUES ('equals', 'swiggy', $1, 'user')
        """,
        shopping,
    )
    deps = CategorizerDeps(import_id=import_id, run_id="run")
    deps.answers["swiggy"] = ("Food & Dining", "model guess")
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())

    await save_categories(ctx, [])

    line = await ledger.fetchrow(
        "SELECT movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    labeled = await ledger.fetchval(
        """
        SELECT category_id FROM movement_legs
        WHERE movement_id = $1 AND category_id IS NOT NULL
        """,
        line["movement_id"],
    )
    assert labeled == shopping
    assert await ledger.fetchval("SELECT source FROM category_rules WHERE pattern = 'swiggy'") == (
        "user"
    )


async def test_a_reversed_logged_row_does_not_absorb_a_statement_line(ledger):
    from arno.ledger.audit import undo_group

    account = await _account(ledger)
    logged = await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="swiggy",
        date=date(2026, 4, 2),
        capture={"kind": "chat", "ref": "msg_1"},
    )
    deleted = await movements_mod.correct_movement(ledger, logged["id"], changes={"delete": True})
    import_id = await _review_import(
        ledger, account, [_row("SWIGGY", debit=45000, day="2026-04-03")]
    )

    line = await ledger.fetchrow(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    # The logged row was undone, so the statement's row is new money, not a merge.
    assert line["match_state"] == "new"
    assert line["movement_id"] is None

    # An undo of that undo makes the logged row effective again, and then it matches.
    await undo_group(ledger, deleted["undo_id"])
    await reconcile(ledger, import_id=import_id, account=account, period=PERIOD)
    line = await ledger.fetchrow(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    assert line["match_state"] == "certain"
    assert line["movement_id"] == logged["id"]


async def test_a_committed_import_cannot_be_parsed_and_booked_again(ledger):
    from arno.agent.parser import submit_parser_code

    account = await _account(ledger, opening_paise=24567890)
    payload = sbi_csv()
    attachment_id = await _attachment(ledger, payload, name="sbi.csv")
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, account_id, commit_groups)
        VALUES ($1, 'hash-resubmit', 'parsing', $2, $3, '[]'::jsonb)
        """,
        import_id,
        attachment_id,
        account["id"],
    )
    first = await submit_parser_code(ledger, import_ids=[import_id], code=SBI_PARSER)
    assert first["status"] == "parser_submitted", first
    await commit_import(ledger, import_id)
    before = await ledger.fetch(
        "SELECT id, movement_id FROM statement_lines WHERE import_id = $1 ORDER BY id", import_id
    )
    assert len(before) == 3

    again = await submit_parser_code(ledger, import_ids=[import_id], code=SBI_PARSER)

    assert again["status"] == "failed"
    assert "already committed" in again["reason"]
    after = await ledger.fetch(
        "SELECT id, movement_id FROM statement_lines WHERE import_id = $1 ORDER BY id", import_id
    )
    assert after == before
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == (
        "committed"
    )
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 2


# Tasks: a parsing import cannot be left stuck -----------------------------------------------


async def test_a_restart_fails_an_import_that_never_got_a_task(ledger):
    account = await _account(ledger)
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, commit_groups)
        VALUES ($1, 'hash-orphan', 'parsing', $2, '[]'::jsonb)
        """,
        import_id,
        account["id"],
    )

    changed = await reconcile_task_imports(ledger, {})

    assert changed == 1
    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "failed"
    assert row["reason"]


# Readers: a multi-file archive is never imported in part ------------------------------------


async def test_a_zip_of_statements_is_refused_rather_than_half_imported(ledger):
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from arno.agent.deps import AgentDeps
    from arno.agent.parser import open_imports

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("april.csv", sbi_csv())
        archive.writestr("may.csv", sbi_csv())
    attachment_id = await _attachment(ledger, buffer.getvalue(), name="statements.zip")
    await _account(ledger)
    ctx = RunContext(
        deps=AgentDeps(conversation_id="cnv_zip", statement_attachments=(attachment_id,)),
        model=TestModel(),
        usage=RunUsage(),
    )

    opened = await open_imports(ctx)

    assert opened[0]["import_id"]
    assert opened[0]["reason"]
    status = await ledger.fetchval(
        "SELECT status FROM imports WHERE id = $1", opened[0]["import_id"]
    )
    assert status == "failed"
