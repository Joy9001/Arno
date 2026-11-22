"""Read tools, write tools, Code Mode cards, and the approval gate."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import asyncpg
import pytest
from pydantic_ai import DeferredToolRequests, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from arno.agent.builder import build_agent
from arno.agent.cards import cards_from_messages
from arno.agent.deps import AgentDeps
from arno.agent.toolset import LoanConventions
from arno.agent.toolset import amortize as amortize_tool
from arno.agent.toolset import card_payoff as card_payoff_tool
from arno.agent.toolset import compare_offers as compare_offers_tool
from arno.agent.toolset import present as present_tool
from arno.engine.money import Prepay
from arno.engine.money import amortize as amortize_fn
from arno.tools import read as read_tools
from arno.tools import write as write_tools


async def test_missing_card_dates_are_refused_through_the_agent_setup_tool(ledger):
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from arno.agent.toolset import update_setup
    from arno.agent.write_models import AccountSetup

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


async def test_financial_context_has_ages_sources_and_no_unknown_zero(demo_ledger):
    from arno.ledger.accounts import create_account

    context = await read_tools.get_financial_context(demo_ledger)
    assert context["today"]
    assert context["accounts"]
    assert all("as_of" in account for account in context["accounts"])
    assert context["source_ids"]["accounts"]
    assert context["source_ids"]["schedules"]
    assert any(schedule["name"] == "Rent" for schedule in context["schedules"])
    assert context["liquid_total"]["paise"] > 0
    assert isinstance(context["setup_gaps"], list)

    await create_account(demo_ledger, kind="bank", name="Empty bank")
    context = await read_tools.get_financial_context(demo_ledger)
    empty = next(account for account in context["accounts"] if account["name"] == "Empty bank")
    assert empty["balance"] is None
    assert context["liquid_total"] is None
    assert "Empty bank has no stated balance" in context["warnings"]


async def test_a_movement_does_not_make_the_financial_context_balance_known(ledger):
    from arno.ledger.accounts import create_account, get_account, set_statement_balance
    from arno.ledger.common import today as ledger_today
    from arno.ledger.movements import record_expense

    account = await create_account(ledger, kind="bank", name="SBI Savings")
    await record_expense(ledger, account_id=account["id"], amount_paise=25_000, description="Lunch")

    context = await read_tools.get_financial_context(ledger)
    row = next(item for item in context["accounts"] if item["id"] == account["id"])
    assert row["known"] is False
    assert row["balance"] is None
    assert row["as_of"] is None
    assert context["liquid_total"] is None
    assert "SBI Savings has no stated balance" in context["warnings"]
    assert "an account has no stated balance" in context["setup_gaps"]
    # The ledger net change is still auditable even though it is not a known bank balance.
    assert (await get_account(ledger, account["id"]))["balance_paise"] == -25_000

    await set_statement_balance(ledger, account["id"], balance_paise=10_000, as_of=ledger_today())
    context = await read_tools.get_financial_context(ledger)
    row = next(item for item in context["accounts"] if item["id"] == account["id"])
    assert row["known"] is True
    assert row["balance"] == {"paise": 10_000, "text": "₹100"}
    assert context["liquid_total"] == {"paise": 10_000, "text": "₹100"}


async def test_financial_context_card_credit_is_unknown_until_a_balance_is_stated(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.common import today as ledger_today
    from arno.ledger.movements import record_expense

    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    await record_expense(
        ledger, account_id=card["id"], amount_paise=3_000_000, description="Headphones"
    )

    context = await read_tools.get_financial_context(ledger)
    row = next(item for item in context["accounts"] if item["id"] == card["id"])
    assert row["known"] is False
    assert row["balance"] is None
    assert row["outstanding"] is None
    assert row["available_credit"] is None
    assert row["credit_limit"] == {"paise": 10_000_000, "text": "₹1,00,000"}
    assert row["statement_day"] == 5
    assert row["due_day"] == 25

    from arno.ledger.accounts import set_statement_balance

    await set_statement_balance(ledger, card["id"], balance_paise=-3_000_000, as_of=ledger_today())
    context = await read_tools.get_financial_context(ledger)
    row = next(item for item in context["accounts"] if item["id"] == card["id"])
    assert row["known"] is True
    assert row["outstanding"] == {"paise": 3_000_000, "text": "₹30,000"}
    assert row["available_credit"] == {"paise": 7_000_000, "text": "₹70,000"}


async def test_financial_context_known_zero_card_keeps_zero_outstanding(ledger):
    from arno.ledger.accounts import create_account

    card = await create_account(
        ledger,
        kind="card",
        name="Zero card",
        opening_balance_paise=0,
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )

    context = await read_tools.get_financial_context(ledger)
    row = next(item for item in context["accounts"] if item["id"] == card["id"])
    assert row["known"] is True
    assert row["outstanding"] == {"paise": 0, "text": "₹0"}
    assert row["available_credit"] == {"paise": 10_000_000, "text": "₹1,00,000"}


async def test_financial_context_reports_a_card_s_finance_terms(ledger):
    from arno.ledger.accounts import create_account, update_account

    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )

    context = await read_tools.get_financial_context(ledger)
    row = next(item for item in context["accounts"] if item["id"] == card["id"])
    assert row["finance_rate_bps"] is None
    assert row["min_due_bps"] is None
    assert row["min_due_floor"] is None

    await update_account(
        ledger,
        card["id"],
        finance_rate_bps=4_200,
        min_due_bps=500,
        min_due_floor_paise=20_000,
    )
    context = await read_tools.get_financial_context(ledger)
    row = next(item for item in context["accounts"] if item["id"] == card["id"])
    assert row["finance_rate_bps"] == 4_200
    assert row["min_due_bps"] == 500
    assert row["min_due_floor"] == {"paise": 20_000, "text": "₹200"}


def test_unknown_balance_is_null_not_zero():
    from arno.engine.money import money

    assert money(None) is None
    assert money(0) == {"paise": 0, "text": "₹0"}


async def test_a_period_with_no_rows_and_no_statement_is_unknown(demo_ledger):
    result = await read_tools.summarize_spending(demo_ledger, group_by="none", period="2000-01")
    assert result["total"] is None
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["gaps"]


async def test_run_query_is_read_only_and_marks_every_text():
    result = await read_tools.run_query("SELECT 'hello' AS greeting, 45000 AS amount_paise")
    assert result["columns"] == ["greeting", "amount_paise"]
    assert result["rows"] == [["<outside>hello</outside>", 45000]]
    assert result["outside_text"] is True
    with pytest.raises((ValueError, asyncpg.PostgresError)):
        await read_tools.run_query("UPDATE accounts SET name = 'hacked'")
    with pytest.raises((ValueError, asyncpg.PostgresError)):
        await read_tools.run_query("SELECT 1; SELECT 2")


async def test_run_query_has_a_two_second_timeout():
    with pytest.raises((asyncpg.QueryCanceledError, TimeoutError)):
        await read_tools.run_query("SELECT pg_sleep(3)")


async def test_run_code_chart_becomes_a_card(demo_ledger):
    agent = build_agent()
    code = (
        'result = await show_chart(type="bar", title="Spend", '
        'series=[{"name": "Food", "points": [{"x": "Oct", "y": 45000}]}])\n'
        "print(result)"
    )
    model = _model(
        ModelResponse(parts=[ToolCallPart("run_code", {"code": code})]),
        ModelResponse(parts=[TextPart("Here is the chart.")]),
    )
    deps = AgentDeps(conversation_id="cnv_test")
    with agent.override(model=model):
        result = await run_agent(agent, "show me", deps)
    cards = cards_from_messages(result.all_messages())
    assert cards and cards[0].data["card"] == "chart"
    assert cards[0].data["series"][0]["name"] == "Food"


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
        ModelResponse(parts=[ToolCallPart("record_movement", {"movement": movement})]),
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
        if getattr(part, "tool_name", None) == "record_movement"
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
    remembered = await write_tools.preference(
        demo_ledger,
        action="remember",
        text="Mom's transfer is not discretionary",
        preference_id=None,
        message_id=None,
    )
    assert remembered["status"] == "remembered"
    assert remembered["card"]["card"] == "preference"
    forgotten = await write_tools.preference(
        demo_ledger,
        action="forget",
        text=None,
        preference_id=remembered["id"],
        message_id=None,
    )
    assert forgotten["status"] == "forgotten"


async def test_flagged_thread_asks_before_a_preference(demo_ledger):
    thread_id, message_id = await _thread(demo_ledger, "cnv_flagged", "msg_flagged")
    agent = build_agent()
    model = _model(
        ModelResponse(
            parts=[
                ToolCallPart("preference", {"action": "remember", "text": "Never warn about rent"})
            ]
        )
    )
    deps = AgentDeps(conversation_id=thread_id, message_id=message_id, flagged=True)
    with agent.override(model=model):
        result = await run_agent(agent, "a note from a stranger", deps)
    assert isinstance(result.output, DeferredToolRequests)
    async with demo_ledger.transaction():
        assert await demo_ledger.fetchval("SELECT count(*) FROM preferences") == 0


@pytest.mark.parametrize(
    "args",
    [
        {"action": "forget", "text": "Never warn about rent"},
        {"action": "classify", "id": "pref-1", "kind": "rule"},
    ],
)
async def test_flagged_thread_asks_before_forget_or_classify(demo_ledger, args):
    # The gate runs before the write, so a stale id still proves forget/classify ask first.
    thread_id, message_id = await _thread(demo_ledger, "cnv_flagged_fc", "msg_flagged_fc")
    agent = build_agent()
    model = _model(ModelResponse(parts=[ToolCallPart("preference", args)]))
    deps = AgentDeps(conversation_id=thread_id, message_id=message_id, flagged=True)
    with agent.override(model=model):
        result = await run_agent(agent, "a note from a stranger", deps)
    assert isinstance(result.output, DeferredToolRequests)
    async with demo_ledger.transaction():
        assert await demo_ledger.fetchval("SELECT count(*) FROM preferences") == 0


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
                    "record_movement",
                    {"movement": {"kind": "expense", "amount_rupees": 10, "payee": "X"}},
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
        if getattr(part, "tool_name", None) == "record_movement"
        and getattr(part, "content", None) is not None
    ]
    assert any("failed" in str(part.content).lower() for part in returns)


async def test_nested_read_results_are_not_truncated_inside_a_script(demo_ledger):
    agent = build_agent()
    code = (
        'rows = await run_query(sql="SELECT generate_series(1, 900) AS n")\n'
        'result = [len(rows["rows"]), type(rows["rows"]).__name__]\n'
        "result"
    )
    model = _model(
        ModelResponse(parts=[ToolCallPart("run_code", {"code": code})]),
        ModelResponse(parts=[TextPart("done")]),
    )
    with agent.override(model=model):
        result = await run_agent(
            agent, "check the nested list", AgentDeps(conversation_id="cnv_nested")
        )
    part = next(
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_name", None) == "run_code"
        and getattr(part, "content", None) is not None
    )
    assert "900" in str(part.content)
    assert "list" in str(part.content)


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


async def test_amortize_tool_uses_a_loan_from_the_ledger(demo_ledger):
    # The tool's own return is asserted: this test once passed while every named loan failed.
    agent = build_agent()
    model = _model(
        ModelResponse(
            parts=[
                ToolCallPart(
                    "run_code",
                    {"code": "result = await amortize(loan='Home loan')\nresult['emi']"},
                )
            ]
        ),
        ModelResponse(parts=[TextPart("The EMI is in the tool result.")]),
    )
    with agent.override(model=model):
        result = await run_agent(
            agent, "what is my home loan EMI", AgentDeps(conversation_id="cnv_x")
        )
    returns = [
        part.content
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", "") == "tool-return"
    ]
    assert {"paise": 3_150_000, "text": "₹31,500"} in returns


async def test_read_filters_reject_unknown_keys(demo_ledger):
    # A dropped `categories` filter once answered "food last month" with the whole month.
    with pytest.raises(ValueError, match="Unknown filter 'categories'"):
        await read_tools.summarize_spending(
            demo_ledger,
            group_by="none",
            period="2026-10",
            filters={"categories": ["Food & Dining"]},
        )
    with pytest.raises(ValueError, match="The period is its own argument"):
        await read_tools.summarize_spending(
            demo_ledger, group_by="none", period="2026-10", filters={"period": "2026-09"}
        )
    with pytest.raises(ValueError, match=r"YYYY-MM-DD\.\.YYYY-MM-DD"):
        await read_tools.query_movements(demo_ledger, filters={"date_from": "2026-10-01"})


async def test_a_script_sees_the_typed_filters_and_a_wrong_key_fails(demo_ledger):
    seen: dict[str, str] = {}
    code = (
        "r = await summarize_spending(group_by='none', period='2026-10', "
        "filters={'categories': ['Food & Dining']})\nr"
    )

    def script(messages, info: AgentInfo) -> ModelResponse:
        seen.setdefault("catalog", info.instructions or "")
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
        return ModelResponse(parts=[TextPart("Done.")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = await run_agent(agent, "food last month?", AgentDeps(conversation_id="cnv_x"))
    for field in ("class MovementFilters", "include_reversed", "class Prepay", "at_emi"):
        assert field in seen["catalog"]
    feedback = " ".join(
        str(getattr(part, "content", ""))
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", "") in {"tool-return", "retry-prompt"}
    )
    assert "categories" in feedback
    assert "60,999" not in feedback


async def test_counts_leave_out_a_corrected_entry_and_its_reversal(demo_ledger):
    groceries = await read_tools.summarize_spending(
        demo_ledger, group_by="category", period="2026-10", filters={"category": "Groceries"}
    )
    assert groceries["total"]["paise"] == 650_000  # the sum is unchanged
    assert groceries["groups"][0]["count"] == 3  # three trips, not five rows
    raw = await demo_ledger.fetchval(
        "SELECT count(*) FROM movements WHERE date BETWEEN '2026-10-01' AND '2026-10-31'"
    )
    october = await read_tools.query_movements(
        demo_ledger, filters={"period": "2026-10"}, limit=200
    )
    assert october["total_count"] == len(october["rows"]) == raw - 2
    everything = await read_tools.query_movements(
        demo_ledger, filters={"period": "2026-10", "include_reversed": True}, limit=200
    )
    assert everything["total_count"] == len(everything["rows"]) == raw

    seen: list[str] = []
    cursor = None
    while True:
        page = await read_tools.query_movements(
            demo_ledger, filters={"period": "2026-10"}, limit=5, cursor=cursor
        )
        assert len(page["rows"]) <= 5
        seen += [row["id"] for row in page["rows"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == october["total_count"]


async def test_amortize_uses_the_loan_s_real_emi_and_a_strict_prepay(demo_ledger):
    from arno.ledger.accounts import loan_terms

    terms = await loan_terms(demo_ledger, "Home loan")
    assert terms["emi_paise"] == 3_150_000
    now = amortize_fn(**terms, prepay={"amount_paise": 5_000_000})
    later = amortize_fn(**terms, prepay={"amount_paise": 5_000_000, "at_emi": 13})
    now_prepay = now["prepay"]
    later_prepay = later["prepay"]
    assert now_prepay is not None
    assert later_prepay is not None
    assert 0 < later_prepay["interest_saved"]["paise"] < now_prepay["interest_saved"]["paise"]
    # An unknown key must still be refused at runtime; the type cannot express a deliberate miss.
    rejected = cast(Prepay, {"amount_paise": 5_000_000, "after_months": 12})
    with pytest.raises(ValueError, match="after_months"):
        amortize_fn(**terms, prepay=rejected)


def _amortize_ctx() -> RunContext[AgentDeps]:
    """The amortize tool reads only `ctx.deps`, whose result registry lives in memory."""
    return cast(RunContext[AgentDeps], SimpleNamespace(deps=AgentDeps(conversation_id="cnv_x")))


async def _amortize(**kwargs):
    return (await amortize_tool(_amortize_ctx(), **kwargs)).return_value


async def test_amortize_infers_the_rate_from_the_emi():
    result = await _amortize(principal_paise=100_000_000, months=240, emi_paise=867_823)
    assert result["emi"] == {"paise": 867_823, "text": "₹8,678.23"}
    assert "8.5000% to 8.5000% a year, inferred from the EMI" in result["assumptions"]


async def test_amortize_accepts_an_emi_that_matches_a_rate_range():
    result = await _amortize(principal_paise=10_000_000, months=240, emi_paise=86_782)
    assert result["emi"]["paise"] == 86_782
    assert "8.4999% to 8.5000% a year, inferred from the EMI" in result["assumptions"]


async def test_amortize_requests_a_rate_for_daily_rest():
    with pytest.raises(ModelRetry, match="monthly rest only"):
        await _amortize(
            principal_paise=10_000_000,
            months=240,
            emi_paise=86_782,
            conventions=LoanConventions(rest="daily"),
        )


async def test_amortize_conventions_round_the_emi_like_the_bank():
    plain = await _amortize(principal_paise=100_000_000, annual_rate=8.5, months=240)
    rounded = await _amortize(
        principal_paise=100_000_000,
        annual_rate=8.5,
        months=240,
        conventions=LoanConventions(emi_rounding="up", emi_unit_paise=100),
    )
    assert plain["emi"]["paise"] == 867_823
    assert rounded["emi"]["paise"] == 867_900
    assert rounded["emi"]["paise"] % 100 == 0
    assert "EMI rounded up to 100 paise" in rounded["assumptions"]


async def test_amortize_refuses_an_emi_that_no_rate_fits():
    # 867,824 paise sits between two rate units at the default conventions.
    with pytest.raises(ModelRetry, match="no exact unit match"):
        await _amortize(principal_paise=100_000_000, months=240, emi_paise=867_824)
    # Whole-rupee rounding never yields 8,678.23, so these conventions reach the inference.
    with pytest.raises(ModelRetry, match="no exact unit match"):
        await _amortize(
            principal_paise=100_000_000,
            months=240,
            emi_paise=867_823,
            conventions=LoanConventions(emi_rounding="up", emi_unit_paise=100),
        )


async def test_amortize_with_a_rate_is_unchanged():
    expected = amortize_fn(100_000_000, 8.5, 240)
    result = await _amortize(principal_paise=100_000_000, annual_rate=8.5, months=240)
    assert set(result.keys()) - {"ui_results"} == set(expected.keys())
    assert result["emi"] == expected["emi"]
    assert result["months"] == expected["months"]
    assert result["total_interest"] == expected["total_interest"]


async def test_amortize_with_fee_returns_apr_percent_and_works_on_named_loan(demo_ledger):
    with_fee = await _amortize(
        principal_paise=10_000_000, annual_rate=10, months=12, fee_paise=200_000
    )
    assert with_fee["processing_fee"] == {"paise": 200_000, "text": "₹2,000"}
    assert with_fee["fee_gst"] == {"paise": 36_000, "text": "₹360"}
    assert with_fee["net_disbursed"] == {"paise": 9_764_000, "text": "₹97,640"}
    assert isinstance(with_fee["apr_percent"], float)
    assert 14.0 < with_fee["apr_percent"] < 15.5

    agent = build_agent()
    code = (
        "res = await amortize(loan='Home loan', fee_paise=500000)\n"
        "(res['net_disbursed'], res['apr_percent'])"
    )
    model = _model(
        ModelResponse(parts=[ToolCallPart("run_code", {"code": code})]),
        ModelResponse(parts=[TextPart("Computed.")]),
    )
    with agent.override(model=model):
        run_res = await run_agent(
            agent, "What is the APR on my Home loan with a ₹5,000 fee?", AgentDeps("cnv_fee")
        )
    returns = [
        part.content
        for message in run_res.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", "") == "tool-return"
    ]

    def numbers(value):
        if isinstance(value, bool):
            return []
        if isinstance(value, (int, float)):
            return [float(value)]
        if isinstance(value, dict):
            return [number for item in value.values() for number in numbers(item)]
        if isinstance(value, (list, tuple)):
            return [number for item in value for number in numbers(item)]
        return []

    # A bare `assert returns` also passes on a "Runtime error:" string, hiding a script that
    # never produced the APR. The named loan has its own ledger rate, so require a real APR
    # figure from the script, not the rate of the direct call above.
    assert not any("Runtime error" in str(value) for value in returns), returns
    assert any(0.0 < number < 100.0 for value in returns for number in numbers(value)), returns


async def test_compare_offers_adapter_separates_components_and_refuses_a_missing_rate():
    ctx = _amortize_ctx()
    result = (
        await compare_offers_tool(
            ctx,
            offers=[
                {"label": "Cash", "cash_price_paise": 10_000, "discount_paise": 1_000},
                {
                    "label": "EMI",
                    "cash_price_paise": 10_000,
                    "financing": {
                        "kind": "card_emi",
                        "annual_rate_bps": 0,
                        "months": 2,
                        "processing_fee_paise": 500,
                    },
                },
            ],
        )
    ).return_value
    assert isinstance(result, dict)
    cash, emi = result["offers"]
    assert cash["difference_vs_cash_paise"] == -1_000
    assert cash["complete_cost"] is True
    assert emi["gst_on_fee_paise"] == 90
    assert emi["apr_percent"] > 0
    assert emi["complete_cost"] is False
    with pytest.raises(ModelRetry, match="requires an annual rate"):
        await compare_offers_tool(
            ctx,
            offers=[
                {"label": "Cash", "cash_price_paise": 10_000},
                {
                    "label": "Unknown rate",
                    "cash_price_paise": 10_000,
                    "financing": {"kind": "loan", "months": 2},
                },
            ],
        )


async def test_card_payoff_adapter_errors_present_and_code_mode(demo_ledger):
    ctx = _amortize_ctx()
    with pytest.raises(ModelRetry, match="Ask the user for the card's finance charge"):
        await card_payoff_tool(ctx, card="Slice card", balance_paise=4_000_000)

    with pytest.raises(ModelRetry, match="never clears the balance"):
        await card_payoff_tool(
            ctx,
            balance_paise=4_000_000,
            monthly_rate_percent=3.5,
            payment_paise=10_000,
        )

    res = (
        await card_payoff_tool(
            ctx,
            balance_paise=4_000_000,
            monthly_rate_percent=3.5,
        )
    ).return_value
    assert isinstance(res, dict)
    assert res["cost_of_carrying"]["paise"] > 0
    assert res["months"] > 0
    result_id = res["ui_results"][0]["id"]
    presented_val = (
        await present_tool(
            ctx,
            {
                "_type": "Stack",
                "children": [
                    {"_type": "Summary", "result_id": result_id},
                    {"_type": "Table", "result_id": result_id},
                ],
            },
        )
    ).return_value
    assert isinstance(presented_val, dict)
    assert presented_val["shown"] is True

    with pytest.raises(ModelRetry, match="Unknown or stale result id"):
        await present_tool(ctx, {"_type": "Summary", "result_id": "res_does_not_exist"})
    with pytest.raises(ModelRetry, match="Chart"):
        await present_tool(ctx, {"_type": "Chart", "result_id": result_id})

    pos_res = (await card_payoff_tool(ctx, None, 4_000_000, 3.5)).return_value
    assert isinstance(pos_res, dict)
    assert pos_res["months"] == res["months"]

    # An error echoing an outside_text person account marks the name and flags the conversation.
    from arno.ledger.payees import create_payee

    thread_id, message_id = await _thread(demo_ledger, "cnv_card_outside", "msg_card_outside")
    await create_payee(
        demo_ledger,
        display_name="Outside person payee",
        kind="person",
        outside_text=True,
        actor="system",
    )
    flagged_ctx = cast(
        RunContext[AgentDeps],
        SimpleNamespace(deps=AgentDeps(conversation_id=thread_id, message_id=message_id)),
    )
    with pytest.raises(ModelRetry, match="<outside>Outside person payee</outside>"):
        await card_payoff_tool(flagged_ctx, card="Outside person payee", monthly_rate_percent=3.5)
    assert await demo_ledger.fetchval(
        "SELECT outside_text_at IS NOT NULL FROM conversations WHERE id = $1", thread_id
    )

    agent = build_agent()
    code = (
        "kw = await card_payoff(balance_paise=4000000, monthly_rate_percent=3.5)\n"
        "kw2 = await card_payoff(balance_paise=5000000, monthly_rate_percent=3.0)\n"
        "loan_kw = await amortize(principal_paise=10000000, annual_rate=10, months=12, "
        "fee_paise=200000)\n"
        "(kw['cost_of_carrying'], kw['months'], kw2['months'], loan_kw['apr_percent'])"
    )
    model = _model(
        ModelResponse(parts=[ToolCallPart("run_code", {"code": code})]),
        ModelResponse(parts=[TextPart("Done.")]),
    )
    with agent.override(model=model):
        run_res = await run_agent(agent, "Compute card and loan costs.", AgentDeps("cnv_cm"))
    tool_returns = [
        part.content
        for message in run_res.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", "") == "tool-return"
    ]
    assert len(tool_returns) == 1
    assert res["cost_of_carrying"]["text"] in str(tool_returns[0])
    assert str(res["months"]) in str(tool_returns[0])
    # Calling card_payoff many times in a script must not emit native cards without `present`.
    assert not cards_from_messages(run_res.all_messages())
