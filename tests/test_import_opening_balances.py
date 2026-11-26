"""Statement openings, balance anchors, and repair."""

from __future__ import annotations

from datetime import date
from typing import Any

from arno.importers.balance_plan import (
    BalanceReviewSnapshot,
    book_adjustment,
    plan_balances,
    resolve_balance_window,
)
from arno.importers.commit import commit_import, undo_import
from arno.ledger.accounts import create_account, set_statement_balance
from arno.ledger.common import ledger_balance
from arno.tools.projection import _balance_at
from tests.fixtures.import_review import review_statement
from tests.invariants import check_ledger

# --- helpers ----------------------------------------------------------------------------------


def _row(
    day: str,
    description: str,
    *,
    debit: int = 0,
    credit: int = 0,
    balance: int | None = None,
) -> dict[str, Any]:
    return {
        "date": day,
        "description": description,
        "debit": debit,
        "credit": credit,
        "balance": balance,
    }


def _running(rows: list[dict[str, Any]], opening: int, day: date) -> int:
    """The statement's own running balance on ``day``: its printed opening plus its rows."""
    return opening + sum(
        int(row["credit"]) - int(row["debit"])
        for row in rows
        if date.fromisoformat(str(row["date"])) <= day
    )


async def _balance_on(conn, account: dict[str, Any], day: date) -> int | None:
    """The ledger balance at the end of ``day``, from the same view the UI reads."""
    value = await conn.fetchval(
        """
        SELECT balance_paise FROM v_balances
        WHERE account_id = $1 AND as_of <= $2
        ORDER BY as_of DESC LIMIT 1
        """,
        account["id"],
        day,
    )
    return int(value) if value is not None else None


async def _review(
    conn,
    account: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    start: date,
    end: date,
    opening: int,
    closing: int,
    opening_source: str = "printed",
) -> str:
    return await review_statement(
        conn,
        account,
        rows,
        start=start,
        end=end,
        opening=opening,
        closing=closing,
        check={
            "opening_source": opening_source,
            "opening_paise": opening,
            "closing_paise": closing,
            "chain_ok": True,
        },
        review={"manual_opening_paise": opening} if opening_source == "manual" else None,
    )


async def _adjustments_on(conn, account: dict[str, Any], day: date) -> int:
    """What that day's balance adjustments did to the account."""
    value = await conn.fetchval(
        """
        SELECT coalesce(sum(
            CASE WHEN ml.to_account_id = $1 THEN ml.amount_paise ELSE -ml.amount_paise END
        ), 0)::bigint
        FROM movements m
        JOIN movement_legs ml ON ml.movement_id = m.id
        WHERE (ml.from_account_id = $1 OR ml.to_account_id = $1)
          AND m.date = $2
          AND m.kind = 'adjustment'
        """,
        account["id"],
        day,
    )
    return int(value or 0)


async def _curve(conn, account: dict[str, Any]) -> list[tuple[date, int]]:
    """The dated balance curve. Reversed pairs leave a dated zero, which is not a balance."""
    return [
        (row["as_of"], int(row["balance_paise"]))
        for row in await conn.fetch(
            "SELECT as_of, balance_paise FROM v_balances WHERE account_id = $1 ORDER BY as_of",
            account["id"],
        )
        if int(row["balance_paise"]) != 0
    ]


# The opening belongs at the statement's first day.

BANK_START = date(2025, 11, 2)
BANK_END = date(2026, 11, 1)
BANK_OPENING = 200_000
BANK_ROWS = [
    _row("2026-01-15", "NEFT CR/ACME PAYROLL/SALARY", credit=100_000, balance=300_000),
    _row("2026-06-15", "UPI/DR/1/RENT", debit=50_000, balance=250_000),
    _row("2026-10-30", "UPI/CR/2/REFUND", credit=40_000, balance=290_000),
]
BANK_CLOSING = 290_000


