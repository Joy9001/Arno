"""final2: the statement commit path before the stable release.

Four fixes, each failing before its change: a balance the user stated inside the statement's
period that the statement disagrees with (never replaced silently), an undone import counted as
ledger evidence (a later good statement could never commit), payment-rail tokens turned into a
payee and a global category rule, and a closing taken from the running balance with no visible
word on the review.
"""

from __future__ import annotations

from datetime import date

import pytest

from fin_agent.importers.checks import check_statement
from fin_agent.importers.commit import commit_import, undo_import
from fin_agent.importers.monty import normalize_accounts
from fin_agent.importers.periods import ledger_opening
from fin_agent.importers.pipeline import import_payload
from fin_agent.importers.reconcile import insert_lines, reconcile
from fin_agent.importers.rules import payee_key, pending_rows
from fin_agent.ledger.accounts import create_account, set_statement_balance
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import ledger_balance
from fin_agent.ledger.errors import ConflictLedgerError
from fin_agent.ledger.payees import is_placeholder_name
from tests.invariants import check_ledger

PERIOD = (date(2026, 10, 1), date(2026, 11, 10))
OPENING = 100_000  # ₹1,000.00, printed
CLOSING = 505_000  # ₹5,050.00
STATED = 500_000  # ₹5,000.00, stated by the user on 2026-11-10
CREDIT = CLOSING - OPENING


def _row(day: str, *, debit: int = 0, credit: int = 0, counterparty: str | None = None) -> dict:
    row = {
        "date": day,
        "description": "UPI/DR/555000111222/BIGBASKET GURGAON",
        "debit": debit,
        "credit": credit,
        "balance": None,
    }
    if counterparty is not None:
        row["counterparty"] = counterparty
    return row


async def _bank(conn, *, stated: int | None = STATED, as_of: date = PERIOD[1]) -> dict:
    created = await create_account(conn, kind="bank", name="HDFC Savings", institution="HDFC")
    if stated is not None:
        await set_statement_balance(conn, created["id"], balance_paise=stated, as_of=as_of)
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def _review(
    conn,
    account: dict,
    rows: list[dict],
    *,
    opening: int,
    closing: int | None,
    period: tuple[date, date] = PERIOD,
    check: dict | None = None,
) -> str:
    """One import in `review`, its check VERIFIED with the printed opening."""
    import_id = await new_id(conn, "imp")
    stored = check or {
        "status": "VERIFIED",
        "opening_source": "printed",
        "opening_paise": opening,
        "closing_paise": closing,
    }
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, check_result,
                             review, commit_groups)
        VALUES ($1, $2, 'review', $3, $4, $5, $6, $7, $8, '{}'::jsonb, '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
        period[0],
        period[1],
        opening,
        closing,
        stored,
    )
    fresh = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    await insert_lines(conn, import_id=import_id, account=fresh, parsed_account={"rows": rows})
    await reconcile(conn, import_id=import_id, account=fresh, period=period)
    return import_id


async def _spending_rows(conn) -> int:
    return int(await conn.fetchval("SELECT count(*) FROM v_spending"))


# 1 — a stated balance the statement disagrees with needs a confirmation -----------------------


async def test_a_stated_balance_that_agrees_commits_without_confirmation(ledger):
    """The normal first import: the user's figure equals the statement's, so nothing is asked."""
    account = await _bank(ledger, stated=CLOSING)
    import_id = await _review(
        ledger, account, [_row("2026-10-05", credit=CREDIT)], opening=OPENING, closing=CLOSING
    )
    detail = await import_payload(ledger, import_id)
    assert detail["stated_balance_reason"] is None

    result = await commit_import(ledger, import_id)
    assert result["rows"] == 1
    assert await ledger_balance(ledger, account["ledger_account_id"]) == CLOSING
    stored = await ledger.fetchrow(
        "SELECT statement_balance_paise, statement_balance_as_of FROM accounts WHERE id = $1",
        account["id"],
    )
    assert stored["statement_balance_paise"] == CLOSING
    assert stored["statement_balance_as_of"] == PERIOD[1]


