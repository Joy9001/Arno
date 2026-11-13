"""Stage 2 tests: single database definition of movement reversal state."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from arno.db.conn import connect
from arno.engine.patterns import _load_charges
from arno.importers.baselines import recompute_baselines
from arno.importers.categorize import _counts as categorize_counts
from arno.importers.commit import _book_adjustment, _superseded_anchors
from arno.importers.periods import ledger_opening
from arno.importers.reconcile import insert_lines, reconcile
from arno.importers.rules import apply_rules, check_payees, pending_payees
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id, undo_group, write_group
from arno.ledger.common import today
from arno.ledger.errors import ConflictLedgerError
from arno.ledger.movements import (
    _effectively_reversed_ids,
    _interest_booked,
    _reversal_state,
    correct_movement,
    ledger_span,
    list_movements,
    record_expense,
    record_loan_payment,
    reverse_movement,
)
from arno.ledger.payees import create_payee
from arno.ledger.uncategorized import uncategorized_rows
from arno.tools.projection import _spend_categories
from arno.tools.read import query_movements, summarize_spending
from arno.tools.recaps import _spending_days, recap


async def _bank(conn, *, name: str = "Stage2 Bank", opening: int = 10_000_000) -> dict:
    return await create_account(conn, kind="bank", name=name, opening_balance_paise=opening)


async def _category(conn, name: str) -> str:
    return str(await conn.fetchval("SELECT id FROM categories WHERE name = $1 AND active", name))


async def _sql_reversal_map(conn, as_of: date | None = None) -> dict[str, bool]:
    rows = await conn.fetch(
        "SELECT movement_id, is_reversed FROM movement_reversal_state($1)",
        as_of,
    )
    return {str(row["movement_id"]): bool(row["is_reversed"]) for row in rows}


async def _assert_helpers_match_sql(conn, ids: list[str], *, as_of: date | None = None) -> None:
    sql_map = await _sql_reversal_map(conn, as_of=as_of)
    py_map = await _reversal_state(conn, ids, as_of=as_of)
    assert {mid: py_map[mid]["reversed"] for mid in ids} == {
        mid: sql_map.get(mid, False) for mid in ids
    }
    if as_of is None:
        eff_rev = await _effectively_reversed_ids(conn)
        assert eff_rev == sorted(mid for mid, rev in sql_map.items() if rev)
        view_rows = await conn.fetch(
            "SELECT movement_id, is_reversal, is_reversed, is_effective "
            "FROM v_movement_state WHERE movement_id = ANY($1::text[])",
            ids,
        )
        for row in view_rows:
            mid = str(row["movement_id"])
            assert row["is_reversed"] == sql_map.get(mid, False)
            assert row["is_effective"] == (not row["is_reversal"] and not row["is_reversed"])


async def test_reversal_state_equivalence_across_deep_chains_and_as_of(ledger):
    bank = await _bank(ledger)
    food = await _category(ledger, "Food & Dining")
    d0 = date(2026, 1, 5)
    m = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=25_000,
        description="Chain Coffee",
        category_id=food,
        date=d0,
    )
    m_id = m["id"]
    await _assert_helpers_match_sql(ledger, [m_id])

    async with write_group(ledger, actor="user") as g1:
        r1 = await reverse_movement(g1, m_id, date=date(2026, 1, 10))
    ids = [m_id, r1.id]
    assert (await _sql_reversal_map(ledger)) == {m_id: True, r1.id: False}
    await _assert_helpers_match_sql(ledger, ids)

    async with write_group(ledger, actor="user") as g2:
        r2 = await reverse_movement(g2, r1.id, date=date(2026, 1, 15))
    ids.append(r2.id)
    assert (await _sql_reversal_map(ledger)) == {m_id: False, r1.id: True, r2.id: False}
    await _assert_helpers_match_sql(ledger, ids)

    async with write_group(ledger, actor="user") as g3:
        r3 = await reverse_movement(g3, m_id, date=date(2026, 1, 20))
    ids.append(r3.id)
    assert (await _sql_reversal_map(ledger)) == {
        m_id: True,
        r1.id: True,
        r2.id: False,
        r3.id: False,
    }
    await _assert_helpers_match_sql(ledger, ids)

    async with write_group(ledger, actor="user") as g4:
        r4 = await reverse_movement(g4, r3.id, date=date(2026, 1, 25))
    ids.append(r4.id)
    assert (await _sql_reversal_map(ledger)) == {
        m_id: False,
        r1.id: True,
        r2.id: False,
        r3.id: True,
        r4.id: False,
    }
    for probe in (
        date(2026, 1, 4),
        date(2026, 1, 5),
        date(2026, 1, 10),
        date(2026, 1, 15),
        date(2026, 1, 20),
        date(2026, 1, 25),
    ):
        await _assert_helpers_match_sql(ledger, ids, as_of=probe)


async def test_reverse_movement_locks_target_row_against_concurrent_reversals(ledger):
    bank = await _bank(ledger)
    created = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=45_000,
        description="Concurrent Target",
        date=today(),
    )
    entered_second = asyncio.Event()

    async with connect() as conn1, connect() as conn2:

        async def _second_reversal() -> None:
            async with write_group(conn2, actor="user") as g2:
                entered_second.set()
                await reverse_movement(g2, created["id"], note="second")

        async with write_group(conn1, actor="user") as g1:
            await reverse_movement(g1, created["id"], note="first")
            task = asyncio.create_task(_second_reversal())
            await entered_second.wait()
            await asyncio.sleep(0.05)
            # conn2 must block on SELECT ... FOR UPDATE until conn1 commits.
            assert not task.done()

        with pytest.raises(ConflictLedgerError, match="already corrected or removed"):
            await task

    reversals = await ledger.fetchval(
        "SELECT count(*) FROM movements WHERE reverses_id = $1",
        created["id"],
    )
    assert reversals == 1


async def test_uncategorized_and_read_tools_restore_after_undo_of_undo(ledger):
    bank = await _bank(ledger, opening=0)
    day = today()
    m = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=33_000,
        description="Uncategorized Chai",
        date=day,
    )
    assert [row["id"] for row in await uncategorized_rows(ledger)] == [m["id"]]

    deleted = await correct_movement(ledger, m["id"], changes={"delete": True})
    assert await uncategorized_rows(ledger) == []
    q_hidden = await query_movements(ledger, filters={"period": day.isoformat()})
    assert [row["id"] for row in q_hidden["rows"]] == []
    q_all = await query_movements(
        ledger, filters={"period": day.isoformat(), "include_reversed": True}
    )
    assert len(q_all["rows"]) == 2
    assert (await list_movements(ledger, limit=20))["items"] == []
    span_rev = await ledger_span(ledger)
    assert span_rev == {"movements": 1, "spending": 1, "first_date": day.isoformat()}
    sum_rev = await summarize_spending(ledger, group_by="none", period=day.isoformat())
    assert sum_rev["total"]["paise"] == 0
    assert sum_rev["groups"] == []

    # Reversal of reversal restores M cleanly everywhere.
    await undo_group(ledger, deleted["undo_id"])
    assert [row["id"] for row in await uncategorized_rows(ledger)] == [m["id"]]
    q_restored = await query_movements(ledger, filters={"period": day.isoformat()})
    assert [row["id"] for row in q_restored["rows"]] == [m["id"]]
    assert [row["id"] for row in (await list_movements(ledger, limit=20))["items"]] == [m["id"]]
    sum_restored = await summarize_spending(ledger, group_by="none", period=day.isoformat())
    assert sum_restored["total"]["paise"] == 33_000
    assert sum_restored["groups"][0]["count"] == 1


async def test_importers_and_categorize_use_reversal_state_across_undo_of_undo(ledger):
    account = await _bank(ledger, name="HDFC Import", opening=500_000)
    acc_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    food = await _category(ledger, "Food & Dining")

    # Opening balance movement before the statement period (M -> R1 -> R2).
    m_open = await record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=10_000,
        description="March coffee",
        category_id=food,
        date=date(2026, 3, 20),
    )
    del_open = await correct_movement(ledger, m_open["id"], changes={"delete": True})
    assert await ledger_opening(ledger, account["id"], date(2026, 4, 1)) is None
    await undo_group(ledger, del_open["undo_id"])
    opened = await ledger_opening(ledger, account["id"], date(2026, 4, 1))
    assert opened is not None and opened["as_of"] == "2026-03-20"

    # Adjustment anchor inside period (M -> R1 -> R2) for _superseded_anchors.
    adj_group = await _book_adjustment(
        ledger,
        acc_row,
        amount=5_000,
        day=date(2026, 4, 2),
        description="Anchor check",
    )
    adj_id = await ledger.fetchval(
        "SELECT id FROM movements WHERE kind = 'adjustment' AND description = 'Anchor check'"
    )
    assert [
        row["id"]
        for row in await _superseded_anchors(
            ledger, acc_row["ledger_account_id"], date(2026, 4, 1), date(2026, 4, 30)
        )
    ] == [adj_id]
    undone_adj = await undo_group(ledger, adj_group)
    assert (
        await _superseded_anchors(
            ledger, acc_row["ledger_account_id"], date(2026, 4, 1), date(2026, 4, 30)
        )
        == []
    )
    # Undo of undo on the adjustment restores it as an effective anchor.
    async with write_group(ledger, actor="user") as g_adj_r2:
        rev_adj_id = await ledger.fetchval(
            "SELECT id FROM movements WHERE reverses_id = $1", adj_id
        )
        await reverse_movement(g_adj_r2, str(rev_adj_id), note="Restore anchor")
    assert undone_adj["status"] == "undone"
    assert [
        row["id"]
        for row in await _superseded_anchors(
            ledger, acc_row["ledger_account_id"], date(2026, 4, 1), date(2026, 4, 30)
        )
    ] == [adj_id]

    # Reconcile candidates + logged_not_on_statement across M -> R1 -> R2.
    m_chat = await record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=25_000,
        description="Chat dosa",
        date=date(2026, 4, 5),
        capture={"kind": "chat", "ref": "msg_chat", "upi_ref": "999888777666"},
    )
    m_extra = await record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=12_000,
        description="Logged not on statement",
        date=date(2026, 4, 20),
        capture={"kind": "chat", "ref": "msg_extra"},
    )
    del_chat = await correct_movement(ledger, m_chat["id"], changes={"delete": True})
    del_extra = await correct_movement(ledger, m_extra["id"], changes={"delete": True})

    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             commit_groups)
        VALUES ($1, 'h-s2', 'review', $2, '2026-04-01', '2026-04-30', '[]'::jsonb)
        """,
        import_id,
        account["id"],
    )
    await insert_lines(
        ledger,
        import_id=import_id,
        account=acc_row,
        parsed_account={
            "rows": [
                {
                    "date": "2026-04-05",
                    "description": "UPI/DR/999888777666/DOSA CORNER",
                    "debit": 25_000,
                    "credit": 0,
                    "balance": 453_000,
                    "upi_ref": "999888777666",
                }
            ],
            "headers": ["Date", "Narration", "Debit", "Credit", "Balance"],
        },
    )
    rev_while_reversed = await reconcile(
        ledger,
        import_id=import_id,
        account=acc_row,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    counts = await ledger.fetchrow("SELECT counts FROM imports WHERE id = $1", import_id)
    assert counts["counts"]["new"] == 1
    assert rev_while_reversed["logged_not_on_statement"] == []

    # Restore both movements via reversal-of-reversal and re-reconcile.
    await undo_group(ledger, del_chat["undo_id"])
    await undo_group(ledger, del_extra["undo_id"])
    rev_after_restore = await reconcile(
        ledger,
        import_id=import_id,
        account=acc_row,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    counts = await ledger.fetchrow("SELECT counts FROM imports WHERE id = $1", import_id)
    assert counts["counts"]["certain"] == 1
    assert [row["movement_id"] for row in rev_after_restore["logged_not_on_statement"]] == [
        m_extra["id"]
    ]

    # Categorize counts, rules._import_rows / pending_payees, check_payees, _refund_source.
    m_debit = await record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=40_000,
        description="UPI/DR/111222333444/ACME LOCAL SHOP",
        category_id=food,
        date=date(2026, 4, 10),
    )
    m_refund_target = await record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=40_000,
        description="UPI/CR/555666777888/ACME LOCAL SHOP",
        date=date(2026, 4, 12),
    )
    line_debit_id = await new_id(ledger, "sl")
    line_refund_id = await new_id(ledger, "sl")
    await ledger.execute(
        """
        INSERT INTO statement_lines
            (id, import_id, date, description_raw, amount_paise, content_hash,
             match_state, movement_id, match_detail)
        VALUES
            ($1, $3, '2026-04-10', 'UPI/DR/111222333444/ACME LOCAL SHOP', -40000, 'hd',
             'merged', $4, '{}'::jsonb),
            ($2, $3, '2026-04-12', 'UPI/CR/555666777888/ACME LOCAL SHOP', 40000, 'hr',
             'merged', $5, '{}'::jsonb)
        """,
        line_debit_id,
        line_refund_id,
        import_id,
        m_debit["id"],
        m_refund_target["id"],
    )
    del_debit = await correct_movement(ledger, m_debit["id"], changes={"delete": True})
    assert await categorize_counts(ledger, import_id) == (0, 1)
    assert [g["key"] for g in await pending_payees(ledger, import_id)] == ["acme local shop"]
    samples_rev, known_rev = await check_payees(ledger, import_id, 5)
    assert samples_rev == [] and known_rev == {}
    # Reversed debit must NOT match as refund source.
    res_rev = await apply_rules(ledger, import_id)
    assert res_rev["labeled"] == 0

    # Undo reversal of m_debit -> m_debit becomes effective again -> counts, check_payees, refund.
    await undo_group(ledger, del_debit["undo_id"])
    assert await categorize_counts(ledger, import_id) == (1, 1)
    samples_restored, known_restored = await check_payees(ledger, import_id, 5)
    assert [s["key"] for s in samples_restored] == ["acme local shop"]
    assert known_restored["acme local shop"] == "Food & Dining"
    res_restored = await apply_rules(ledger, import_id)
    assert res_restored["labeled"] == 1