async def _stated_bank(conn, *, balance_paise: int = BANK_CLOSING, as_of: date = BANK_END) -> dict:
    created = await create_account(
        conn, kind="bank", name="SBI savings", institution="SBI", last4="6789"
    )
    await set_statement_balance(conn, created["id"], balance_paise=balance_paise, as_of=as_of)
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def test_a_backfill_after_a_stated_balance_keeps_the_running_balance(ledger):
    account = await _stated_bank(ledger)
    stated_before = await _curve(ledger, account)
    import_id = await _review(
        ledger,
        account,
        BANK_ROWS,
        start=BANK_START,
        end=BANK_END,
        opening=BANK_OPENING,
        closing=BANK_CLOSING,
    )

    await commit_import(ledger, import_id)

    # Every date the statement covers carries the statement's own running balance. Before the fix
    # each of these was short by the printed opening, so a savings account read negative.
    for day in (
        BANK_START,
        date(2026, 1, 14),
        date(2026, 1, 15),
        date(2026, 6, 15),
        date(2026, 10, 30),
        BANK_END,
    ):
        expected = _running(BANK_ROWS, BANK_OPENING, day)
        assert await _balance_on(ledger, account, day) == expected, day
        # The projection's own balance seam agrees with the view the UI reads.
        assert await _balance_at(ledger, account["id"], day) == expected, day

    # The stated balance at D still holds, and it is today's balance.
    assert await _balance_on(ledger, account, BANK_END) == BANK_CLOSING
    stored = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert int(stored["statement_balance_paise"]) == BANK_CLOSING
    assert stored["statement_balance_as_of"] == BANK_END
    # The adjustment at D carries only the genuine difference: nothing, because it is consistent.
    assert await _adjustments_on(ledger, account, BANK_END) == 0

    # An opening balance is never consumption: only the statement's own debits are.
    spending = await ledger.fetchval(
        "SELECT coalesce(sum(amount_paise), 0)::bigint FROM v_spending"
    )
    assert int(spending) == sum(int(row["debit"]) for row in BANK_ROWS)
    assert await check_ledger(ledger) == []

    # Undo restores the exact previous state: the same dated curve and the same anchor.
    await undo_import(ledger, import_id)
    assert await _curve(ledger, account) == stated_before
    restored = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert int(restored["statement_balance_paise"]) == BANK_CLOSING
    assert await check_ledger(ledger) == []


async def test_a_stated_balance_after_the_statement_period_is_kept(ledger):
    rows = BANK_ROWS[:2]
    closing = _running(rows, BANK_OPENING, date(2026, 6, 15))
    account = await _stated_bank(ledger)
    import_id = await _review(
        ledger,
        account,
        rows,
        start=BANK_START,
        end=date(2026, 6, 15),
        opening=BANK_OPENING,
        closing=closing,
    )

    await commit_import(ledger, import_id)

    assert await _balance_on(ledger, account, BANK_START) == BANK_OPENING
    assert await _balance_on(ledger, account, date(2026, 6, 15)) == closing
    # The gap the statement does not cover is unexplained, and the user's figure stands at D.
    assert await _balance_on(ledger, account, date(2026, 10, 31)) == closing
    assert await _balance_on(ledger, account, BANK_END) == BANK_CLOSING
    assert await check_ledger(ledger) == []


async def test_a_longer_statement_replaces_an_earlier_opening(ledger):
    rows_a = [_row("2026-01-10", "NEFT CR/SALARY", credit=50_000, balance=150_000)]
    rows_b = [
        _row("2025-11-15", "NEFT CR/OPENING GIFT", credit=30_000, balance=90_000),
        *rows_a,
    ]
    account = await _stated_bank(ledger)
    await set_statement_balance(ledger, account["id"], balance_paise=0, as_of=date(2025, 10, 1))
    account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])

    first = await _review(
        ledger,
        account,
        rows_a,
        start=date(2025, 11, 1),
        end=date(2026, 3, 31),
        opening=100_000,
        closing=150_000,
    )
    await commit_import(ledger, first)
    # A longer statement, imported later, covers the same time and the same rows.
    second = await _review(
        ledger,
        account,
        rows_b,
        start=date(2025, 10, 1),
        end=date(2026, 3, 31),
        opening=60_000,
        closing=140_000,
    )

    await commit_import(ledger, second)

    for day in (date(2025, 10, 1), date(2025, 11, 15), date(2026, 1, 10), date(2026, 3, 31)):
        assert await _balance_on(ledger, account, day) == _running(rows_b, 60_000, day), day
    # One opening adjustment is live, not two: the superseded one is deleted.
    live = await ledger.fetchval(
        """
        SELECT count(*) FROM movements m
        WHERE m.kind = 'adjustment' AND m.description LIKE 'Opening balance%'
        """
    )
    assert int(live) == 1
    assert await check_ledger(ledger) == []


