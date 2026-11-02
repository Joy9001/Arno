"""Read tools, write tools, Code Mode cards, and the approval gate."""

from __future__ import annotations

import psycopg
import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from fin_agent.agent.builder import build_agent
from fin_agent.agent.cards import cards_from_messages
from fin_agent.agent.deps import AgentDeps
from fin_agent.engine.money import amortize as amortize_fn
from fin_agent.tools import read as read_tools
from fin_agent.tools import write as write_tools


def _thread(conn, thread_id="cnv_test", message_id="msg_test"):
    conn.execute("INSERT INTO conversations (id, title) VALUES (%s, 'test')", (thread_id,))
    conn.execute(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES (%s, %s, 'user', '{}'::jsonb, 'test')
        """,
        (thread_id, message_id),
    )
    row = conn.execute("SELECT id FROM messages WHERE client_id = %s", (message_id,)).fetchone()
    conn.commit()
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


def run_agent(agent, prompt, deps):
    return agent.run_sync(prompt, deps=deps)


def test_financial_context_has_ages_sources_and_no_unknown_zero(demo_ledger):
    from fin_agent.ledger.accounts import create_account

    context = read_tools.get_financial_context(demo_ledger)
    assert context["today"]
    assert context["accounts"]
    assert all("as_of" in account for account in context["accounts"])
    assert context["source_ids"]["accounts"]
    assert context["source_ids"]["schedules"]
    assert any(schedule["name"] == "Rent" for schedule in context["schedules"])
    assert context["liquid_total"]["paise"] > 0
    assert isinstance(context["setup_gaps"], list)

    create_account(demo_ledger, kind="bank", name="Empty bank")
    context = read_tools.get_financial_context(demo_ledger)
    empty = next(account for account in context["accounts"] if account["name"] == "Empty bank")
    assert empty["balance"] is None
    assert context["liquid_total"] is None
    assert "Empty bank has no stated balance" in context["warnings"]


def test_unknown_balance_is_null_not_zero():
    from fin_agent.engine.money import money

    assert money(None) is None
    assert money(0) == {"paise": 0, "text": "₹0"}


def test_a_period_with_no_rows_and_no_statement_is_unknown(demo_ledger):
    result = read_tools.summarize_spending(demo_ledger, group_by="none", period="2000-01")
    assert result["total"] is None
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["gaps"]


def test_run_query_is_read_only_and_marks_every_text(db):
    result = read_tools.run_query("SELECT 'hello' AS greeting, 45000 AS amount_paise")
    assert result["columns"] == ["greeting", "amount_paise"]
    assert result["rows"] == [["<outside>hello</outside>", 45000]]
    assert result["outside_text"] is True
    with pytest.raises(psycopg.Error):
        read_tools.run_query("UPDATE accounts SET name = 'hacked'")
    with pytest.raises(psycopg.Error):
        read_tools.run_query("SELECT 1; SELECT 2")


def test_run_query_has_a_two_second_timeout(db):
    with pytest.raises(psycopg.errors.QueryCanceled):
        read_tools.run_query("SELECT pg_sleep(3)")


def test_run_code_chart_becomes_a_card(demo_ledger):
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
        result = run_agent(agent, "show me", deps)
    cards = cards_from_messages(result.all_messages())
    assert cards and cards[0].data["card"] == "chart"
    assert cards[0].data["series"][0]["name"] == "Food"


def test_record_movement_returns_a_undo_card(demo_ledger):
    thread_id, message_id = _thread(demo_ledger)
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
        result = run_agent(agent, "spent 450 on swiggy", deps)
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


def test_correct_movement_learns_a_user_rule(demo_ledger):
    movement = write_tools.record_movement(
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
    category = demo_ledger.execute("SELECT id FROM categories WHERE name = 'Groceries'").fetchone()
    result = write_tools.correct_movement(
        demo_ledger,
        movement_id=movement["movement_id"],
        changes={"category": "Groceries"},
        scope="future",
        message_id=None,
    )
    assert result["rows_changed"] >= 1
    rule = demo_ledger.execute(
        "SELECT * FROM category_rules WHERE merchant_pattern = 'swiggy'"
    ).fetchone()
    assert rule["source"] == "user"
    assert rule["category_id"] == category["id"]


def test_preference_remember_and_forget(demo_ledger):
    remembered = write_tools.preference(
        demo_ledger,
        action="remember",
        text="Mom's transfer is not discretionary",
        preference_id=None,
        message_id=None,
    )
    assert remembered["status"] == "remembered"
    assert remembered["card"]["card"] == "preference"
    forgotten = write_tools.preference(
        demo_ledger,
        action="forget",
        text=None,
        preference_id=remembered["id"],
        message_id=None,
    )
    assert forgotten["status"] == "forgotten"


def test_flagged_thread_asks_before_a_preference(demo_ledger):
    thread_id, message_id = _thread(demo_ledger, "cnv_flagged", "msg_flagged")
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
        result = run_agent(agent, "a note from a stranger", deps)
    assert isinstance(result.output, DeferredToolRequests)
    with demo_ledger.transaction():
        assert demo_ledger.execute("SELECT count(*) AS n FROM preferences").fetchone()["n"] == 0


def test_an_unexpected_tool_error_becomes_a_model_readable_result(monkeypatch, demo_ledger):
    thread_id, message_id = _thread(demo_ledger, "cnv_error", "msg_error")

    def blow_up(*args, **kwargs):
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
        result = run_agent(agent, "log 10", deps)
    assert result.output == "I couldn't log that."
    returns = [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_name", None) == "record_movement"
        and getattr(part, "content", None) is not None
    ]
    assert any("failed" in str(part.content).lower() for part in returns)


def test_nested_read_results_are_not_truncated_inside_a_script(demo_ledger):
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
        result = run_agent(agent, "check the nested list", AgentDeps(conversation_id="cnv_nested"))
    part = next(
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_name", None) == "run_code"
        and getattr(part, "content", None) is not None
    )
    assert "900" in str(part.content)
    assert "list" in str(part.content)


def test_update_setup_sets_the_floor_and_a_baseline(demo_ledger):
    floor = write_tools.update_setup_item(
        demo_ledger,
        entity="settings",
        action="update",
        data={"floor_rupees": 20_000},
        message_id=None,
    )
    baseline = write_tools.update_setup_item(
        demo_ledger,
        entity="baseline",
        action="add",
        data={"category": "Food & Dining", "amount_per_day_rupees": 300},
        message_id=None,
    )
    assert floor["status"] == "saved"
    assert "20,000" in floor["summary"]
    assert baseline["status"] == "saved"
    row = demo_ledger.execute(
        "SELECT amount_per_day_paise FROM baselines b JOIN categories c ON c.id = b.category_id "
        "WHERE c.name = 'Food & Dining'"
    ).fetchone()
    assert row["amount_per_day_paise"] == 30_000


def test_amortize_tool_uses_a_loan_from_the_ledger(demo_ledger):
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
        result = run_agent(agent, "what is my home loan EMI", AgentDeps(conversation_id="cnv_x"))
    returns = [
        part.content
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", "") == "tool-return"
    ]
    assert {"paise": 3_150_000, "text": "₹31,500"} in returns


def test_read_filters_reject_unknown_keys(demo_ledger):
    # A dropped `categories` filter once answered "food last month" with the whole month.
    with pytest.raises(ValueError, match="Unknown filter 'categories'"):
        read_tools.summarize_spending(
            demo_ledger,
            group_by="none",
            period="2026-10",
            filters={"categories": ["Food & Dining"]},
        )
    with pytest.raises(ValueError, match="The period is its own argument"):
        read_tools.summarize_spending(
            demo_ledger, group_by="none", period="2026-10", filters={"period": "2026-09"}
        )
    with pytest.raises(ValueError, match=r"YYYY-MM-DD\.\.YYYY-MM-DD"):
        read_tools.query_movements(demo_ledger, filters={"date_from": "2026-10-01"})


def test_a_script_sees_the_typed_filters_and_a_wrong_key_fails(demo_ledger):
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
        result = run_agent(agent, "food last month?", AgentDeps(conversation_id="cnv_x"))
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


def test_counts_leave_out_a_corrected_entry_and_its_reversal(demo_ledger):
    groceries = read_tools.summarize_spending(
        demo_ledger, group_by="category", period="2026-10", filters={"category": "Groceries"}
    )
    assert groceries["total"]["paise"] == 650_000  # the sum is unchanged
    assert groceries["groups"][0]["count"] == 3  # three trips, not five rows
    raw = demo_ledger.execute(
        "SELECT count(*) AS n FROM movements WHERE date BETWEEN '2026-10-01' AND '2026-10-31'"
    ).fetchone()["n"]
    october = read_tools.query_movements(demo_ledger, filters={"period": "2026-10"}, limit=200)
    assert october["total_count"] == len(october["rows"]) == raw - 2
    everything = read_tools.query_movements(
        demo_ledger, filters={"period": "2026-10", "include_reversed": True}, limit=200
    )
    assert everything["total_count"] == len(everything["rows"]) == raw

    seen: list[str] = []
    cursor = None
    while True:
        page = read_tools.query_movements(
            demo_ledger, filters={"period": "2026-10"}, limit=5, cursor=cursor
        )
        assert len(page["rows"]) <= 5
        seen += [row["id"] for row in page["rows"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == october["total_count"]


def test_amortize_uses_the_loan_s_real_emi_and_a_strict_prepay(demo_ledger):
    from fin_agent.ledger.accounts import loan_terms

    terms = loan_terms(demo_ledger, "Home loan")
    assert terms["emi_paise"] == 3_150_000
    now = amortize_fn(**terms, prepay={"amount_paise": 5_000_000})
    later = amortize_fn(**terms, prepay={"amount_paise": 5_000_000, "at_emi": 13})
    assert 0 < later["prepay"]["interest_saved"]["paise"] < now["prepay"]["interest_saved"]["paise"]
    with pytest.raises(ValueError, match="after_months"):
        amortize_fn(**terms, prepay={"amount_paise": 5_000_000, "after_months": 12})
