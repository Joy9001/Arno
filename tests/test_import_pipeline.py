"""The import pipeline end to end on synthetic statements."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest
from psycopg.types.json import Jsonb
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from fin_agent.agent.parse_run import ParseResult
from fin_agent.importers.categorizer import categorize_import
from fin_agent.importers.commit import commit_import, undo_import
from fin_agent.importers.layout import detect_fingerprint
from fin_agent.importers.pipeline import (
    _account_fingerprint,
    _run_import,
    create_import,
    import_payload,
)
from fin_agent.importers.readers import Doc
from fin_agent.importers.reconcile import insert_lines, reconcile
from fin_agent.ledger.accounts import account_summary, create_account, set_statement_balance
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import ledger_balance, system_account
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError
from tests.import_fixtures import SBI_PARSER, sbi_csv


def test_a_card_without_headers_gets_a_stable_layout_key():
    account = {"kind": "card", "institution": "Slice", "name": "Slice card", "last4": "3753"}
    assert _account_fingerprint(account) == "card|Slice|3753"
    assert _account_fingerprint({**account, "last4": None}) is None


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


async def test_a_verified_import_anchors_an_unknown_account_and_undo_removes_it(ledger):
    account = create_account(
        ledger, kind="bank", name="SBI savings", institution="SBI", last4="6789"
    )
    assert account_summary(ledger)["cash_quality"]["status"] == "unknown"
    _save_layout(ledger, account)
    attachment_id = _attachment(ledger, sbi_csv())
    row = create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    await _run_import(row["id"])

    detail = import_payload(ledger, row["id"])
    assert detail["check"] == "VERIFIED"
    commit_import(ledger, row["id"], categorize=False)
    assert account_summary(ledger)["cash_quality"]["status"] == "known"
    account_row = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    assert account_row["statement_balance_paise"] == 34402890
    assert str(account_row["statement_balance_as_of"]) == "2026-04-30"

    undo_import(ledger, row["id"])

    assert account_summary(ledger)["cash_quality"]["status"] == "unknown"
    account_row = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    assert account_row["statement_balance_paise"] is None
    assert account_row["statement_balance_as_of"] is None


async def test_a_backdated_import_keeps_a_newer_statement_balance(ledger):
    account = _account(ledger)
    set_statement_balance(ledger, account["id"], balance_paise=30_000_000, as_of=date(2026, 5, 31))
    _save_layout(ledger, account)
    attachment_id = _attachment(ledger, sbi_csv())
    row = create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    await _run_import(row["id"])

    detail = import_payload(ledger, row["id"])
    assert detail["check"] == "VERIFIED"
    commit_import(ledger, row["id"], categorize=False)

    account_row = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    assert account_row["statement_balance_paise"] == 30_000_000
    assert str(account_row["statement_balance_as_of"]) == "2026-05-31"
    review = ledger.execute("SELECT review FROM imports WHERE id = %s", (row["id"],)).fetchone()[
        "review"
    ]
    assert review["statement_updated"] is False

    undo_import(ledger, row["id"])

    account_row = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    assert account_row["statement_balance_paise"] == 30_000_000
    assert str(account_row["statement_balance_as_of"]) == "2026-05-31"
    assert account_summary(ledger)["cash_quality"]["status"] == "known"


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


def _batch_model(calls: dict, answers: list[dict]) -> FunctionModel:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        del messages
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"response": answers})])

    return FunctionModel(respond)


def _review_import(conn, account: dict, rows: list[dict]) -> tuple[str, dict]:
    raw_account = conn.execute("SELECT * FROM accounts WHERE id = %s", (account["id"],)).fetchone()
    import_id = new_id(conn, "imp")
    conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             commit_groups)
        VALUES (%s, 'hash-categorize', 'review', %s, '2026-04-01', '2026-04-30', '[]'::jsonb)
        """,
        (import_id, account["id"]),
    )
    insert_lines(
        conn,
        import_id=import_id,
        account=raw_account,
        parsed_account={"rows": rows},
    )
    reconcile(
        conn,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    conn.commit()
    return import_id, raw_account


async def test_one_batched_call_categorizes_many_lines_sharing_keys(ledger):
    account = _account(ledger)
    import_id, raw_account = _review_import(
        ledger,
        account,
        [
            {
                "date": "2026-04-01",
                "description": "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
                "debit": 45000,
                "credit": 0,
                "balance": 24522890,
                "counterparty": "Swiggy",
            },
            {
                "date": "2026-04-02",
                "description": "UPI/DR/123456789013/SWIGGY BANGALORE/ramesh@okaxis",
                "debit": 30000,
                "credit": 0,
                "balance": 24492890,
                "counterparty": "Swiggy",
            },
            {
                "date": "2026-04-03",
                "description": "UPI/DR/223456789012/AMAZON PAY INDIA/amazon@okhdfcbank",
                "debit": 120000,
                "credit": 0,
                "balance": 24372890,
                "counterparty": "Amazon",
            },
        ],
    )
    calls = {"n": 0}
    counts = await categorize_import(
        ledger,
        import_id,
        raw_account,
        model=_batch_model(
            calls,
            [
                {"key": "swiggy", "category": "Food & Dining"},
                {"key": "amazon", "category": "Shopping"},
            ],
        ),
    )
    ledger.commit()

    assert calls["n"] == 1
    assert counts == {
        "lines": 3,
        "rule_hits": 0,
        "llm_keys": 2,
        "llm_calls": 1,
        "transfers": 0,
    }
    lines = ledger.execute(
        "SELECT match_detail FROM statement_lines WHERE import_id = %s ORDER BY date",
        (import_id,),
    ).fetchall()
    assert [line["match_detail"]["suggested_category"]["category_name"] for line in lines] == [
        "Food & Dining",
        "Food & Dining",
        "Shopping",
    ]
    rules = ledger.execute(
        "SELECT merchant_pattern FROM category_rules WHERE source = 'llm' ORDER BY merchant_pattern"
    ).fetchall()
    assert [row["merchant_pattern"] for row in rules] == ["amazon", "swiggy"]


async def test_an_own_account_answer_is_a_transfer_and_saves_no_rule(ledger):
    account = _account(ledger)
    other = create_account(
        ledger,
        kind="card",
        name="Slice card",
        institution="Slice",
        last4="4242",
        opening_balance_paise=0,
        opening_balance_as_of=date(2026, 3, 31),
        credit_limit_paise=500000,
        statement_day=25,
        due_day=5,
    )
    ledger.commit()
    import_id, raw_account = _review_import(
        ledger,
        account,
        [
            {
                "date": "2026-04-02",
                "description": "UPI/DR/123456789012/SLICE CARD/self",
                "debit": 45000,
                "credit": 0,
                "balance": 24522890,
                "counterparty": "Slice Card",
            }
        ],
    )
    calls = {"n": 0}
    counts = await categorize_import(
        ledger,
        import_id,
        raw_account,
        model=_batch_model(
            calls,
            [
                {
                    "key": "slice card",
                    "category": "Bills & Utilities",
                    "own_account_id": other["id"],
                }
            ],
        ),
    )
    ledger.commit()

    assert counts["transfers"] == 1
    detail = ledger.execute(
        "SELECT match_detail FROM statement_lines WHERE import_id = %s", (import_id,)
    ).fetchone()["match_detail"]
    assert detail["suggested_transfer_account_id"] == other["id"]
    assert "suggested_category" not in detail
    assert ledger.execute("SELECT count(*) AS n FROM category_rules").fetchone()["n"] == 0


async def test_an_exact_rule_hit_skips_the_model(ledger):
    account = _account(ledger)
    category = ledger.execute("SELECT id FROM categories WHERE name = 'Food & Dining'").fetchone()
    ledger.execute(
        """
        INSERT INTO category_rules (merchant_pattern, category_id, source)
        VALUES ('swiggy', %s, 'user')
        """,
        (category["id"],),
    )
    import_id, raw_account = _review_import(
        ledger,
        account,
        [
            {
                "date": "2026-04-01",
                "description": "UPI/DR/123456789012/SWIGGY/ramesh@okaxis",
                "debit": 45000,
                "credit": 0,
                "balance": 24522890,
                "counterparty": "Swiggy",
            }
        ],
    )

    def explode(messages, info: AgentInfo) -> ModelResponse:
        del messages, info
        raise AssertionError("the model must not be called for a rule hit")

    counts = await categorize_import(ledger, import_id, raw_account, model=FunctionModel(explode))
    ledger.commit()

    assert counts["rule_hits"] == 1
    assert counts["llm_keys"] == 0
    assert counts["llm_calls"] == 0
    line = ledger.execute(
        "SELECT match_detail FROM statement_lines WHERE import_id = %s", (import_id,)
    ).fetchone()
    assert line["match_detail"]["suggested_category"]["source"] == "rule"


async def test_a_blocked_categorizer_still_reaches_review(ledger):
    account = _account(ledger)
    attachment_id = _attachment(ledger, sbi_csv())
    row = create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    async def fake_parse(doc, context, *, account_name=None):
        del doc, context, account_name
        return ParseResult(status="parser_submitted", script=SBI_PARSER)

    await _run_import(row["id"], parser_runner=fake_parse)
    detail = import_payload(ledger, row["id"])

    assert detail["status"] == "review"
    assert detail["lines"] != []
    assert all(line["suggested_category"] is None for line in detail["lines"])


async def test_commit_books_an_own_account_line_as_a_transfer(ledger, monkeypatch):
    account = _account(ledger)
    other = create_account(
        ledger,
        kind="bank",
        name="Axis savings",
        institution="Axis",
        last4="1111",
        opening_balance_paise=100000,
        opening_balance_as_of=date(2026, 3, 31),
    )
    ledger.commit()
    spending = system_account(ledger, "spending")
    spending_before = ledger_balance(ledger, spending["ledger_account_id"])
    food = ledger.execute("SELECT id FROM categories WHERE name = 'Food & Dining'").fetchone()

    import_id, raw_account = _review_import(
        ledger,
        account,
        [
            {
                "date": "2026-04-02",
                "description": "UPI/DR/123456789012/SBI CARD/self",
                "debit": 45000,
                "credit": 0,
                "balance": 24522890,
                "counterparty": "SBI Card",
            },
            {
                "date": "2026-04-03",
                "description": "AMAZON PAY INDIA",
                "debit": 120000,
                "credit": 0,
                "balance": 24402890,
                "counterparty": "Amazon",
            },
        ],
    )
    lines = ledger.execute(
        "SELECT id FROM statement_lines WHERE import_id = %s ORDER BY date", (import_id,)
    ).fetchall()
    ledger.execute(
        """
        UPDATE statement_lines
        SET match_detail = coalesce(match_detail, '{}'::jsonb) || %s
        WHERE id = %s
        """,
        (Jsonb({"suggested_transfer_account_id": other["id"]}), lines[0]["id"]),
    )
    ledger.execute(
        """
        UPDATE statement_lines
        SET match_detail = coalesce(match_detail, '{}'::jsonb) || %s
        WHERE id = %s
        """,
        (
            Jsonb(
                {
                    "suggested_category": {
                        "category_id": str(food["id"]),
                        "category_name": "Food & Dining",
                        "source": "llm",
                    }
                }
            ),
            lines[1]["id"],
        ),
    )
    ledger.execute(
        "UPDATE imports SET check_result = %s WHERE id = %s",
        (Jsonb({"status": "VERIFIED"}), import_id),
    )
    ledger.commit()

    def no_model(*args, **kwargs):
        del args, kwargs
        raise AssertionError("commit must not call the model")

    monkeypatch.setattr("fin_agent.importers.categorizer.Agent", no_model)
    with pytest.raises(ConflictLedgerError, match="Confirm every payment"):
        commit_import(ledger, import_id, categorize=True)

    ledger.execute(
        """
        UPDATE statement_lines
        SET match_detail = coalesce(match_detail, '{}'::jsonb) || %s
        WHERE id = %s
        """,
        (Jsonb({"transfer_confirmed": True}), lines[0]["id"]),
    )
    ledger.commit()
    commit_import(ledger, import_id, categorize=True)

    account_row = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    other_after = ledger.execute("SELECT * FROM accounts WHERE id = %s", (other["id"],)).fetchone()
    assert ledger_balance(ledger, account_row["ledger_account_id"]) == 24567890 - 45000 - 120000
    assert ledger_balance(ledger, other_after["ledger_account_id"]) == 100000 + 45000
    assert ledger_balance(ledger, spending["ledger_account_id"]) == spending_before + 120000

    first = ledger.execute(
        """
        SELECT m.kind FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.id = %s
        """,
        (lines[0]["id"],),
    ).fetchone()
    second = ledger.execute(
        """
        SELECT m.kind, m.id FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.id = %s
        """,
        (lines[1]["id"],),
    ).fetchone()
    assert first["kind"] == "transfer"
    assert second["kind"] == "expense"
    label = ledger.execute(
        "SELECT category_id FROM transfer_labels WHERE movement_id = %s", (second["id"],)
    ).fetchone()
    assert label["category_id"] == food["id"]

    undo_import(ledger, import_id)
    account_row = ledger.execute(
        "SELECT * FROM accounts WHERE id = %s", (account["id"],)
    ).fetchone()
    assert ledger_balance(ledger, account_row["ledger_account_id"]) == 24567890