async def _live_adjustments_named(conn, prefix: str) -> int:
    value = await conn.fetchval(
        """
        SELECT count(*) FROM movements m
        WHERE m.kind = 'adjustment' AND m.description LIKE $1
        """,
        f"{prefix}%",
    )
    return int(value)


async def test_an_older_statement_committed_after_a_newer_one_keeps_every_day_right(ledger):
    # The newer statement lands first and books its printed opening on an empty ledger. The older
    # one, committed next, explains that opening with its own rows. Before the fix the newer
    # opening stayed on top, so every day of the newer statement read high by it and a stated
    # balance re-anchor at the end hid the error.
    created = await create_account(ledger, kind="bank", name="SBI main", institution="SBI")
    account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])
    older_rows = [_row("2025-06-10", "NEFT CR/SALARY", credit=50_000, balance=150_000)]
    newer_rows = [
        _row("2026-05-10", "UPI/DR/1/RENT", debit=40_000, balance=110_000),
        _row("2026-09-10", "NEFT CR/SALARY", credit=20_000, balance=130_000),
    ]
    newer = await _review(
        ledger,
        account,
        newer_rows,
        start=date(2026, 4, 1),
        end=date(2026, 11, 1),
        opening=150_000,
        closing=130_000,
    )
    await commit_import(ledger, newer)
    # A rename between the two commits must not hide the newer statement's opening.
    await ledger.execute("UPDATE accounts SET name = 'SBI savings' WHERE id = $1", account["id"])
    account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    after_newer = await _curve(ledger, account)
    older = await _review(
        ledger,
        account,
        older_rows,
        start=date(2025, 4, 1),
        end=date(2026, 3, 31),
        opening=100_000,
        closing=150_000,
    )

    await commit_import(ledger, older)

    for day in (date(2025, 6, 10), date(2026, 3, 31)):
        assert await _balance_on(ledger, account, day) == _running(older_rows, 100_000, day), day
    for day in (date(2026, 4, 1), date(2026, 5, 10), date(2026, 9, 10), date(2026, 11, 1)):
        assert await _balance_on(ledger, account, day) == _running(newer_rows, 150_000, day), day
    # One opening (the older statement's) and no stated-balance re-anchor are live.
    assert await _live_adjustments_named(ledger, "Opening balance") == 1
    assert await _live_adjustments_named(ledger, "Stated balance re-anchor") == 0
    assert await check_ledger(ledger) == []

    # Undoing the older statement restores the newer one's curve exactly.
    await undo_import(ledger, older)
    assert await _curve(ledger, account) == after_newer
    assert await check_ledger(ledger) == []


async def test_a_reanchor_of_an_import_without_a_printed_period_is_recomputed_not_stacked(ledger):
    account = await _stated_bank(ledger, balance_paise=295_000)
    newer = await _review(
        ledger,
        account,
        BANK_ROWS[1:],
        start=date(2026, 6, 1),
        end=date(2026, 10, 31),
        opening=300_000,
        closing=BANK_CLOSING,
    )
    # A statement that prints no period ends on its last line.
    await ledger.execute(
        "UPDATE imports SET period_start = NULL, period_end = NULL WHERE id = $1", newer
    )
    await commit_import(ledger, newer)
    assert await _balance_on(ledger, account, BANK_END) == 295_000

    older = await _review(
        ledger,
        account,
        BANK_ROWS[:1],
        start=BANK_START,
        end=date(2026, 5, 31),
        opening=BANK_OPENING,
        closing=300_000,
    )
    await commit_import(ledger, older)

    assert await _balance_on(ledger, account, BANK_END) == 295_000
    assert await _live_adjustments_named(ledger, "Stated balance re-anchor") <= 1
    assert await check_ledger(ledger) == []


