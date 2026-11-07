"""The import pipeline end to end on synthetic statements."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest
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
    retry_with_opening,
)
from fin_agent.importers.readers import Doc
from fin_agent.importers.reconcile import insert_lines, reconcile, resolve_line
from fin_agent.ledger.accounts import account_summary, create_account, set_statement_balance
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import ledger_balance, system_account
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError
from tests.import_fixtures import SBI_PARSER, sbi_csv


def test_a_card_without_headers_gets_a_stable_layout_key():
    account = {"kind": "card", "institution": "Slice", "name": "Slice card", "last4": "3753"}
    assert _account_fingerprint(account) == "card|Slice|3753"
    assert _account_fingerprint({**account, "last4": None}) is None


async def _attachment(conn, payload: bytes) -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', 'sbi.csv', 'text/csv', $2, $3, $4)
        """,
        attachment_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


async def _account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


async def _save_layout(conn, account: dict) -> None:
    payload = sbi_csv()
    fingerprint = detect_fingerprint(Doc(payload))
    assert fingerprint
    await conn.execute(
        """
        INSERT INTO statement_layouts
            (id, institution, fingerprint, parser, helper_version, last_used_at)
        VALUES ($1, $2, $3, $4, '1', now())
        """,
        await new_id(conn, "lay"),
        "SBI",
        fingerprint,
        SBI_PARSER,
    )