async def test_a_stated_balance_inside_the_period_that_disagrees_needs_confirmation(ledger):
    account = await _bank(ledger)
    import_id = await _review(
        ledger, account, [_row("2026-10-05", credit=CREDIT)], opening=OPENING, closing=CLOSING
    )
    detail = await import_payload(ledger, import_id)
    reason = detail["stated_balance_reason"] or ""
    assert "₹5,000.00" in reason
    assert "₹5,050.00" in reason
    assert "2026-11-10" in reason
    assert detail["stated_balance_paise"] == STATED
    assert detail["closing_adjustment_paise"] == 0

    with pytest.raises(ConflictLedgerError) as refused:
        await commit_import(ledger, import_id)
    assert "₹5,000.00" in refused.value.message
    assert "₹5,050.00" in refused.value.message

    spending_before = await _spending_rows(ledger)
    committed = await commit_import(ledger, import_id, adjustment_confirmed=True)
    assert committed["rows"] == 1
    assert await ledger_balance(ledger, account["ledger_account_id"]) == CLOSING
    assert await _spending_rows(ledger) == spending_before
    assert await check_ledger(ledger) == []

    # Undo restores the stated figure and its own adjustment exactly.
    await undo_import(ledger, import_id)
    assert await ledger_balance(ledger, account["ledger_account_id"]) == STATED
    stored = await ledger.fetchrow(
        "SELECT statement_balance_paise, statement_balance_as_of FROM accounts WHERE id = $1",
        account["id"],
    )
    assert stored["statement_balance_paise"] == STATED
    assert stored["statement_balance_as_of"] == PERIOD[1]
    assert await check_ledger(ledger) == []


async def test_a_stated_balance_is_compared_on_its_own_day(ledger):
    """A figure that matches the statement's balance on its day is no disagreement."""
    account = await _bank(ledger, stated=110_000, as_of=date(2026, 10, 5))
    import_id = await _review(
        ledger,
        account,
        [
            _row("2026-10-05", credit=10_000),
            _row("2026-10-20", credit=CREDIT - 10_000),
        ],
        opening=OPENING,
        closing=CLOSING,
    )
    detail = await import_payload(ledger, import_id)
    assert detail["stated_balance_reason"] is None
    await commit_import(ledger, import_id)
    assert await ledger_balance(ledger, account["ledger_account_id"]) == CLOSING


# 2 — an undone import is not ledger evidence -------------------------------------------------