async def test_a_balance_stated_after_the_import_is_still_an_adjustment_at_its_day(ledger):
    created = await create_account(
        ledger, kind="bank", name="SBI savings", institution="SBI", last4="6789"
    )
    account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])
    import_id = await _review(
        ledger,
        account,
        BANK_ROWS,
        start=BANK_START,
        end=BANK_END,
        opening=BANK_OPENING,
        closing=BANK_CLOSING,
    )
    await commit_import(ledger, import_id)

    later = date(2026, 11, 3)
    await set_statement_balance(ledger, account["id"], balance_paise=310_000, as_of=later)

    # The import's own history does not move, and the new figure is a visible difference at its day.
    assert await _balance_on(ledger, account, date(2026, 6, 15)) == 250_000
    assert await _balance_on(ledger, account, BANK_END) == BANK_CLOSING
    assert await _balance_on(ledger, account, later) == 310_000
    assert await _adjustments_on(ledger, account, later) == 310_000 - BANK_CLOSING
    assert await check_ledger(ledger) == []


async def test_an_unevidenced_opening_places_nothing(ledger):
    """A parser's fallback zero is a claim, not a fact: with no trusted opening nothing moves."""
    account = await _stated_bank(ledger)
    import_id = await _review(
        ledger,
        account,
        BANK_ROWS,
        start=BANK_START,
        end=BANK_END,
        opening=0,
        closing=BANK_CLOSING,
        opening_source="parser-zero",
    )

    # The stated balance's difference is a visible adjustment the review confirms.
    await commit_import(ledger, import_id, adjustment_confirmed=True)

    # Nothing is invented at the statement's first day. The stated balance at D still holds, and
    # the curve inside the period stays where it was: an untrusted opening cannot place it (the
    # documented limit; the review screen asks for a printed or stated opening instead).
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM movements WHERE description LIKE 'Opening balance%statement'"
        )
        == 0
    )
    assert await _balance_on(ledger, account, date(2026, 1, 15)) == 100_000
    assert await _balance_on(ledger, account, BANK_END) == BANK_CLOSING
    assert await check_ledger(ledger) == []


async def test_a_card_backfill_keeps_the_ledger_sign(ledger):
    created = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        institution="Slice",
        last4="4242",
        credit_limit_paise=5_000_000,
        statement_day=25,
        due_day=5,
    )
    # ₹4,450 owed is -445000 in the ledger; a card in credit would be positive.
    await set_statement_balance(ledger, created["id"], balance_paise=-445_000, as_of=BANK_END)
    account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])
    rows = [
        _row("2026-02-10", "AMAZON PAY INDIA", debit=400_000, balance=-500_000),
        _row("2026-04-20", "PAYMENT RECEIVED", credit=55_000, balance=-445_000),
    ]
    import_id = await _review(
        ledger,
        account,
        rows,
        start=date(2025, 5, 1),
        end=BANK_END,
        opening=-100_000,
        closing=-445_000,
        opening_source="manual",
    )
    await ledger.execute(
        """
        UPDATE statement_lines SET match_detail = match_detail || '{"credit_kind": "refund"}'::jsonb
        WHERE import_id = $1 AND amount_paise > 0
        """,
        import_id,
    )

    await commit_import(ledger, import_id)

    for day in (date(2025, 5, 1), date(2026, 2, 10), date(2026, 4, 20), BANK_END):
        assert await _balance_on(ledger, account, day) == _running(rows, -100_000, day), day
    assert await ledger_balance(ledger, account["id"]) == -445_000
    assert await _adjustments_on(ledger, account, BANK_END) == 0
    assert await check_ledger(ledger) == []


# --- the shared balance plan -------------------------------------------------------------------


