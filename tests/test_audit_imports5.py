"""Audit regressions for statement import: a movement logged after the review, the closing
adjustment, a future period, a refund's date, card fees, and a "not a statement" upload.

Every test here is a bug found in the release audit and must fail before its fix.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from arno.agent.parser import ParseReport
from arno.importers.checks import check_statement
from arno.importers.commit import commit_import
from arno.importers.pipeline import import_payload
from arno.importers.reconcile import insert_lines, reconcile
from arno.importers.rules import apply_rules, match_rule
from arno.importers.seed_rules import SEED_RULES, ensure_seed_rules
from arno.importers.task_status import NOT_A_STATEMENT_REASON, settle_imports
from arno.ledger import movements as movements_mod
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from arno.ledger.common import ledger_balance, today
from arno.ledger.errors import ConflictLedgerError

PERIOD = (date(2026, 4, 1), date(2026, 4, 30))


async def _bank(conn, *, name: str = "SBI Salary", opening_paise: int = 29020000) -> dict:
    created = await create_account(
        conn,
        kind="bank",
        name=name,
        institution="SBI",
        last4="6789",
        opening_balance_paise=opening_paise,
        opening_balance_as_of=date(2026, 3, 31),
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def _card(conn, *, last4: str = "4242") -> dict:
    created = await create_account(
        conn,
        kind="card",
        name=f"Slice card {last4}",
        institution="Slice",
        last4=last4,
        credit_limit_paise=10000000,
        statement_day=25,
        due_day=5,
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


def _row(description: str, *, day: str, debit: int = 0, credit: int = 0) -> dict:
    return {
        "date": day,
        "description": description,
        "debit": debit,
        "credit": credit,
        "balance": None,
    }


async def _review(
    conn,
    account: dict,
    rows: list[dict],
    *,
    opening: int,
    closing: int | None,
    period: tuple[date, date] = PERIOD,
) -> str:
    """One import in `review`: verified against a stated opening, lines reconciled."""
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
        period[0],
        period[1],
        opening,
        closing,
        {
            "opening_source": "manual",
            "opening_paise": opening,
            "closing_paise": closing,
        },
        {"manual_opening_paise": opening},
    )
    await insert_lines(conn, import_id=import_id, account=account, parsed_account={"rows": rows})
    await reconcile(conn, import_id=import_id, account=account, period=period)
    return import_id


async def _net(conn, account: dict, movement_id: str) -> int:
    """The movement's signed effect on the account, in paise."""
    return int(
        await conn.fetchval(
            """
            SELECT coalesce(sum(
                CASE WHEN t.from_account_id = $1 THEN -t.amount
                     WHEN t.to_account_id = $2 THEN t.amount
                     ELSE 0 END
            ), 0)::bigint
            FROM movement_legs ml
            JOIN pgledger_transfers t ON t.id = ml.transfer_id
            WHERE ml.movement_id = $3
            """,
            account["ledger_account_id"],
            account["ledger_account_id"],
            movement_id,
        )
    )


async def _movements(conn, account: dict, kind: str) -> list[dict]:
    return await conn.fetch(
        """
        SELECT * FROM movements
        WHERE kind = $1
          AND EXISTS (
              SELECT 1 FROM movement_legs ml
              JOIN pgledger_transfers t ON t.id = ml.transfer_id
              WHERE ml.movement_id = movements.id
                AND (t.from_account_id = $2 OR t.to_account_id = $3)
          )
        ORDER BY date, id
        """,
        kind,
        account["ledger_account_id"],
        account["ledger_account_id"],
    )


# N1: a movement logged after the review ----------------------------------------------------


async def test_a_movement_logged_after_the_review_merges_instead_of_booking_twice(ledger):
    account = await _bank(ledger)
    import_id = await _review(
        ledger,
        account,
        [
            _row("NEFT CR/ACME PAYROLL/1234", day="2026-04-05", credit=10000000),
            _row("UPI/DR/1/SWIGGY", day="2026-04-10", debit=45000),
            _row("UPI/DR/2/OLA", day="2026-04-12", debit=30000),
        ],
        opening=29020000,
        closing=38945000,
    )
    detail = await import_payload(ledger, import_id)
    assert detail["counts"]["new"] == 3

    # Chat logs the salary after the statement reached review.
    logged = await movements_mod.record_income(
        ledger,
        account_id=account["id"],
        amount_paise=10000000,
        description="Salary from Acme (April)",
        date=date(2026, 4, 5),
        capture={"kind": "chat", "ref": "msg_salary"},
    )

    result = await commit_import(ledger, import_id)

    incomes = await _movements(ledger, account, "income")
    assert [str(row["id"]) for row in incomes] == [str(logged["id"])]
    assert result["adjustment_paise"] == 0
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 38945000
    line = await ledger.fetchrow(
        "SELECT match_state, movement_id FROM statement_lines "
        "WHERE import_id = $1 AND amount_paise = 10000000",
        import_id,
    )
    assert line["match_state"] == "merged"
    assert str(line["movement_id"]) == str(logged["id"])
    # No opaque closing adjustment: the statement's own rows are the ledger's sum.
    adjustments = [
        row for row in await _movements(ledger, account, "adjustment") if row["date"] >= PERIOD[0]
    ]
    assert adjustments == []


