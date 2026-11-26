"""Read tools, write tools, Code Mode cards, and the approval gate."""

from __future__ import annotations

from decimal import Decimal

import asyncpg
import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from arno.agent.builder import build_agent
from arno.agent.deps import AgentDeps
from arno.tools import read as read_tools
from arno.tools import recaps as recap_tools
from arno.tools import write as write_tools


async def test_missing_card_dates_are_refused_through_the_agent_setup_tool(ledger):
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from arno.agent.toolset import update_setup
    from arno.tools.write import AccountSetup

    thread_id, message_id = await _thread(ledger)
    ctx = RunContext(
        deps=AgentDeps(conversation_id=thread_id, message_id=message_id),
        model=TestModel(),
        usage=RunUsage(),
    )
    with pytest.raises(ModelRetry) as caught:
        await update_setup(
            ctx,
            entity="account",
            action="add",
            data=AccountSetup(name="Incomplete card", kind="card", credit_limit_rupees=1000),
        )
    assert "statement day" in str(caught.value)
    assert "due day" in str(caught.value)
    assert (
        await ledger.fetchval("SELECT count(*) FROM accounts WHERE name = 'Incomplete card'") == 0
    )


async def _thread(conn, thread_id="cnv_test", message_id="msg_test"):
    await conn.execute("INSERT INTO conversations (id, title) VALUES ($1, 'test')", thread_id)
    await conn.execute(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES ($1, $2, 'user', '{}'::jsonb, 'test')
        """,
        thread_id,
        message_id,
    )
    row = await conn.fetchrow("SELECT id FROM messages WHERE client_id = $1", message_id)
    return thread_id, str(row["id"])


def _model(*responses):
    calls = {"n": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        index = calls["n"]
        calls["n"] += 1
        if index < len(responses):
            return responses[index]
        return ModelResponse(parts=[TextPart("Done.")])

    return FunctionModel(script)


async def run_agent(agent, prompt, deps):
    return await agent.run(prompt, deps=deps)


def test_unknown_balance_is_null_not_zero():
    from arno.engine.money import money

    assert money(None) is None
    assert money(0) == {"paise": 0, "text": "₹0"}


async def test_a_period_with_no_rows_and_no_statement_is_unknown(demo_ledger):
    result = await recap_tools.summarize_spending(demo_ledger, group_by="none", period="2000-01")
    assert result["total"] is None
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["gaps"]


async def test_run_query_is_read_only():
    result = await read_tools.run_query("SELECT 'hello' AS greeting, 45000 AS amount_paise")
    assert result["columns"] == ["greeting", "amount_paise"]
    assert result["rows"] == [["hello", 45000]]
    with pytest.raises((ValueError, asyncpg.PostgresError)):
        await read_tools.run_query("UPDATE accounts SET name = 'hacked'")
    with pytest.raises((ValueError, asyncpg.PostgresError)):
        await read_tools.run_query("SELECT 1; SELECT 2")


async def test_run_query_has_a_two_second_timeout():
    with pytest.raises((asyncpg.QueryCanceledError, TimeoutError)):
        await read_tools.run_query("SELECT pg_sleep(3)")


async def test_record_movement_returns_a_undo_card(demo_ledger):
    thread_id, message_id = await _thread(demo_ledger)
    agent = build_agent()
    movement = {
        "kind": "expense",
        "amount_rupees": 450,
        "payee": "Swiggy",
        "account": "SBI Salary",
        "category": "Food & Dining",
    }
    model = _model(
        ModelResponse(parts=[ToolCallPart("record_movements", {"movements": [movement]})]),
        ModelResponse(parts=[TextPart("Logged ₹450.")]),
    )
    deps = AgentDeps(conversation_id=thread_id, message_id=message_id)
    with agent.override(model=model):
        result = await run_agent(agent, "spent 450 on swiggy", deps)
    assert result.output == "Logged ₹450."
    part = next(
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_name", None) == "record_movements"
        and getattr(part, "content", None) is not None
    )
    assert "recorded" in str(part.content)
    card = next(
        item
        for item in (part.metadata or [])
        if getattr(item, "data", {}).get("card") == "movement"
    )
    assert card.data["title"] == "Logged"


async def test_correct_movement_learns_a_user_rule(demo_ledger):
    movement = await write_tools.record_movement(
        demo_ledger,
        {
            "kind": "expense",
            "amount_rupees": 300,
            "payee": "Swiggy",
            "account": "SBI Salary",
            "category": "Food & Dining",
        },
        message_id=None,
        conversation_id=None,
    )
    category = await demo_ledger.fetchrow("SELECT id FROM categories WHERE name = 'Groceries'")
    result = await write_tools.correct_movement(
        demo_ledger,
        movement_id=movement["movement_id"],
        changes={"category": "Groceries"},
        scope="future",
        message_id=None,
    )
    assert result["rows_changed"] >= 1
    rule = await demo_ledger.fetchrow("SELECT * FROM category_rules WHERE pattern = 'swiggy'")
    assert rule["source"] == "user"
    assert rule["category_id"] == category["id"]


async def test_correct_movement_marks_a_payment_one_off(demo_ledger):
    movement = await write_tools.record_movement(
        demo_ledger,
        {
            "kind": "expense",
            "amount_rupees": 40_000,
            "payee": "Swiggy",
            "account": "SBI Salary",
            "category": "Food & Dining",
        },
        message_id=None,
        conversation_id=None,
    )
    result = await write_tools.correct_movement(
        demo_ledger,
        movement_id=movement["movement_id"],
        changes={"one_off": True},
        scope="this",
        message_id=None,
    )
    assert result["movement"]["one_off"] is True
    assert result["card"]["card"] == "correction"
    row = await demo_ledger.fetchrow(
        "SELECT one_off FROM movements WHERE id = $1", movement["movement_id"]
    )
    assert row["one_off"] is True


async def test_preference_remember_and_forget(demo_ledger):
    remembered = await write_tools.change_preference(
        demo_ledger,
        action="add",
        text="Mom's transfer is not discretionary",
        preference_id=None,
        message_id=None,
    )
    assert remembered["status"] == "remembered"
    assert remembered["card"]["card"] == "preference"
    forgotten = await write_tools.change_preference(
        demo_ledger,
        action="archive",
        text=None,
        preference_id=remembered["id"],
        message_id=None,
    )
    assert forgotten["status"] == "forgotten"


async def test_an_unexpected_tool_error_becomes_a_model_readable_result(monkeypatch, demo_ledger):
    thread_id, message_id = await _thread(demo_ledger, "cnv_error", "msg_error")

    async def blow_up(*args, **kwargs):
        raise RuntimeError("db down at 10.0.0.5:5432")

    monkeypatch.setattr(write_tools, "record_movement", blow_up)
    agent = build_agent()
    model = _model(
        ModelResponse(
            parts=[
                ToolCallPart(
                    "record_movements",
                    {"movements": [{"kind": "expense", "amount_rupees": 10, "payee": "X"}]},
                )
            ]
        ),
        ModelResponse(parts=[TextPart("I couldn't log that.")]),
    )
    deps = AgentDeps(conversation_id=thread_id, message_id=message_id)
    with agent.override(model=model):
        result = await run_agent(agent, "log 10", deps)
    assert result.output == "I couldn't log that."
    returns = [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_name", None) == "record_movements"
        and getattr(part, "content", None) is not None
    ]
    assert any("failed" in str(part.content).lower() for part in returns)


async def test_update_setup_sets_the_floor_and_a_baseline(demo_ledger):
    floor = await write_tools.update_setup_item(
        demo_ledger,
        entity="settings",
        action="update",
        data={"floor_rupees": 20_000},
        message_id=None,
    )
    baseline = await write_tools.update_setup_item(
        demo_ledger,
        entity="baseline",
        action="add",
        data={"category": "Food & Dining", "amount_per_day_rupees": 300},
        message_id=None,
    )
    assert floor["status"] == "saved"
    assert "20,000" in floor["summary"]
    assert baseline["status"] == "saved"
    row = await demo_ledger.fetchrow(
        "SELECT amount_per_day_paise FROM baselines b JOIN categories c ON c.id = b.category_id "
        "WHERE c.name = 'Food & Dining'"
    )
    assert row["amount_per_day_paise"] == 30_000


@pytest.mark.parametrize(
    ("monthly_percent", "annual_bps"), [("3.5", 4_200), ("3.75", 4_500), ("2.99", 3_588)]
)
async def test_update_setup_converts_a_monthly_finance_charge_to_annual_basis_points(
    ledger, monthly_percent, annual_bps
):
    saved = await write_tools.update_setup_item(
        ledger,
        entity="account",
        action="add",
        data={
            "name": "Slice card",
            "kind": "card",
            "credit_limit_rupees": 100_000,
            "statement_day": 5,
            "due_day": 25,
            "finance_rate_percent": Decimal(monthly_percent),
            "min_due_percent": Decimal("5"),
            "min_due_floor_paise": 20_000,
        },
        message_id=None,
    )
    card = saved["result"]
    assert card["finance_rate_bps"] == annual_bps
    assert card["min_due_bps"] == 500
    assert card["min_due_floor_paise"] == 20_000

    updated = await write_tools.update_setup_item(
        ledger,
        entity="account",
        action="update",
        data={"name": "Slice card", "finance_rate_percent": Decimal(monthly_percent)},
        message_id=None,
    )
    assert updated["result"]["finance_rate_bps"] == annual_bps
