"""The import pipeline end to end on synthetic statements."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from fin_agent.agent.parse_run import ParseResult
from fin_agent.importers.commit import commit_import, undo_import
from fin_agent.importers.layout import detect_fingerprint
from fin_agent.importers.pipeline import _run_import, create_import, import_payload
from fin_agent.importers.readers import Doc
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import ledger_balance
from fin_agent.ledger.errors import InvalidLedgerError
from tests.import_fixtures import SBI_PARSER, sbi_csv


def _attachment(conn, payload: bytes) -> str:
    attachment_id = new_id(conn, "att")
    conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES (%s, 'statement', 'sbi.csv', 'text/csv', %s, %s, %s)
        """,
        (attachment_id, len(payload), hashlib.sha256(payload).hexdigest(), payload),
    )
    conn.commit()
    return attachment_id


def _account(conn) -> dict:
    return create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


def _save_layout(conn, account: dict) -> None:
    payload = sbi_csv()
    fingerprint = detect_fingerprint(Doc(payload))
    assert fingerprint
    conn.execute(
        """
        INSERT INTO statement_layouts
            (id, institution, fingerprint, parser, helper_version, last_used_at)
        VALUES (%s, %s, %s, %s, '1', now())
        """,
        (new_id(conn, "lay"), "SBI", fingerprint, SBI_PARSER),
    )
    conn.commit()


async def test_a_saved_layout_replays_with_no_model_and_commits(ledger):
    account = _account(ledger)
    _save_layout(ledger, account)
    attachment_id = _attachment(ledger, sbi_csv())
    row = create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    job = ledger.execute("SELECT * FROM jobs WHERE key = %s", (f"import:{row['id']}",)).fetchone()
    assert job is not None and job["status"] == "pending"

    await _run_import(row["id"])

    detail = import_payload(ledger, row["id"])
    assert detail["status"] == "review", detail
    assert detail["check"] == "VERIFIED"
    assert detail["counts"]["new"] == 3
    assert len(detail["lines"]) == 3

    result = commit_import(ledger, row["id"], categorize=False)
    assert result["status"] == "committed"
    detail = import_payload(ledger, row["id"])
    assert detail["status"] == "committed"
    assert detail["parser_saved"] is True

    attachment = ledger.execute(
        "SELECT data, purged_at FROM attachments WHERE import_id = %s", (row["id"],)
    ).fetchone()
    assert attachment["data"] is None and attachment["purged_at"] is not None

    account_row = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    assert ledger_balance(ledger, account_row["ledger_account_id"]) == 34402890
    assert account_row["statement_balance_paise"] == 34402890
    assert str(account_row["statement_balance_as_of"]) == "2026-04-30"

    undo = undo_import(ledger, row["id"])
    assert undo["status"] == "undone"
    account_row = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    # Every imported movement was reversed; the opening balance remains.
    assert ledger_balance(ledger, account_row["ledger_account_id"]) == 24567890
    assert account_row["statement_balance_paise"] == 24567890
    assert str(account_row["statement_balance_as_of"]) == "2026-03-31"


async def test_a_new_layout_runs_the_parser_then_saves_only_after_commit(ledger):
    account = _account(ledger)
    attachment_id = _attachment(ledger, sbi_csv())
    row = create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    async def fake_parse(doc, context, *, account_name=None):
        del doc, context, account_name
        return ParseResult(status="parser_submitted", script=SBI_PARSER)

    await _run_import(row["id"], parser_runner=fake_parse)
    detail = import_payload(ledger, row["id"])
    assert detail["status"] == "review", detail
    assert detail["parser_saved"] is False
    assert ledger.execute("SELECT count(*) AS n FROM statement_layouts").fetchone()["n"] == 0

    commit_import(ledger, row["id"], categorize=False)
    assert ledger.execute("SELECT count(*) AS n FROM statement_layouts").fetchone()["n"] == 1