def test_balance_review_snapshot_reads_legacy_rows():
    legacy = BalanceReviewSnapshot.model_validate({})
    assert legacy.statement_updated is True
    assert legacy.account_before is None
    assert legacy.anchors_repaired is None
    # repair.py selects rows where this key is absent; a JSON null is not the same thing.
    assert "anchors_repaired" not in legacy.to_review_json()
    empty_before = BalanceReviewSnapshot.model_validate({"account_before": {}})
    assert empty_before.account_before is None
    null_flag = BalanceReviewSnapshot.model_validate({"statement_updated": None})
    assert null_flag.statement_updated is True

    full = BalanceReviewSnapshot.model_validate(
        {
            "account_before": {
                "statement_balance_paise": 290_000,
                "statement_balance_as_of": "2026-11-01",
            },
            "statement_updated": False,
            "adjustment_paise": 12_000,
            "opening_booked_paise": 200_000,
            "superseded_anchors": 2,
            "reanchored_paise": 5_000,
            "anchors_repaired": True,
        }
    )
    assert full.account_before is not None
    assert full.account_before.statement_balance_as_of == date(2026, 11, 1)
    assert full.to_review_json() == {
        "account_before": {
            "statement_balance_paise": 290_000,
            "statement_balance_as_of": "2026-11-01",
        },
        "statement_updated": False,
        "adjustment_paise": 12_000,
        "opening_booked_paise": 200_000,
        "superseded_anchors": 2,
        "reanchored_paise": 5_000,
        "anchors_repaired": True,
    }


async def test_plan_uses_one_closing_and_period_end_for_review_and_commit(ledger):
    account = await _stated_bank(ledger)
    import_id = await _review(
        ledger,
        account,
        [_row("2026-01-15", "SALARY", credit=100_000, balance=300_000)],
        start=BANK_START,
        end=date(2026, 1, 31),
        opening=BANK_OPENING,
        closing=300_000,
    )
    # The stored closing is the figure a commit books, and a missing period falls back to the
    # statement's own rows. The check still carries the older closing: the plan must ignore it.
    await ledger.execute(
        "UPDATE imports SET closing_balance_paise = 310_000, period_end = NULL WHERE id = $1",
        import_id,
    )
    row = await ledger.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    lines = await ledger.fetch(
        "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id", import_id
    )
    window = resolve_balance_window(row, lines)
    assert window.closing == 310_000
    assert window.period_start == BANK_START
    assert window.period_end == date(2026, 1, 15)

    plan = await plan_balances(ledger, account=account, row=row, lines=lines)
    assert plan.closing == 310_000
    assert plan.period_start == BANK_START
    assert plan.period_end == date(2026, 1, 15)
    assert plan.closing_adjustment_paise == 10_000
    assert plan.stated_balance is None


async def test_adjustments_are_found_by_their_import_not_by_their_label(ledger):
    created = await create_account(ledger, kind="bank", name="SBI main", institution="SBI")
    account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])
    newer = await _review(
        ledger,
        account,
        [_row("2026-05-10", "UPI/DR/1/RENT", debit=40_000, balance=110_000)],
        start=date(2026, 4, 1),
        end=date(2026, 11, 1),
        opening=150_000,
        closing=110_000,
    )
    await commit_import(ledger, newer)
    opening_id = await ledger.fetchval(
        "SELECT id FROM movements WHERE kind = 'adjustment' AND import_id = $1", newer
    )
    assert opening_id is not None

    # Renaming the account changes every label it will ever book. A look-alike label on the same
    # day, booked by nobody's import, is not an import's opening either.
    await ledger.execute("UPDATE accounts SET name = 'Salary account' WHERE id = $1", account["id"])
    account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    await book_adjustment(
        ledger,
        account,
        amount=1_000,
        day=date(2026, 4, 1),
        description="Opening balance for SBI main statement",
    )
    decoy_id = await ledger.fetchval(
        "SELECT id FROM movements WHERE import_id IS NULL AND kind = 'adjustment'"
    )
    older = await _review(
        ledger,
        account,
        [_row("2025-06-10", "NEFT CR/SALARY", credit=50_000, balance=150_000)],
        start=date(2025, 4, 1),
        end=date(2026, 3, 31),
        opening=100_000,
        closing=150_000,
    )
    await commit_import(ledger, older)

    async def live(movement_id: str) -> bool:
        return bool(
            await ledger.fetchval(
                "SELECT EXISTS(SELECT 1 FROM movements WHERE id = $1)", movement_id
            )
        )

    assert not await live(opening_id)  # the older rows explain it, so it was found and deleted
    assert await live(decoy_id)  # a label is no longer a claim on a movement