async def test_baselines_projection_patterns_recaps_and_loan_interest_across_undo_of_undo(ledger):
    bank = await _bank(ledger, name="Engine Bank", opening=20_000_000)
    groceries = await _category(ledger, "Groceries")
    netflix = await create_payee(ledger, display_name="Netflix", kind="merchant")
    day = today()
    d_month_start = day.replace(day=1)
    d1 = d_month_start
    d2 = d_month_start + timedelta(days=1) if d_month_start.day < 28 else d_month_start
    d3 = d_month_start + timedelta(days=2) if d_month_start.day < 27 else d1

    # 1. Baselines + Projection + Patterns + Recaps across M -> R1 -> R2.
    m1 = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=15_000,
        description="Netflix Monthly",
        category_id=groceries,
        payee_id=netflix["id"],
        date=d1,
    )
    m2 = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=15_000,
        description="Netflix Monthly",
        category_id=groceries,
        payee_id=netflix["id"],
        date=d2,
    )
    m3 = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=15_000,
        description="Netflix Monthly",
        category_id=groceries,
        payee_id=netflix["id"],
        date=d3,
    )
    # Also one uncategorized expense in the same window to exercise projection's uncategorized gap.
    m_uncat = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=99_000,
        description="Big Uncat Spend",
        payee_id=netflix["id"],
        date=d1,
    )

    del_m1 = await correct_movement(ledger, m1["id"], changes={"delete": True})
    del_uncat = await correct_movement(ledger, m_uncat["id"], changes={"delete": True})

    as_of_after = d3 + timedelta(days=1)
    base_rev = await recompute_baselines(ledger, as_of=as_of_after)
    assert base_rev["computed"] == 0
    spend_cats_rev, _, gaps_rev = await _spend_categories(
        ledger, as_of=as_of_after, covered_category_ids=set()
    )
    assert all("uncategorized spending" not in g for g in gaps_rev)
    assert any("Groceries has only 2 real spending days" in g for g in gaps_rev)
    assert all(c.name != "Groceries" for c in spend_cats_rev)
    charges_rev = await _load_charges(ledger, as_of=as_of_after)
    assert len(charges_rev) == 2

    span_str = day.strftime("%Y-%m")
    recap_rev = await recap(ledger, span_str)
    biggest_rev = next(s for s in recap_rev["stories"] if s["key"] == "biggest")
    assert biggest_rev["rows"]["key"] in {m2["id"], m3["id"]}
    days_rev = await _spending_days(ledger, d_month_start, d3)
    assert days_rev == {d2, d3}

    await undo_group(ledger, del_m1["undo_id"])
    await undo_group(ledger, del_uncat["undo_id"])
    assert await _spending_days(ledger, d_month_start, d3) == {d1, d2, d3}

    base_res = await recompute_baselines(ledger, as_of=as_of_after)
    assert base_res["computed"] == 1
    spend_cats_res, _, gaps_res = await _spend_categories(
        ledger, as_of=as_of_after, covered_category_ids=set()
    )
    assert any(
        c.name == "Groceries" and sum(amt for _, amt in c.history) == 45_000 for c in spend_cats_res
    )
    assert any("uncategorized spending" in g for g in gaps_res)
    charges_res = await _load_charges(ledger, as_of=as_of_after)
    assert len(charges_res) == 4
    recap_res = await recap(ledger, span_str)
    biggest_res = next(s for s in recap_res["stories"] if s["key"] == "biggest")
    assert biggest_res["rows"]["key"] == m_uncat["id"]

    # 2. _interest_booked on loan payments across M -> R1 -> R2.
    loan = await create_account(
        ledger,
        kind="loan",
        name="Home Loan",
        opening_balance_paise=-1_000_000,
        rate_bps=1200,
        tenure_months=12,
    )
    loan_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", loan["id"])
    m_loan = await record_loan_payment(
        ledger,
        account_id=bank["id"],
        loan_account_id=loan["id"],
        amount_paise=50_000,
        principal_paise=40_000,
        interest_paise=10_000,
        date=d1,
    )
    assert await _interest_booked(ledger, loan_row, d1) is True
    del_loan = await correct_movement(ledger, m_loan["id"], changes={"delete": True})
    assert await _interest_booked(ledger, loan_row, d1) is False
    await undo_group(ledger, del_loan["undo_id"])
    assert await _interest_booked(ledger, loan_row, d1) is True