async def test_statement_wins_on_date_and_keeps_labels_and_captures(ledger):
    from psycopg.types.json import Jsonb

    from fin_agent.importers.checks import check_statement
    from fin_agent.importers.reconcile import insert_lines, reconcile
    from fin_agent.ledger import movements as movements_mod

    account = _account(ledger)
    raw_account = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    category = ledger.execute("SELECT id FROM categories WHERE name = 'Food & Dining'").fetchone()
    movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="swiggy",
        category_id=category["id"],
        date=date(2026, 4, 1),
        capture={"kind": "chat", "ref": "msg_1", "upi_ref": "123456789012"},
    )
    ledger.commit()
    import_id = new_id(ledger, "imp")
    ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, commit_groups)
        VALUES (%s, 'hash-merge', 'review', %s, '2026-04-01', '2026-04-30',
                24567890, 24522890, '[]'::jsonb)
        """,
        (import_id, account["id"]),
    )
    insert_lines(
        ledger,
        import_id=import_id,
        account=raw_account,
        parsed_account={
            "rows": [
                {
                    "date": "2026-04-03",
                    "description": "UPI/DR/123456789012/SWIGGY/ramesh@okaxis",
                    "debit": 45000,
                    "credit": 0,
                    "balance": 24522890,
                    "upi_ref": "123456789012",
                }
            ]
        },
    )
    check = check_statement(
        [
            {
                "date": "2026-04-03",
                "debit": 45000,
                "credit": 0,
                "balance": 24522890,
            }
        ],
        24567890,
        24522890,
        period=("2026-04-01", "2026-04-30"),
        ledger_opening=24567890,
    )
    ledger.execute(
        "UPDATE imports SET check_result = %s WHERE id = %s",
        (Jsonb(check), import_id),
    )
    reconcile(
        ledger,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = ledger.execute(
        "SELECT * FROM statement_lines WHERE import_id = %s", (import_id,)
    ).fetchone()
    assert line["match_state"] == "certain"
    commit_import(ledger, import_id, categorize=False)
    corrected = ledger.execute(
        "SELECT * FROM movements WHERE id = %s", (line["movement_id"],)
    ).fetchone()
    assert str(corrected["date"]) == "2026-04-03"
    label = ledger.execute(
        "SELECT category_id FROM transfer_labels WHERE movement_id = %s",
        (corrected["id"],),
    ).fetchone()
    assert label["category_id"] == category["id"]
    captures = ledger.execute(
        "SELECT kind FROM captures WHERE movement_id = %s ORDER BY kind", (corrected["id"],)
    ).fetchall()
    assert {row["kind"] for row in captures} == {"chat", "statement_line"}


async def test_commit_adds_a_visible_equity_adjustment_and_queues_baselines(ledger):
    from psycopg.types.json import Jsonb

    from fin_agent.importers.checks import check_statement
    from fin_agent.importers.reconcile import insert_lines, reconcile
    from fin_agent.ledger import movements as movements_mod

    account = _account(ledger)
    raw_account = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    # A logged row the statement doesn't contain: the closing balance must still win.
    movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=5000,
        description="extra logged row",
        date=date(2026, 4, 10),
        capture={"kind": "chat", "ref": "msg_extra"},
    )
    ledger.commit()
    import_id = new_id(ledger, "imp")
    ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, commit_groups)
        VALUES (%s, 'hash-adjust', 'review', %s, '2026-04-01', '2026-04-30',
                24567890, 24522890, '[]'::jsonb)
        """,
        (import_id, account["id"]),
    )
    insert_lines(
        ledger,
        import_id=import_id,
        account=raw_account,
        parsed_account={
            "rows": [
                {
                    "date": "2026-04-03",
                    "description": "SWIGGY",
                    "debit": 45000,
                    "credit": 0,
                    "balance": 24522890,
                }
            ]
        },
    )
    check = check_statement(
        [{"date": "2026-04-03", "debit": 45000, "credit": 0, "balance": 24522890}],
        24567890,
        24522890,
        period=("2026-04-01", "2026-04-30"),
        ledger_opening=24567890,
    )
    ledger.execute("UPDATE imports SET check_result = %s WHERE id = %s", (Jsonb(check), import_id))
    reconcile(
        ledger,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    result = commit_import(ledger, import_id, categorize=False)
    assert result["adjustment_paise"] == 5000
    row = ledger.execute("SELECT * FROM accounts WHERE id = %s", (account["id"],)).fetchone()
    assert row["statement_balance_paise"] == 24522890
    adjustment = ledger.execute(
        "SELECT * FROM movements WHERE kind = 'adjustment' ORDER BY created_at DESC"
    ).fetchone()
    assert adjustment is not None
    queued = ledger.execute(
        "SELECT count(*) AS n FROM jobs WHERE kind = 'recompute_baselines' AND key LIKE %s",
        (f"%import:{import_id}",),
    ).fetchone()
    assert queued["n"] == 1


async def test_a_non_statement_ends_with_a_plain_answer(ledger):
    account = _account(ledger)
    attachment_id = _attachment(ledger, b"Salary slip\nNet pay 1,00,000\n")
    row = create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    async def fake_parse(doc, context, *, account_name=None):
        del doc, context, account_name
        return ParseResult(status="not_a_statement", kind="payslip")

    await _run_import(row["id"], parser_runner=fake_parse)
    detail = import_payload(ledger, row["id"])
    assert detail["status"] == "not_a_statement"
    assert "payslip" in detail["reason"]


async def test_a_failed_check_writes_no_rows(ledger):
    account = _account(ledger)
    attachment_id = _attachment(ledger, sbi_csv())
    row = create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    broken = SBI_PARSER.replace("'closing': closing", "'closing': 1")

    async def fake_parse(doc, context, *, account_name=None):
        del doc, context, account_name
        return ParseResult(status="parser_submitted", script=broken)

    await _run_import(row["id"], parser_runner=fake_parse)
    detail = import_payload(ledger, row["id"])
    assert detail["status"] == "failed"
    assert detail["lines"] == []
    with pytest.raises(InvalidLedgerError):
        commit_import(ledger, row["id"], categorize=False)