async def test_a_new_identical_amount_on_another_day_stays_its_own_income(ledger):
    account = await _bank(ledger)
    import_id = await _review(
        ledger,
        account,
        [
            _row("NEFT CR/ACME PAYROLL/1234", day="2026-04-05", credit=10000000),
            _row("UPI/DR/1/SWIGGY", day="2026-04-10", debit=45000),
        ],
        opening=29020000,
        closing=38975000,
    )
    # Same amount, 20 days later: outside the ±3 day window, so it is a second income.
    second = await movements_mod.record_income(
        ledger,
        account_id=account["id"],
        amount_paise=10000000,
        description="Salary from Elsewhere (April)",
        date=date(2026, 4, 25),
        capture={"kind": "chat", "ref": "msg_second"},
    )

    detail = await import_payload(ledger, import_id)
    assert detail["closing_adjustment_paise"] == -10000000
    with pytest.raises(ConflictLedgerError):
        await commit_import(ledger, import_id)

    await commit_import(ledger, import_id, adjustment_confirmed=True)

    incomes = await _movements(ledger, account, "income")
    assert [(row["date"], str(row["id"])) for row in incomes] == [
        (date(2026, 4, 5), incomes[0]["id"]),
        (date(2026, 4, 25), str(second["id"])),
    ]
    assert str(incomes[0]["id"]) != str(second["id"])
    adjustments = [
        row for row in await _movements(ledger, account, "adjustment") if row["date"] >= PERIOD[0]
    ]
    # The difference is booked, but visibly and only because the user confirmed it.
    assert len(adjustments) == 1
    assert await _net(ledger, account, str(adjustments[0]["id"])) == -10000000
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 38975000


async def test_two_movements_logged_after_the_review_send_the_line_back_to_review(ledger):
    account = await _bank(ledger)
    import_id = await _review(
        ledger,
        account,
        [
            _row("UPI/DR/1/SWIGGY", day="2026-04-10", debit=45000),
            _row("UPI/DR/2/OLA", day="2026-04-12", debit=30000),
        ],
        opening=29020000,
        closing=28975000,
    )
    for day in (date(2026, 4, 10), date(2026, 4, 11)):
        await movements_mod.record_expense(
            ledger,
            account_id=account["id"],
            amount_paise=45000,
            description="Swiggy, logged by chat",
            date=day,
        )

    with pytest.raises(ConflictLedgerError) as refused:
        await commit_import(ledger, import_id)

    assert "logged after this statement reached review" in refused.value.message
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "review"
    detail = await import_payload(ledger, import_id)
    line = next(row for row in detail["lines"] if row["amount_paise"] == -45000)
    assert line["match_state"] == "ambiguous"
    assert len(line["candidates"]) == 2
    # Nothing was written by the refused commit.
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 29020000 - 90000


# R5: a non-zero closing adjustment is the user's call --------------------------------------


async def test_a_closing_adjustment_is_stated_on_the_review_and_needs_confirmation(ledger):
    account = await _bank(ledger, name="Kotak savings", opening_paise=0)
    period = (date(2026, 9, 1), date(2026, 9, 30))
    first = await _review(
        ledger,
        account,
        [_row("OPENING DEPOSIT", day="2026-09-05", credit=100000)],
        opening=0,
        closing=100000,
        period=period,
    )
    assert (await commit_import(ledger, first))["adjustment_paise"] == 0

    second = await _review(
        ledger,
        account,
        [_row("SOMETHING ELSE", day="2026-09-10", debit=5000)],
        opening=0,
        closing=107000,
        period=period,
    )
    detail = await import_payload(ledger, second)
    assert detail["closing_adjustment_paise"] == 12000
    assert "₹120.00" in (detail["closing_adjustment_reason"] or "")
    assert detail["status"] == "review"

    with pytest.raises(ConflictLedgerError) as refused:
        await commit_import(ledger, second)
    assert "₹120.00" in refused.value.message

    committed = await commit_import(ledger, second, adjustment_confirmed=True)
    assert committed["adjustment_paise"] == 12000
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 107000