async def test_a_saved_layout_replays_with_no_model_and_commits(ledger):
    account = await _account(ledger)
    await _save_layout(ledger, account)
    attachment_id = await _attachment(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    job = await ledger.fetchrow("SELECT * FROM jobs WHERE key = $1", f"import:{row['id']}")
    assert job is not None and job["status"] == "pending"

    await _run_import(row["id"])

    detail = await import_payload(ledger, row["id"])
    assert detail["status"] == "review", detail
    assert detail["check"] == "VERIFIED"
    assert detail["counts"]["new"] == 3
    assert len(detail["lines"]) == 3

    result = await commit_import(ledger, row["id"], categorize=False)
    assert result["status"] == "committed"
    detail = await import_payload(ledger, row["id"])
    assert detail["status"] == "committed"
    assert detail["parser_saved"] is True

    attachment = await ledger.fetchrow(
        "SELECT data, purged_at FROM attachments WHERE import_id = $1", row["id"]
    )
    assert attachment["data"] is None and attachment["purged_at"] is not None

    account_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert await ledger_balance(ledger, account_row["ledger_account_id"]) == 34402890
    assert account_row["statement_balance_paise"] == 34402890
    assert str(account_row["statement_balance_as_of"]) == "2026-04-30"

    undo = await undo_import(ledger, row["id"])
    assert undo["status"] == "undone"
    account_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    # Every imported movement was reversed; the opening balance remains.
    assert await ledger_balance(ledger, account_row["ledger_account_id"]) == 24567890
    assert account_row["statement_balance_paise"] == 24567890
    assert str(account_row["statement_balance_as_of"]) == "2026-03-31"


async def test_a_verified_import_anchors_an_unknown_account_and_undo_removes_it(ledger):
    account = await create_account(
        ledger, kind="bank", name="SBI savings", institution="SBI", last4="6789"
    )
    assert (await account_summary(ledger))["cash_quality"]["status"] == "unknown"
    await _save_layout(ledger, account)
    attachment_id = await _attachment(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    await _run_import(row["id"])

    detail = await import_payload(ledger, row["id"])
    assert detail["check"] == "VERIFIED"
    await commit_import(ledger, row["id"], categorize=False)
    assert (await account_summary(ledger))["cash_quality"]["status"] == "known"
    account_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert account_row["statement_balance_paise"] == 34402890
    assert str(account_row["statement_balance_as_of"]) == "2026-04-30"

    await undo_import(ledger, row["id"])

    assert (await account_summary(ledger))["cash_quality"]["status"] == "unknown"
    account_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert account_row["statement_balance_paise"] is None
    assert account_row["statement_balance_as_of"] is None


async def test_a_backdated_import_keeps_a_newer_statement_balance(ledger):
    account = await _account(ledger)
    await set_statement_balance(
        ledger, account["id"], balance_paise=30_000_000, as_of=date(2026, 5, 31)
    )
    await _save_layout(ledger, account)
    attachment_id = await _attachment(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    await _run_import(row["id"])

    detail = await import_payload(ledger, row["id"])
    assert detail["check"] == "VERIFIED"
    await commit_import(ledger, row["id"], categorize=False)

    account_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert account_row["statement_balance_paise"] == 30_000_000
    assert str(account_row["statement_balance_as_of"]) == "2026-05-31"
    review = (await ledger.fetchrow("SELECT review FROM imports WHERE id = $1", row["id"]))[
        "review"
    ]
    assert review["statement_updated"] is False

    await undo_import(ledger, row["id"])

    account_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert account_row["statement_balance_paise"] == 30_000_000
    assert str(account_row["statement_balance_as_of"]) == "2026-05-31"
    assert (await account_summary(ledger))["cash_quality"]["status"] == "known"


async def test_a_new_layout_runs_the_parser_then_saves_only_after_commit(ledger):
    account = await _account(ledger)
    attachment_id = await _attachment(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    async def fake_parse(doc, context, *, account_name=None):
        del doc, context, account_name
        return ParseResult(status="parser_submitted", script=SBI_PARSER)

    await _run_import(row["id"], parser_runner=fake_parse)
    detail = await import_payload(ledger, row["id"])
    assert detail["status"] == "review", detail
    assert detail["parser_saved"] is False
    assert await ledger.fetchval("SELECT count(*) FROM statement_layouts") == 0

    await commit_import(ledger, row["id"], categorize=False)
    assert await ledger.fetchval("SELECT count(*) FROM statement_layouts") == 1


async def test_statement_wins_on_date_and_keeps_labels_and_captures(ledger):
    from fin_agent.importers.checks import check_statement
    from fin_agent.ledger import movements as movements_mod

    account = await _account(ledger)
    raw_account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    category = await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Food & Dining'")
    await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="swiggy",
        category_id=category["id"],
        date=date(2026, 4, 1),
        capture={"kind": "chat", "ref": "msg_1", "upi_ref": "123456789012"},
    )
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, commit_groups)
        VALUES ($1, 'hash-merge', 'review', $2, '2026-04-01', '2026-04-30',
                24567890, 24522890, '[]'::jsonb)
        """,
        import_id,
        account["id"],
    )
    await insert_lines(
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
    await ledger.execute(
        "UPDATE imports SET check_result = $1 WHERE id = $2",
        check,
        import_id,
    )
    await reconcile(
        ledger,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = await ledger.fetchrow("SELECT * FROM statement_lines WHERE import_id = $1", import_id)
    assert line["match_state"] == "certain"
    await commit_import(ledger, import_id, categorize=False)
    corrected = await ledger.fetchrow("SELECT * FROM movements WHERE id = $1", line["movement_id"])
    assert str(corrected["date"]) == "2026-04-03"
    label = await ledger.fetchrow(
        "SELECT category_id FROM transfer_labels WHERE movement_id = $1",
        corrected["id"],
    )
    assert label["category_id"] == category["id"]
    captures = await ledger.fetch(
        "SELECT kind FROM captures WHERE movement_id = $1 ORDER BY kind", corrected["id"]
    )
    assert {row["kind"] for row in captures} == {"chat", "statement_line"}


async def test_commit_adds_a_visible_equity_adjustment_and_queues_baselines(ledger):
    from fin_agent.importers.checks import check_statement
    from fin_agent.ledger import movements as movements_mod

    account = await _account(ledger)
    raw_account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    # A logged row the statement doesn't contain: the closing balance must still win.
    await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=5000,
        description="extra logged row",
        date=date(2026, 4, 10),
        capture={"kind": "chat", "ref": "msg_extra"},
    )
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, commit_groups)
        VALUES ($1, 'hash-adjust', 'review', $2, '2026-04-01', '2026-04-30',
                24567890, 24522890, '[]'::jsonb)
        """,
        import_id,
        account["id"],
    )
    await insert_lines(
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
    await ledger.execute("UPDATE imports SET check_result = $1 WHERE id = $2", check, import_id)
    await reconcile(
        ledger,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    result = await commit_import(ledger, import_id, categorize=False)
    assert result["adjustment_paise"] == 5000
    row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert row["statement_balance_paise"] == 24522890
    adjustment = await ledger.fetchrow(
        "SELECT * FROM movements WHERE kind = 'adjustment' ORDER BY created_at DESC"
    )
    assert adjustment is not None
    queued = await ledger.fetchval(
        "SELECT count(*) FROM jobs WHERE kind = 'recompute_baselines' AND key LIKE $1",
        f"%import:{import_id}",
    )
    assert queued == 1


async def test_a_non_statement_ends_with_a_plain_answer(ledger):
    account = await _account(ledger)
    attachment_id = await _attachment(ledger, b"Salary slip\nNet pay 1,00,000\n")
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    async def fake_parse(doc, context, *, account_name=None):
        del doc, context, account_name
        return ParseResult(status="not_a_statement", kind="payslip")

    await _run_import(row["id"], parser_runner=fake_parse)
    detail = await import_payload(ledger, row["id"])
    assert detail["status"] == "not_a_statement"
    assert "payslip" in detail["reason"]


async def test_a_failed_check_writes_no_rows(ledger):
    account = await _account(ledger)
    attachment_id = await _attachment(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    broken = SBI_PARSER.replace("'closing': closing", "'closing': 1")

    async def fake_parse(doc, context, *, account_name=None):
        del doc, context, account_name
        return ParseResult(status="parser_submitted", script=broken)

    await _run_import(row["id"], parser_runner=fake_parse)
    detail = await import_payload(ledger, row["id"])
    assert detail["status"] == "failed"
    assert detail["lines"] == []
    with pytest.raises(InvalidLedgerError):
        await commit_import(ledger, row["id"], categorize=False)


def _batch_model(calls: dict, answers: list[dict]) -> FunctionModel:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        del messages
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"response": answers})])

    return FunctionModel(respond)


