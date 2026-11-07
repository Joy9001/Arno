"""The pinned snapshot, the two records, cards surviving a reload, and output limits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai_harness.compaction import is_pinned

from fin_agent.agent.builder import build_agent
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.history import (
    create_thread,
    ensure_history,
    refresh_snapshot,
    save_records,
)
from tests.agent_helpers import streaming_model


def _count_pins(history) -> int:
    return sum(
        1 for message in history for part in getattr(message, "parts", []) if is_pinned(part)
    )


async def test_a_new_thread_pins_the_snapshot_as_its_own_exchange(demo_ledger):
    thread = await create_thread(demo_ledger, title="new")
    history, outside = await ensure_history(demo_ledger, thread)
    assert len(history) == 2
    assert _count_pins(history) == 1
    assert history[1].parts[0].content == "Snapshot read."
    saved = await demo_ledger.fetchrow(
        "SELECT model_history, snapshot_at FROM conversations WHERE id = $1", thread["id"]
    )
    assert saved["snapshot_at"] is not None
    assert (
        _count_pins(
            __import__("pydantic_ai").messages.ModelMessagesTypeAdapter.validate_python(
                saved["model_history"]
            )
        )
        == 1
    )
    assert outside in (True, False)


async def test_a_stale_snapshot_is_swapped_without_piling_up_pins(demo_ledger):
    thread = await create_thread(demo_ledger, title="new")
    history, _ = await ensure_history(demo_ledger, thread)
    # A write after the snapshot, and an old snapshot time, is the swap condition.
    await demo_ledger.execute(
        "INSERT INTO messages (conversation_id, client_id, role, content) "
        "VALUES ($1, 'client-old', 'user', '{}')",
        thread["id"],
    )
    message = await demo_ledger.fetchrow("SELECT id FROM messages WHERE client_id = 'client-old'")
    await demo_ledger.execute(
        "INSERT INTO audit_log (actor, message_id, group_id, table_name, row_id) "
        "VALUES ('user', $1, 'und_old', 'settings', '1')",
        message["id"],
    )
    await demo_ledger.execute(
        "UPDATE conversations SET snapshot_at = $1 WHERE id = $2",
        datetime.now(UTC) - timedelta(hours=1),
        thread["id"],
    )
    thread = await demo_ledger.fetchrow("SELECT * FROM conversations WHERE id = $1", thread["id"])
    swapped, _ = await refresh_snapshot(demo_ledger, thread, history, cache_ttl_seconds=300)
    assert _count_pins(swapped) == 1


async def test_a_chart_card_survives_a_reload(authed_client, demo_ledger):
    code = (
        'await show_chart(type="line", title="Balance", '
        'series=[{"name": "SBI", "points": [{"x": "Oct", "y": 100000}]}])'
    )
    calls = {"n": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
        return ModelResponse(parts=[TextPart("Here it is.")])

    body = {
        "id": "thread-card",
        "trigger": "submit-message",
        "messages": [
            {"id": "client-card", "role": "user", "parts": [{"type": "text", "text": "chart"}]}
        ],
    }
    with authed_client.app.state.agent.override(model=streaming_model(script)):
        response = authed_client.post("/api/chat", json=body)
    assert response.status_code == 200
    thread = authed_client.get("/api/threads/thread-card/messages").json()
    cards = [
        part
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "data-card"
    ]
    assert cards and cards[0]["data"]["card"] == "chart"


async def test_save_records_keeps_model_history_and_the_transcript(demo_ledger):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    thread = await create_thread(demo_ledger, title="saved")
    history, _ = await ensure_history(demo_ledger, thread)
    from fin_agent.agent.history import record_user_message

    await record_user_message(
        demo_ledger, thread_id=thread["id"], client_id="client-saved", text="hello"
    )
    turn = [
        ModelRequest(parts=[UserPromptPart("hello")]),
        ModelResponse(parts=[TextPart("hi")]),
    ]
    saved = await save_records(
        demo_ledger,
        thread_id=thread["id"],
        run_id="run_saved",
        ui_message_id="client-saved",
        history=history,
        turn_messages=turn,
        ending="complete",
    )
    assert saved["messages"]
    row = await demo_ledger.fetchrow(
        "SELECT model_history, run_started_at FROM conversations WHERE id = $1",
        thread["id"],
    )
    assert row["run_started_at"] is None
    assert row["model_history"]
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM messages WHERE conversation_id = $1", thread["id"]
        )
        == 2
    )


async def test_tool_output_limits_truncate_only_the_return_to_the_model(demo_ledger):
    agent = build_agent()
    seen: list[list] = []

    def script(messages, info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        if not any(
            getattr(part, "tool_name", None) == "run_code"
            for message in messages
            for part in getattr(message, "parts", [])
        ):
            code = "value = 'x' * 20000\nprint(len(value))"
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
        return ModelResponse(parts=[TextPart("It was long.")])

    with agent.override(
        model=__import__("pydantic_ai.models.function", fromlist=["FunctionModel"]).FunctionModel(
            script
        )
    ):
        result = await agent.run("count", deps=AgentDeps(conversation_id="cnv_limit"))
    part = next(
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"
    )
    text = str(part.content)
    assert "20000" in text
    assert len(text) < 11_000