# R7: a statement whose period has not ended ------------------------------------------------


async def test_a_period_that_has_not_ended_is_refused(ledger):
    end = today() + timedelta(days=20)
    rows = [
        {"date": end.isoformat(), "debit": 45000, "credit": 0, "balance": 955000},
    ]
    result = check_statement(
        rows,
        1000000,
        955000,
        period=((end - timedelta(days=10)).isoformat(), end.isoformat()),
    )
    assert result["status"] == "DISCREPANCY"
    assert "has not ended yet" in result["reason"]
    assert end.isoformat() in result["reason"]

    # A period that ends today is a real, finished statement.
    finished = check_statement(
        [{"date": today().isoformat(), "debit": 45000, "credit": 0, "balance": 955000}],
        1000000,
        955000,
        period=((today() - timedelta(days=10)).isoformat(), today().isoformat()),
    )
    assert finished["status"] == "VERIFIED"


# F5: a refund merge takes the statement's posted date ---------------------------------------


async def test_a_refund_merge_takes_the_statements_date(ledger):
    account = await _card(ledger)
    period = (date(2026, 10, 25), date(2026, 11, 10))
    logged = await movements_mod.record_refund(
        ledger,
        account_id=account["id"],
        amount_paise=20000,
        description="Croma refunded 200 to the Slice card",
        date=date(2026, 11, 10),
    )
    import_id = await _review(
        ledger,
        account,
        [_row("CROMA REFUND", day="2026-11-09", credit=20000)],
        opening=0,
        closing=20000,
        period=period,
    )
    detail = await import_payload(ledger, import_id)
    assert detail["lines"][0]["match_state"] == "certain"
    # The review says which date the merge replaces, before it replaces it.
    assert detail["lines"][0]["date"] == "2026-11-09"
    assert detail["lines"][0]["ledger_date"] == "2026-11-10"

    await commit_import(ledger, import_id)

    refunds = await _movements(ledger, account, "refund")
    assert [str(row["id"]) for row in refunds] == [str(logged["id"])]
    assert refunds[0]["date"] == date(2026, 11, 9)
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 20000


# F7: a card fee is a fee, not a guess ------------------------------------------------------


def _seed_rule(pattern: str, match: str = "contains") -> dict:
    return {"match": match, "pattern": pattern, "source": "seed", "priority": 0}


def test_card_fee_narrations_reach_fees_and_charges():
    rules = [
        {"id": str(index), **_seed_rule(pattern, match), "category_id": "c", "category_name": name}
        for index, (name, match, pattern) in enumerate(SEED_RULES)
    ]
    for narration in (
        "ANNUAL FEE",
        "ANNUAL FEE CHARGES",
        "GST ON FEES",
        "IGST ON FEES",
        "SURCHARGE",
        "INTEREST CHARGES",
    ):
        rule = match_rule(narration.lower(), rules)
        assert rule is not None, narration
        assert rule["category_name"] == "Fees & Charges", narration


async def test_an_imported_card_fee_is_categorized_by_a_rule(ledger):
    await ensure_seed_rules(ledger)
    account = await _card(ledger)
    import_id = await _review(
        ledger,
        account,
        [_row("ANNUAL FEE", day="2026-11-10", debit=99900)],
        opening=0,
        closing=-99900,
        period=(date(2026, 10, 25), date(2026, 11, 10)),
    )
    await commit_import(ledger, import_id)

    counts = await apply_rules(ledger, import_id)
    assert counts["labeled"] == 1
    fee = await ledger.fetchrow(
        """
        SELECT c.name
        FROM statement_lines sl
        JOIN movement_legs tl ON tl.movement_id = sl.movement_id
        JOIN categories c ON c.id = tl.category_id
        WHERE sl.import_id = $1
        """,
        import_id,
    )
    assert fee is not None and fee["name"] == "Fees & Charges"


# R6: a file that is not a statement --------------------------------------------------------


async def test_a_parser_that_says_not_a_statement_settles_the_card_plainly(ledger):
    account = await _bank(ledger)
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, task_id, commit_groups)
        VALUES ($1, $2, 'parsing', $3, 'task_1', '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
    )
    report = ParseReport(
        status="not_a_statement",
        reason="The uploaded file is an employee payslip, not a bank statement.",
    )

    changed = await settle_imports(
        ledger, task_id="task_1", outcome="ok", output=report.model_dump_json()
    )

    assert changed == 1
    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "not_a_statement"
    assert row["reason"] == NOT_A_STATEMENT_REASON
    assert "without reading this file" not in row["reason"]