async def test_an_undone_import_is_not_ledger_evidence_for_the_next_statement(ledger):
    account = await _bank(ledger)
    first = await _review(
        ledger,
        account,
        [_row("2026-04-10", debit=100_000)],
        opening=100_000,
        closing=0,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    assert (await commit_import(ledger, first))["rows"] == 1
    await undo_import(ledger, first)

    # Before the fix this read {"paise": 0, ...} from the undone import's cancellation pairs, so
    # the printed opening of the next statement could never match.
    assert await ledger_opening(ledger, account["id"], date(2026, 5, 1)) is None

    rows = [_row("2026-05-15", credit=20_000)]
    previous = await ledger_opening(ledger, account["id"], date(2026, 5, 1))
    check = check_statement(
        rows,
        480_000,
        500_000,
        period=("2026-05-01", "2026-06-30"),
        ledger_opening=previous["paise"] if previous else None,
    )
    assert check["status"] == "VERIFIED"
    second = await _review(
        ledger,
        account,
        rows,
        opening=480_000,
        closing=500_000,
        period=(date(2026, 5, 1), date(2026, 6, 30)),
        check=check,
    )
    committed = await commit_import(ledger, second)
    assert committed["rows"] == 1
    assert await ledger_balance(ledger, account["ledger_account_id"]) == 500_000
    assert await check_ledger(ledger) == []


# 3 — payment-rail tokens are never a payee or a rule key -------------------------------------


@pytest.mark.parametrize(
    "name", ["DR", "cr", "UPI", "NEFT", "IMPS", "RTGS", "ACH", "NACH", "POS", "ATM"]
)
def test_a_payment_rail_token_is_a_placeholder_name(name):
    assert is_placeholder_name(name)


@pytest.mark.parametrize("name", ["Dr Reddy", "Positano", "Atmosphere Cafe"])
def test_a_name_that_only_starts_like_a_rail_token_is_real(name):
    assert not is_placeholder_name(name)


def test_a_rail_token_counterparty_falls_back_to_the_narration():
    narration = "UPI/DR/555000111222/BIGBASKET GURGAON"
    assert payee_key(narration, "DR") == "bigbasket gurgaon"
    assert payee_key("DR", None) == ""


def test_the_parser_helper_drops_a_rail_token_counterparty():
    accounts, reason = normalize_accounts(
        {
            "accounts": [
                {
                    "name": "HDFC",
                    "rows": [
                        {
                            "date": "2026-10-05",
                            "description": "UPI/DR/555000111222/BIGBASKET GURGAON",
                            "debit": 45000,
                            "credit": 0,
                            "counterparty": "DR",
                        }
                    ],
                }
            ]
        }
    )
    assert reason is None
    assert accounts[0]["rows"][0]["counterparty"] is None


async def test_a_rail_token_counterparty_creates_no_payee_and_no_rule_key(ledger):
    account = await _bank(ledger, stated=None)
    import_id = await _review(
        ledger,
        account,
        [_row("2026-10-05", debit=45_000, counterparty="DR")],
        opening=0,
        closing=-45_000,
    )
    await commit_import(ledger, import_id)

    assert (
        await ledger.fetchval("SELECT count(*) FROM payees WHERE lower(display_name) = 'dr'") == 0
    )
    keys = set(await pending_rows(ledger, import_id))
    assert "dr" not in keys
    assert "bigbasket gurgaon" in keys


# 4 — a closing taken from the rows is stated on the review ----------------------------------


async def test_a_closing_taken_from_the_running_balance_is_stated_on_the_review(ledger):
    account = await _bank(ledger, stated=None)
    import_id = await _review(
        ledger,
        account,
        [_row("2026-10-05", credit=CREDIT)],
        opening=OPENING,
        closing=CLOSING,
        check={
            "status": "VERIFIED",
            "opening_source": "printed",
            "opening_paise": OPENING,
            "closing_paise": CLOSING,
            "closing_source": "last running balance",
        },
    )
    detail = await import_payload(ledger, import_id)
    note = detail["closing_source_note"] or ""
    assert "₹5,050.00" in note
    assert "running balance" in note


async def test_a_printed_closing_needs_no_closing_source_note(ledger):
    account = await _bank(ledger, stated=None)
    import_id = await _review(
        ledger,
        account,
        [_row("2026-10-05", credit=CREDIT)],
        opening=OPENING,
        closing=CLOSING,
    )
    assert (await import_payload(ledger, import_id))["closing_source_note"] is None


# The driver prints the same two facts, so a live run says them without the web.


def test_the_driver_prints_a_stated_balance_conflict_and_needs_the_flag():
    from tests.e2e.driver import _render_import

    rendered = _render_import(
        {
            "import_id": "imp_1",
            "status": "review",
            "lines": [],
            "closing_adjustment_paise": 0,
            "stated_balance_paise": STATED,
            "stated_balance_as_of": "2026-11-10",
            "stated_balance_reason": (
                "You stated ₹5,000.00 on 2026-11-10; the statement shows ₹5,050.00 on that day."
            ),
        }
    )
    assert "stated balance: ₹5,000.00 as of 2026-11-10" in rendered
    assert "the statement shows ₹5,050.00" in rendered
    assert "ready: driver commit imp_1 --confirm-adjustment" in rendered


def test_the_driver_prints_a_closing_taken_from_the_running_balance():
    from tests.e2e.driver import _render_import

    rendered = _render_import(
        {
            "import_id": "imp_1",
            "status": "review",
            "lines": [],
            "closing_adjustment_paise": 0,
            "closing_source_note": "No printed closing balance was read on this statement.",
        }
    )
    assert "closing source: No printed closing balance was read" in rendered
    assert "ready: driver commit imp_1" in rendered
    assert "--confirm-adjustment" not in rendered
