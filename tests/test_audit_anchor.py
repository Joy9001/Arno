"""Audit regressions: a statement's opening is booked where it belongs, and a backtest may never
publish a month it could not measure.

Bug A (P1, wrong money history on real data): a balance the user states first is an `adjustment`
dated that day D. A later backfill's printed opening O then stayed inside that adjustment, so
every balance from the statement's first day S to D was short by O: a savings account read
negative, balance-on-date, the charts, the backtest's actuals and the coverage claims were all
wrong, while today's balance and the statement's closing still matched. Opening balances are not
consumption, so they belong at S, and the later anchor keeps the stated figure.

Bug B: `known_then` treated an account as unknown before its row's `created_at`, so a backfilled
account vanished from the backtest ("no liquid account"); and a backtest that could not measure a
month still published its error as if it meant something.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
from typer.testing import CliRunner

from arno.cli import app as cli_app
from arno.engine import backtest
from arno.importers.commit import _adjust_closing, commit_import, undo_import
from arno.importers.reconcile import insert_lines, reconcile
from arno.ledger import movements as movements_mod
from arno.ledger.accounts import create_account, set_statement_balance
from arno.ledger.audit import new_id
from arno.ledger.common import ledger_balance
from arno.tools.projection import _balance_at, load_inputs
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
    """One import in `review`, VERIFIED against the statement's own printed figures."""
    raw = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
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
        start,
        end,
        opening,
        closing,
        {
            "opening_source": opening_source,
            "opening_paise": opening,
            "closing_paise": closing,
            "chain_ok": True,
        },
        {"manual_opening_paise": opening} if opening_source == "manual" else {},
    )
    await insert_lines(conn, import_id=import_id, account=raw, parsed_account={"rows": rows})
    await reconcile(conn, import_id=import_id, account=raw, period=(start, end))
    return import_id


async def _adjustments_on(conn, account: dict[str, Any], day: date) -> int:
    """What that day's balance adjustments did to the account, cancellations included."""
    value = await conn.fetchval(
        """
        SELECT coalesce(sum(
            CASE WHEN t.to_account_id = $1 THEN t.amount ELSE -t.amount END
        ), 0)::bigint
        FROM movements m
        JOIN movement_legs ml ON ml.movement_id = m.id
        JOIN pgledger_transfers t ON t.id = ml.transfer_id
        WHERE (t.from_account_id = $1 OR t.to_account_id = $1)
          AND m.date = $2
          AND m.kind IN ('adjustment', 'reversal')
        """,
        account["ledger_account_id"],
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


# --- Bug A: the opening belongs at the statement's first day ----------------------------------

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
        assert await _balance_at(ledger, account["ledger_account_id"], day) == expected, day

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
    # One opening adjustment is live, not two: the superseded one nets to nothing.
    live = await ledger.fetchval(
        """
        SELECT count(*) FROM movements m
        WHERE m.kind = 'adjustment' AND m.description LIKE 'Opening balance%'
          AND NOT EXISTS (
              SELECT 1 FROM movements r WHERE r.reverses_id = m.id
          )
        """
    )
    assert int(live) == 1
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
    assert await ledger_balance(ledger, account["ledger_account_id"]) == -445_000
    assert await _adjustments_on(ledger, account, BANK_END) == 0
    assert await check_ledger(ledger) == []


# --- the repair for ledgers written before the fix --------------------------------------------


async def _legacy_import(
    conn,
    account: dict[str, Any],
    *,
    start: date,
    end: date,
    opening: int,
    rows: list[dict[str, Any]],
    closing: int,
) -> str:
    """The state the old commit path left: the opening absorbed into the adjustment at the end.

    Rows and a closing adjustment only; nothing places the printed opening.
    """
    groups: list[str] = []
    for row in rows:
        day = date.fromisoformat(str(row["date"]))
        if row["debit"]:
            record = await movements_mod.record_expense(
                conn,
                account_id=account["id"],
                amount_paise=int(row["debit"]),
                description=str(row["description"]),
                date=day,
                apply_rules=False,
            )
        else:
            record = await movements_mod.record_income(
                conn,
                account_id=account["id"],
                amount_paise=int(row["credit"]),
                description=str(row["description"]),
                date=day,
            )
        groups.append(str(record["undo_id"]))
    adjustment_group, _difference = await _adjust_closing(
        conn, account=account, closing=closing, day=end
    )
    if adjustment_group:
        groups.append(adjustment_group)
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, check_result,
                             check_status, review, commit_groups, committed_at)
        VALUES ($1, $2, 'committed', $3, $4, $5, $6, $7, $8, 'VERIFIED', '{}'::jsonb, $9,
                now())
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
        start,
        end,
        opening,
        closing,
        {
            "opening_source": "printed",
            "opening_paise": opening,
            "closing_paise": closing,
        },
        groups,
    )
    return import_id