async def _review_import(conn, account: dict, rows: list[dict]) -> tuple[str, dict]:
    raw_account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             commit_groups)
        VALUES ($1, 'hash-categorize', 'review', $2, '2026-04-01', '2026-04-30', '[]'::jsonb)
        """,
        import_id,
        account["id"],
    )
    await insert_lines(
        conn,
        import_id=import_id,
        account=raw_account,
        parsed_account={"rows": rows},
    )
    await reconcile(
        conn,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    return import_id, raw_account


async def test_one_batched_call_categorizes_many_lines_sharing_keys(ledger):
    account = await _account(ledger)
    import_id, raw_account = await _review_import(
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

    assert calls["n"] == 1
    assert counts == {
        "lines": 3,
        "rule_hits": 0,
        "llm_keys": 2,
        "llm_calls": 1,
        "transfers": 0,
    }
    lines = await ledger.fetch(
        "SELECT match_detail FROM statement_lines WHERE import_id = $1 ORDER BY date",
        import_id,
    )
    assert [line["match_detail"]["suggested_category"]["category_name"] for line in lines] == [
        "Food & Dining",
        "Food & Dining",
        "Shopping",
    ]
    rules = await ledger.fetch(
        "SELECT merchant_pattern FROM category_rules WHERE source = 'llm' ORDER BY merchant_pattern"
    )
    assert [row["merchant_pattern"] for row in rules] == ["amazon", "swiggy"]


async def test_an_own_account_answer_is_a_transfer_and_saves_no_rule(ledger):
    account = await _account(ledger)
    other = await create_account(
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
    import_id, raw_account = await _review_import(
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

    assert counts["transfers"] == 1
    detail = (
        await ledger.fetchrow(
            "SELECT match_detail FROM statement_lines WHERE import_id = $1", import_id
        )
    )["match_detail"]
    assert detail["suggested_transfer_account_id"] == other["id"]
    assert "suggested_category" not in detail
    assert await ledger.fetchval("SELECT count(*) FROM category_rules") == 0


async def test_an_exact_rule_hit_skips_the_model(ledger):
    account = await _account(ledger)
    category = await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Food & Dining'")
    await ledger.execute(
        """
        INSERT INTO category_rules (merchant_pattern, category_id, source)
        VALUES ('swiggy', $1, 'user')
        """,
        category["id"],
    )
    import_id, raw_account = await _review_import(
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

    assert counts["rule_hits"] == 1
    assert counts["llm_keys"] == 0
    assert counts["llm_calls"] == 0
    line = await ledger.fetchrow(
        "SELECT match_detail FROM statement_lines WHERE import_id = $1", import_id
    )
    assert line["match_detail"]["suggested_category"]["source"] == "rule"


async def test_a_blocked_categorizer_still_reaches_review(ledger):
    account = await _account(ledger)
    attachment_id = await _attachment(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])

    async def fake_parse(doc, context, *, account_name=None):
        del doc, context, account_name
        return ParseResult(status="parser_submitted", script=SBI_PARSER)

    await _run_import(row["id"], parser_runner=fake_parse)
    detail = await import_payload(ledger, row["id"])

    assert detail["status"] == "review"
    assert detail["lines"] != []
    assert all(line["suggested_category"] is None for line in detail["lines"])


async def test_commit_books_an_own_account_line_as_a_transfer(ledger, monkeypatch):
    account = await _account(ledger)
    other = await create_account(
        ledger,
        kind="bank",
        name="Axis savings",
        institution="Axis",
        last4="1111",
        opening_balance_paise=100000,
        opening_balance_as_of=date(2026, 3, 31),
    )
    spending = await system_account(ledger, "spending")
    spending_before = await ledger_balance(ledger, spending["ledger_account_id"])
    food = await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Food & Dining'")

    import_id, _raw_account = await _review_import(
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
    lines = await ledger.fetch(
        "SELECT id FROM statement_lines WHERE import_id = $1 ORDER BY date", import_id
    )
    await ledger.execute(
        """
        UPDATE statement_lines
        SET match_detail = coalesce(match_detail, '{}'::jsonb) || $1
        WHERE id = $2
        """,
        {"suggested_transfer_account_id": other["id"]},
        lines[0]["id"],
    )
    await ledger.execute(
        """
        UPDATE statement_lines
        SET match_detail = coalesce(match_detail, '{}'::jsonb) || $1
        WHERE id = $2
        """,
        {
            "suggested_category": {
                "category_id": str(food["id"]),
                "category_name": "Food & Dining",
                "source": "llm",
            }
        },
        lines[1]["id"],
    )
    await ledger.execute(
        "UPDATE imports SET check_result = $1, review = $2 WHERE id = $3",
        {"status": "VERIFIED", "opening_source": "manual", "opening_paise": 0},
        {"manual_opening_paise": 0},
        import_id,
    )

    def no_model(*args, **kwargs):
        del args, kwargs
        raise AssertionError("commit must not call the model")

    monkeypatch.setattr("fin_agent.importers.categorizer.Agent", no_model)
    with pytest.raises(ConflictLedgerError, match="Confirm every payment"):
        await commit_import(ledger, import_id, categorize=True)

    await ledger.execute(
        """
        UPDATE statement_lines
        SET match_detail = coalesce(match_detail, '{}'::jsonb) || $1
        WHERE id = $2
        """,
        {"transfer_confirmed": True},
        lines[0]["id"],
    )
    await commit_import(ledger, import_id, categorize=True)

    account_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    other_after = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", other["id"])
    assert (
        await ledger_balance(ledger, account_row["ledger_account_id"]) == 24567890 - 45000 - 120000
    )
    assert await ledger_balance(ledger, other_after["ledger_account_id"]) == 100000 + 45000
    assert await ledger_balance(ledger, spending["ledger_account_id"]) == spending_before + 120000

    first = await ledger.fetchrow(
        """
        SELECT m.kind FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.id = $1
        """,
        lines[0]["id"],
    )
    second = await ledger.fetchrow(
        """
        SELECT m.kind, m.id FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.id = $1
        """,
        lines[1]["id"],
    )
    assert first["kind"] == "transfer"
    assert second["kind"] == "expense"
    label = await ledger.fetchrow(
        "SELECT category_id FROM transfer_labels WHERE movement_id = $1", second["id"]
    )
    assert label["category_id"] == food["id"]

    await undo_import(ledger, import_id)
    account_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    assert await ledger_balance(ledger, account_row["ledger_account_id"]) == 24567890


async def _card(conn, *, opening_paise: int | None = 0, last4: str = "4242") -> dict:
    return await create_account(
        conn,
        kind="card",
        name=f"Slice card {last4}",
        institution="Slice",
        last4=last4,
        opening_balance_paise=opening_paise,
        opening_balance_as_of=date(2026, 3, 31),
        credit_limit_paise=500000,
        statement_day=25,
        due_day=5,
    )


async def _verify(conn, import_id: str, opening_paise: int = 0) -> None:
    row = await conn.fetchrow("SELECT review FROM imports WHERE id = $1", import_id)
    review = dict(row["review"] or {})
    review["manual_opening_paise"] = opening_paise
    await conn.execute(
        "UPDATE imports SET check_result = $1, review = $2 WHERE id = $3",
        {
            "status": "VERIFIED",
            "opening_source": "manual",
            "opening_paise": opening_paise,
        },
        review,
        import_id,
    )


async def _line(conn, import_id: str, index: int = 0) -> dict:
    return (
        await conn.fetch(
            "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id", import_id
        )
    )[index]


async def test_a_card_refund_offsets_spending_and_never_becomes_income(ledger):
    card = await _card(ledger)
    spending = await system_account(ledger, "spending")
    before = await ledger_balance(ledger, spending["ledger_account_id"])
    food = await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Food & Dining'")
    import_id, _ = await _review_import(
        ledger,
        card,
        [{"date": "2026-04-04", "description": "Merchant refund", "debit": 0, "credit": 10395}],
    )
    await _verify(ledger, import_id)
    line = await _line(ledger, import_id)

    await resolve_line(
        ledger,
        import_id=import_id,
        line_id=line["id"],
        resolution="classify_credit",
        credit_kind="refund",
        category_id=str(food["id"]),
    )
    await commit_import(ledger, import_id, categorize=False)

    assert await ledger_balance(ledger, spending["ledger_account_id"]) == before - 10395
    card_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", card["id"])
    assert await ledger_balance(ledger, card_row["ledger_account_id"]) == 10395
    movement = await ledger.fetchrow(
        """
        SELECT m.id, m.kind FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.id = $1
        """,
        line["id"],
    )
    assert movement["kind"] == "refund"
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'income'") == 0
    label = await ledger.fetchrow(
        "SELECT category_id FROM transfer_labels WHERE movement_id = $1", movement["id"]
    )
    assert label["category_id"] == food["id"]


async def test_a_card_repayment_debits_the_chosen_bank_and_reduces_the_card_liability(ledger):
    bank = await create_account(
        ledger,
        kind="bank",
        name="Axis savings",
        last4="1111",
        opening_balance_paise=100000,
        opening_balance_as_of=date(2026, 3, 31),
    )
    card = await _card(ledger, opening_paise=-100000)
    spending = await system_account(ledger, "spending")
    spending_before = await ledger_balance(ledger, spending["ledger_account_id"])
    import_id, _ = await _review_import(
        ledger,
        card,
        [{"date": "2026-04-05", "description": "Repayment", "debit": 0, "credit": 42800}],
    )
    await _verify(ledger, import_id)
    line = await _line(ledger, import_id)

    await resolve_line(
        ledger,
        import_id=import_id,
        line_id=line["id"],
        resolution="classify_credit",
        credit_kind="transfer",
        source_account_id=bank["id"],
    )
    await commit_import(ledger, import_id, categorize=False)

    bank_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", bank["id"])
    card_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", card["id"])
    assert await ledger_balance(ledger, bank_row["ledger_account_id"]) == 100000 - 42800
    assert await ledger_balance(ledger, card_row["ledger_account_id"]) == -100000 + 42800
    assert await ledger_balance(ledger, spending["ledger_account_id"]) == spending_before
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'income'") == 0
    movement = await ledger.fetchrow(
        """
        SELECT m.kind FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.id = $1
        """,
        line["id"],
    )
    assert movement["kind"] == "transfer"


async def test_an_unclassified_card_credit_blocks_the_whole_commit_before_writes(ledger):
    card = await _card(ledger)
    import_id, _ = await _review_import(
        ledger,
        card,
        [
            {"date": "2026-04-01", "description": "Blinkit", "debit": 42800, "credit": 0},
            {"date": "2026-04-02", "description": "Refund", "debit": 0, "credit": 10395},
        ],
    )
    await _verify(ledger, import_id)

    with pytest.raises(ConflictLedgerError, match="card credit"):
        await commit_import(ledger, import_id, categorize=False)

    assert await ledger.fetchval("SELECT count(*) FROM movements") == 0
    lines = await ledger.fetch(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    assert all(line["match_state"] == "new" and line["movement_id"] is None for line in lines)


async def test_a_card_repayment_without_a_funding_account_blocks_before_writes(ledger):
    card = await _card(ledger)
    import_id, _ = await _review_import(
        ledger,
        card,
        [
            {"date": "2026-04-01", "description": "Blinkit", "debit": 42800, "credit": 0},
            {"date": "2026-04-02", "description": "Repayment", "debit": 0, "credit": 20000},
        ],
    )
    credit = await _line(ledger, import_id, index=1)
    await ledger.execute(
        """
        UPDATE statement_lines
        SET match_detail = coalesce(match_detail, '{}'::jsonb) || $1
        WHERE id = $2
        """,
        {"credit_kind": "transfer"},
        credit["id"],
    )
    await _verify(ledger, import_id)

    with pytest.raises(ConflictLedgerError, match="card credit"):
        await commit_import(ledger, import_id, categorize=False)

    assert await ledger.fetchval("SELECT count(*) FROM movements") == 0
    lines = await ledger.fetch(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    assert all(line["match_state"] == "new" and line["movement_id"] is None for line in lines)


async def test_a_card_repayment_rejects_a_missing_same_or_system_funding_account(ledger):
    card = await _card(ledger)
    import_id, _ = await _review_import(
        ledger,
        card,
        [{"date": "2026-04-05", "description": "Repayment", "debit": 0, "credit": 42800}],
    )
    line = await _line(ledger, import_id)
    income = await system_account(ledger, "income")

    for source_account_id in ("acc_missing", card["id"], income["id"]):
        with pytest.raises(ValueError, match="account"):
            await resolve_line(
                ledger,
                import_id=import_id,
                line_id=line["id"],
                resolution="classify_credit",
                credit_kind="transfer",
                source_account_id=source_account_id,
            )
    stored = (
        await ledger.fetchrow("SELECT match_detail FROM statement_lines WHERE id = $1", line["id"])
    )["match_detail"]
    assert "credit_kind" not in (stored or {})


async def test_explicit_other_income_is_still_possible_for_a_card_credit(ledger):
    card = await _card(ledger)
    spending = await system_account(ledger, "spending")
    spending_before = await ledger_balance(ledger, spending["ledger_account_id"])
    import_id, _ = await _review_import(
        ledger,
        card,
        [{"date": "2026-04-06", "description": "Cashback", "debit": 0, "credit": 5000}],
    )
    await _verify(ledger, import_id)
    line = await _line(ledger, import_id)

    await resolve_line(
        ledger,
        import_id=import_id,
        line_id=line["id"],
        resolution="classify_credit",
        credit_kind="income",
    )
    await commit_import(ledger, import_id, categorize=False)

    movement = await ledger.fetchrow(
        """
        SELECT m.kind FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.id = $1
        """,
        line["id"],
    )
    assert movement["kind"] == "income"
    assert await ledger_balance(ledger, spending["ledger_account_id"]) == spending_before


async def test_a_confirmed_suggested_transfer_on_a_card_credit_still_commits(ledger):
    bank = await create_account(
        ledger,
        kind="bank",
        name="Axis savings",
        last4="1111",
        opening_balance_paise=100000,
        opening_balance_as_of=date(2026, 3, 31),
    )
    card = await _card(ledger)
    import_id, _ = await _review_import(
        ledger,
        card,
        [{"date": "2026-04-05", "description": "Repayment", "debit": 0, "credit": 42800}],
    )
    line = await _line(ledger, import_id)
    await ledger.execute(
        """
        UPDATE statement_lines
        SET match_detail = coalesce(match_detail, '{}'::jsonb) || $1
        WHERE id = $2
        """,
        {"suggested_transfer_account_id": bank["id"], "transfer_confirmed": True},
        line["id"],
    )
    await _verify(ledger, import_id)

    await commit_import(ledger, import_id, categorize=False)

    movement = await ledger.fetchrow(
        """
        SELECT m.kind FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.id = $1
        """,
        line["id"],
    )
    assert movement["kind"] == "transfer"
    bank_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", bank["id"])
    assert await ledger_balance(ledger, bank_row["ledger_account_id"]) == 100000 - 42800


async def _assumed_zero_review(conn, account: dict, rows: list[dict], closing: int) -> str:
    """A saved review shaped like the F12 case: source label says printed, no evidence."""
    raw_account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, check_result,
                             commit_groups)
        VALUES ($1, 'hash-assumed-zero', 'review', $2, '2026-04-01', '2026-04-30',
                0, $3, $4, '[]'::jsonb)
        """,
        import_id,
        account["id"],
        closing,
        {
            "status": "VERIFIED",
            "opening_source": "printed",
            "opening_paise": 0,
            "closing_paise": closing,
            "chain_ok": None,
            "rows": len(rows),
        },
    )
    await insert_lines(
        conn,
        import_id=import_id,
        account=raw_account,
        parsed_account={"rows": rows},
    )
    await reconcile(
        conn,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    return import_id


async def test_a_saved_printed_zero_opening_without_evidence_blocks_commit(ledger):
    card = await _card(ledger, opening_paise=None)
    import_id = await _assumed_zero_review(
        ledger,
        card,
        [{"date": "2026-04-01", "description": "Blinkit", "debit": 42800, "credit": 0}],
        closing=-42800,
    )

    detail = await import_payload(ledger, import_id)
    assert detail["check"] == "VERIFIED"
    assert detail["opening_source"] == "printed"
    assert detail["opening_evidenced"] is False
    assert detail["opening_required"] is True

    with pytest.raises(ConflictLedgerError, match="opening balance"):
        await commit_import(ledger, import_id, categorize=False)
    assert await ledger.fetchval("SELECT count(*) FROM movements") == 0


async def test_manual_zero_opening_is_accepted_and_labeled_manual(ledger):
    card = await _card(ledger, opening_paise=None)
    import_id = await _assumed_zero_review(
        ledger,
        card,
        [{"date": "2026-04-01", "description": "Blinkit", "debit": 42800, "credit": 0}],
        closing=-42800,
    )

    await retry_with_opening(ledger, import_id=import_id, opening_paise=0)

    detail = await import_payload(ledger, import_id)
    assert detail["check"] == "VERIFIED"
    assert detail["opening_source"] == "manual"
    assert detail["opening_paise"] == 0
    assert detail["opening_evidenced"] is True
    assert detail["opening_required"] is False
    assert (await commit_import(ledger, import_id, categorize=False))["status"] == "committed"


async def test_manual_opening_recheck_preserves_card_credit_decisions(ledger):
    card = await _card(ledger, opening_paise=None)
    food = await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Food & Dining'")
    import_id = await _assumed_zero_review(
        ledger,
        card,
        [
            {"date": "2026-04-01", "description": "Blinkit", "debit": 42800, "credit": 0},
            {"date": "2026-04-02", "description": "Merchant refund", "debit": 0, "credit": 10395},
        ],
        closing=-32405,
    )
    credit = await _line(ledger, import_id, index=1)
    await resolve_line(
        ledger,
        import_id=import_id,
        line_id=credit["id"],
        resolution="classify_credit",
        credit_kind="refund",
        category_id=str(food["id"]),
    )

    await retry_with_opening(ledger, import_id=import_id, opening_paise=0)

    detail = await import_payload(ledger, import_id)
    assert detail["check"] == "VERIFIED"
    assert detail["opening_source"] == "manual"
    stored = await ledger.fetchrow(
        "SELECT match_state, movement_id, match_detail FROM statement_lines WHERE id = $1",
        credit["id"],
    )
    assert stored["match_state"] == "new"
    assert stored["movement_id"] is None
    assert stored["match_detail"]["credit_kind"] == "refund"
    assert stored["match_detail"]["credit_classified_by"] == "user"
    await commit_import(ledger, import_id, categorize=False)


async def test_a_bank_printed_opening_without_running_balances_still_commits(ledger):
    from fin_agent.importers.checks import check_statement

    account = await _account(ledger)
    raw_account = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, commit_groups)
        VALUES ($1, 'hash-printed-bank', 'review', $2, '2026-04-01', '2026-04-30',
                24567890, 24522890, '[]'::jsonb)
        """,
        import_id,
        account["id"],
    )
    rows = [{"date": "2026-04-03", "debit": 45000, "credit": 0}]
    await insert_lines(
        ledger,
        import_id=import_id,
        account=raw_account,
        parsed_account={"rows": [{**rows[0], "description": "SWIGGY"}]},
    )
    check = check_statement(rows, 24567890, 24522890, period=("2026-04-01", "2026-04-30"))
    assert check["status"] == "VERIFIED"
    assert check["opening_source"] == "printed"
    await ledger.execute("UPDATE imports SET check_result = $1 WHERE id = $2", check, import_id)
    await reconcile(
        ledger,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )

    detail = await import_payload(ledger, import_id)
    assert detail["opening_evidenced"] is True
    assert detail["opening_required"] is False
    assert (await commit_import(ledger, import_id, categorize=False))["status"] == "committed"
