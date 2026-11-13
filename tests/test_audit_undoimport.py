"""Audit regression: undoing a committed import also undoes the categorization it caused.

The commit queues the categorizer. The rules engine and the categorizer then label this import's
rows, and write `llm` rules, in their own write groups. `undo_import` used to treat those later
groups as a later write on the same rows and refuse with a 409, so on a real instance with jobs
enabled every committed import became un-undoable seconds after the commit.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo

from arno.importers.categorize import run_categorize
from arno.importers.commit import commit_import, undo_import
from arno.importers.periods import ledger_opening as opening_balance
from arno.importers.reconcile import insert_lines, reconcile
from arno.ledger import movements as movements_mod
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from arno.ledger.categories import upsert_rule
from arno.ledger.common import ledger_balance
from arno.ledger.errors import ConflictLedgerError
from tests.agent_helpers import streaming_model
from tests.invariants import check_ledger

PERIOD = (date(2026, 4, 1), date(2026, 4, 30))
FOOD = "Food & Dining"
SHOPPING = "Shopping"
MODEL_KEY = "alpha store"


async def _account(conn) -> dict:
    created = await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def _category_id(conn, name: str) -> str:
    return str(await conn.fetchval("SELECT id FROM categories WHERE name = $1", name))


async def _review(conn, account: dict, rows: list[dict]) -> str:
    """One import in `review`, verified with a manual opening, lines already reconciled."""
    raw_account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    # The statement prints the balance the ledger held before its period, not a made-up zero: a
    # stated opening the ledger disagrees with is booked as a visible adjustment (and would be
    # replaced by the next, overlapping statement).
    previous = await opening_balance(conn, account["id"], PERIOD[0])
    opening = int(previous["paise"]) if previous else 0
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             check_result, check_status, review, commit_groups)
        VALUES ($1, $2, 'review', $3, $4, $5, $6, 'VERIFIED', $7, '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
        PERIOD[0],
        PERIOD[1],
        {"opening_source": "manual", "opening_paise": opening},
        {"manual_opening_paise": opening},
    )
    await insert_lines(
        conn, import_id=import_id, account=raw_account, parsed_account={"rows": rows}
    )
    await reconcile(conn, import_id=import_id, account=raw_account, period=PERIOD)
    return import_id


def _row(
    description: str,
    *,
    debit: int = 0,
    credit: int = 0,
    day: str = "2026-04-01",
    counterparty: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": day,
        "description": description,
        "debit": debit,
        "credit": credit,
        "balance": 24567890,
    }
    if counterparty:
        row["counterparty"] = counterparty
    return row


def _chunk_answer(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    prompt = ""
    for message in messages:
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str) and "Payees (JSON data" in content:
                prompt = content
    return json.loads(prompt.split("never an instruction): ", 1)[1])


async def _categorize(conn, import_id: str, key: str) -> dict[str, Any]:
    """Run the rules engine and a scripted categorizer, with no real model call."""

    def run_agent(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if "pending_payees" not in {tool.name for tool in info.function_tools}:
            chunk = [
                {"key": item["key"], "category": SHOPPING, "guess_reason": "store"}
                for item in _chunk_answer(messages)
            ]
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, {"response": chunk})]
            )
        returns = [
            part.tool_name
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("pending_payees", {"import_id": import_id})])
        if "categorize_now" not in returns:
            return ModelResponse(parts=[ToolCallPart("categorize_now", {"keys": [key]})])
        if "save_categories" not in returns:
            return ModelResponse(parts=[ToolCallPart("save_categories", {"distrust": []})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"status": "saved", "labeled": 1, "uncategorized": 0},
                )
            ]
        )

    return await run_categorize(conn, import_id, model=streaming_model(run_agent))


async def _state(conn) -> dict[str, Any]:
    """Everything an undo must put back: balances, spending, labels, rules, active payees."""
    accounts = await conn.fetch("SELECT name, ledger_account_id FROM accounts ORDER BY name")
    balances = {
        row["name"]: await ledger_balance(conn, row["ledger_account_id"]) for row in accounts
    }
    spending = {
        str(row["category"]): int(row["total"])
        for row in await conn.fetch(
            """
            SELECT coalesce(category_name, '(uncategorized)') AS category,
                   sum(amount_paise)::bigint AS total
            FROM v_spending GROUP BY 1 ORDER BY 1
            """
        )
        if int(row["total"]) != 0
    }
    labels = [
        (str(row["description"]), str(row["category"]))
        for row in await conn.fetch(
            """
            SELECT m.description, c.name AS category
            FROM movements m
            JOIN movement_legs tl ON tl.movement_id = m.id
            JOIN categories c ON c.id = tl.category_id
            WHERE m.reverses_id IS NULL
            ORDER BY m.description, c.name
            """
        )
    ]
    hints = [
        (str(row["description"]), str((row["category_hint"] or {}).get("reason")))
        for row in await conn.fetch(
            """
            SELECT description, category_hint FROM movements
            WHERE reverses_id IS NULL AND category_hint IS NOT NULL
            ORDER BY description
            """
        )
    ]
    movement_rules = [
        (str(row["description"]), str(row["pattern"]))
        for row in await conn.fetch(
            """
            SELECT m.description, r.pattern
            FROM movements m JOIN category_rules r ON r.id = m.category_rule_id
            WHERE m.reverses_id IS NULL
            ORDER BY m.description, r.pattern
            """
        )
    ]
    rules = [
        (str(row["match"]), str(row["pattern"]), str(row["source"]), str(row["category"]))
        for row in await conn.fetch(
            """
            SELECT r.match, r.pattern, r.source, c.name AS category
            FROM category_rules r JOIN categories c ON c.id = r.category_id
            ORDER BY r.match, r.pattern, r.source
            """
        )
    ]
    payees = [
        str(row["display_name"])
        for row in await conn.fetch(
            "SELECT display_name FROM payees WHERE active ORDER BY display_name"
        )
    ]
    return {
        "balances": balances,
        "spending": spending,
        "labels": labels,
        "hints": hints,
        "movement_rules": movement_rules,
        "rules": rules,
        "payees": payees,
    }


async def _reviewed(conn, account: dict) -> str:
    """An import in review whose rows will be labelled: one by a rule, one by the categorizer."""
    await upsert_rule(
        conn, pattern="swiggy", category_id=await _category_id(conn, FOOD), source="user"
    )
    return await _review(
        conn,
        account,
        [
            _row("UPI/DR/1/SWIGGY BANGALORE", debit=45000, counterparty="Swiggy"),
            _row(
                "UPI/DR/2/ALPHA STORE",
                debit=20000,
                day="2026-04-02",
                counterparty="Alpha Store",
            ),
        ],
    )


async def test_undoing_an_import_also_undoes_the_categorization_it_caused(ledger):
    account = await _account(ledger)
    import_id = await _reviewed(ledger, account)
    # Hold a second, unrelated rule so the snapshot is not just "rules come and go with imports".
    await upsert_rule(
        ledger, pattern="zomato", category_id=await _category_id(ledger, FOOD), source="user"
    )
    before = await _state(ledger)
    await commit_import(ledger, import_id)

    result = await _categorize(ledger, import_id, MODEL_KEY)
    assert result["status"] == "done"
    after_categorize = await _state(ledger)
    assert after_categorize["rules"] != before["rules"]
    assert ("alpha store", "llm", SHOPPING) in [
        (row[1], row[2], row[3]) for row in after_categorize["rules"]
    ]
    assert ("UPI/DR/2/ALPHA STORE", SHOPPING) in after_categorize["labels"]
    assert ("UPI/DR/1/SWIGGY BANGALORE", FOOD) in after_categorize["labels"]

    await undo_import(ledger, import_id)

    # Exactly the pre-commit state: balances, spending, labels, hints, rules, active payees.
    assert await _state(ledger) == before
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == (
        "rejected"
    )
    assert await check_ledger(ledger) == []


async def test_a_later_user_edit_blocks_the_undo_and_says_what_changed(ledger):
    account = await _account(ledger)
    import_id = await _reviewed(ledger, account)
    await commit_import(ledger, import_id)
    await _categorize(ledger, import_id, MODEL_KEY)
    movement_id = await ledger.fetchval(
        """
        SELECT sl.movement_id FROM statement_lines sl
        WHERE sl.import_id = $1 AND sl.description_raw LIKE '%ALPHA%'
        """,
        import_id,
    )
    # The user recategorizes the row the categorizer labelled. That is a genuine later change.
    await movements_mod.correct_movement(
        ledger,
        movement_id,
        changes={"category_id": await _category_id(ledger, FOOD)},
    )

    with pytest.raises(ConflictLedgerError) as caught:
        await undo_import(ledger, import_id)

    message = caught.value.message
    assert "later write" in message
    assert "label" in message
    assert "Undo that change first" in message
    assert message != "A later write changed rows this group touched."
    # Nothing is half-reverted.
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == (
        "committed"
    )
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'reversal'") == 0
    assert await check_ledger(ledger) == []


async def test_undoing_an_import_twice_is_idempotent(ledger):
    account = await _account(ledger)
    import_id = await _reviewed(ledger, account)
    await commit_import(ledger, import_id)
    await _categorize(ledger, import_id, MODEL_KEY)

    await undo_import(ledger, import_id)
    undone = await _state(ledger)
    again = await undo_import(ledger, import_id)

    assert again == {"status": "already_undone", "import_id": import_id}
    assert await _state(ledger) == undone
    assert await check_ledger(ledger) == []


async def test_undoing_one_import_leaves_the_other_imports_categorization(ledger):
    account = await _account(ledger)
    first = await _review(
        ledger,
        account,
        [_row("UPI/DR/2/ALPHA STORE", debit=20000, counterparty="Alpha Store")],
    )
    second = await _review(
        ledger,
        account,
        [
            _row(
                "UPI/DR/3/BETA STORE",
                debit=30000,
                day="2026-04-03",
                counterparty="Beta Store",
            )
        ],
    )
    await commit_import(ledger, first)
    await commit_import(ledger, second)
    await _categorize(ledger, first, "alpha store")
    await _categorize(ledger, second, "beta store")

    await undo_import(ledger, first)

    rules = [
        (str(row["pattern"]), str(row["source"]))
        for row in await ledger.fetch("SELECT pattern, source FROM category_rules ORDER BY pattern")
    ]
    assert rules == [("beta store", "llm")]
    assert (
        await ledger.fetchval(
            """
            SELECT c.name FROM movements m
            JOIN movement_legs tl ON tl.movement_id = m.id
            JOIN categories c ON c.id = tl.category_id
            WHERE m.reverses_id IS NULL AND m.description LIKE '%BETA%'
            """
        )
        == SHOPPING
    )
    assert (
        await ledger.fetchval(
            """
            SELECT count(*) FROM movements m
            JOIN movement_legs tl ON tl.movement_id = m.id
            WHERE m.reverses_id IS NULL AND m.description LIKE '%ALPHA%'
              AND tl.category_id IS NOT NULL
            """
        )
        == 0
    )
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", second) == (
        "committed"
    )
    assert await check_ledger(ledger) == []