async def test_the_repair_dry_run_writes_nothing_and_the_apply_fixes_the_history(ledger):
    from arno.importers.repair import repair_openings

    account = await _stated_bank(ledger)
    before_import = await _curve(ledger, account)
    import_id = await _legacy_import(
        ledger,
        account,
        start=BANK_START,
        end=BANK_END,
        opening=BANK_OPENING,
        rows=BANK_ROWS,
        closing=BANK_CLOSING,
    )
    broken = await _curve(ledger, account)
    # The bug: the first year is short by the printed opening.
    assert await _balance_on(ledger, account, date(2026, 1, 15)) == 100_000

    preview = await repair_openings(ledger, dry_run=True)
    assert await _curve(ledger, account) == broken
    assert [row["import_id"] for row in preview] == [import_id]
    assert preview[0]["account"] == "SBI savings"
    assert preview[0]["opening_paise"] == BANK_OPENING
    assert preview[0]["period_start"] == BANK_START
    assert preview[0]["period_end"] == BANK_END

    applied = await repair_openings(ledger, dry_run=False)
    assert applied == preview
    for day in (BANK_START, date(2026, 1, 15), BANK_END):
        assert await _balance_on(ledger, account, day) == _running(BANK_ROWS, BANK_OPENING, day)
    assert await _balance_on(ledger, account, BANK_END) == BANK_CLOSING

    # Idempotent: a second run finds nothing left to move.
    assert await repair_openings(ledger, dry_run=False) == []

    # Undoing the repaired import puts the ledger back exactly as it was before the import.
    await undo_import(ledger, import_id)
    assert await _curve(ledger, account) == before_import
    assert await check_ledger(ledger) == []


async def test_the_repair_walks_a_chain_of_imports_in_statement_order(ledger):
    from arno.importers.repair import repair_openings

    account = await _stated_bank(ledger)
    first_rows = [
        _row("2025-12-15", "NEFT CR/SALARY", credit=80_000, balance=280_000),
        _row("2026-01-20", "UPI/DR/9/RENT", debit=30_000, balance=250_000),
    ]
    second_rows = [
        _row("2026-05-10", "UPI/DR/8/BILL", debit=20_000, balance=230_000),
    ]
    await _legacy_import(
        ledger,
        account,
        start=date(2025, 12, 1),
        end=date(2026, 2, 28),
        opening=BANK_OPENING,
        rows=first_rows,
        closing=250_000,
    )
    await _legacy_import(
        ledger,
        account,
        start=date(2026, 3, 1),
        end=date(2026, 6, 30),
        opening=250_000,
        rows=second_rows,
        closing=230_000,
    )

    await repair_openings(ledger, dry_run=False)

    assert await _balance_on(ledger, account, date(2025, 12, 1)) == BANK_OPENING
    assert await _balance_on(ledger, account, date(2026, 1, 20)) == 250_000
    assert await _balance_on(ledger, account, date(2026, 3, 1)) == 250_000
    assert await _balance_on(ledger, account, date(2026, 5, 10)) == 230_000
    # The stated balance was dated after both statements, so it still holds.
    assert await _balance_on(ledger, account, BANK_END) == BANK_CLOSING
    assert await check_ledger(ledger) == []


async def test_the_repair_command_prints_what_it_would_change(ledger):
    from arno.importers.repair import repair_openings

    account = await _stated_bank(ledger)
    await _legacy_import(
        ledger,
        account,
        start=BANK_START,
        end=BANK_END,
        opening=BANK_OPENING,
        rows=BANK_ROWS,
        closing=BANK_CLOSING,
    )
    broken = await _curve(ledger, account)

    result = await asyncio.to_thread(CliRunner().invoke, cli_app, ["repair-openings", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "SBI savings" in result.output
    assert f"{BANK_START.isoformat()} to {BANK_END.isoformat()}" in result.output
    assert "nothing written" in result.output.lower()
    assert await _curve(ledger, account) == broken
    assert await repair_openings(ledger, dry_run=False) != []


# --- Bug B: a backfilled account is known then, and an unmeasurable month is not published -----


async def test_a_backfilled_account_is_known_before_its_row_existed(ledger):
    created = await create_account(ledger, kind="bank", name="SBI savings", institution="SBI")
    await movements_mod.record_income(
        ledger,
        account_id=created["id"],
        amount_paise=500_000,
        description="SALARY",
        date=date(2026, 3, 5),
    )
    as_of = date(2026, 4, 1)

    inputs = await load_inputs(ledger, as_of=as_of, known_then=True)

    assert [row.id for row in inputs.liquid] == [created["id"]]
    assert "no liquid account" not in inputs.data_quality["setup_gaps"]


_README = "intro\n<!-- backtest:start -->\nold\n<!-- backtest:end -->\noutro\n"


def _score(group: str = "ledger") -> backtest.Score:
    return backtest.Score(
        group=group,
        as_of=date(2026, 1, 1),
        forecaster="flat",
        target="end",
        horizon=7,
        crps=100.0,
        pinball=(10.0, 50.0, 10.0),
        covered=True,
        width=0,
        pit=0.5,
        breach_probability=None,
        breached=None,
    )


def test_the_backtest_says_when_it_could_not_measure_an_origin(tmp_path):
    # Every origin was dropped for missing setup: nothing was measured.
    result = backtest.summarize([], source="real ledger", dropped=2, names=["flat"])

    report = backtest.format_report(result)
    assert "not a measurement" in report.lower()
    assert "2 dropped" in report
    assert "must not be published" in report

    readme = tmp_path / "README.md"
    readme.write_text(_README, encoding="utf-8")
    with pytest.raises(ValueError, match="not a measurement"):
        backtest.publish_report(result, readme)
    assert readme.read_text(encoding="utf-8") == _README


def test_a_measured_backtest_still_publishes_and_counts_what_it_dropped(tmp_path):
    result = backtest.summarize([_score()], source="real ledger", dropped=1, names=["flat"])
    readme = tmp_path / "README.md"
    readme.write_text(_README, encoding="utf-8")

    backtest.publish_report(result, readme)

    text = readme.read_text(encoding="utf-8")
    assert "Source: real ledger. 1 weekly origins scored, 1 dropped" in text
