"""The two records, old threads, cards surviving a reload, and stored instructions."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo

from arno.agent.history import (
    create_thread,
    load_history,
    save_records,
)
from tests.agent_helpers import streaming_model


async def test_a_new_thread_starts_with_an_empty_history(demo_ledger):
    thread = await create_thread(demo_ledger, title="new")
    assert load_history(thread) == []


async def test_a_thread_saved_with_a_pinned_snapshot_and_a_task_report_still_continues(
    session_client, demo_ledger
):
    """Old threads hold a harness-pinned snapshot part and delegation-report parts."""
    old_history = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=[TextContent('{"liquid_total": {"text": "₹1"}}', metadata="pin")]
                )
            ]
        ),
        ModelResponse(parts=[TextPart("Snapshot read.")]),
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        TextContent(
                            "<task-report>The parser finished.</task-report>",
                            metadata={"kind": "task_report"},
                        )
                    ]
                )
            ]
        ),
        ModelResponse(parts=[TextPart("The import is ready for review.")]),
    ]
    await demo_ledger.execute(
        "INSERT INTO conversations (id, title, model_history) VALUES ('thread-old', 'old', $1)",
        json.loads(ModelMessagesTypeAdapter.dump_json(old_history)),
    )

    def reply(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Still here.")])

    with session_client.app.state.agent.override(model=streaming_model(reply)):
        response = session_client.post(
            "/api/chat",
            json={
                "id": "thread-old",
                "trigger": "submit-message",
                "messages": [
                    {"id": "client-old", "role": "user", "parts": [{"type": "text", "text": "hi"}]}
                ],
            },
        )
    assert response.status_code == 200, response.text
    assert "Still here." in response.text
    saved = await demo_ledger.fetchval(
        "SELECT model_history FROM conversations WHERE id = 'thread-old'"
    )
    assert len(ModelMessagesTypeAdapter.validate_python(saved)) > len(old_history)


async def test_a_query_card_survives_a_reload(session_client, demo_ledger):
    args = {"sql": "SELECT 1 AS one"}
    calls = {"n": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("run_query", args)])
        return ModelResponse(parts=[TextPart("Here it is.")])

    body = {
        "id": "thread-card",
        "trigger": "submit-message",
        "messages": [
            {"id": "client-card", "role": "user", "parts": [{"type": "text", "text": "chart"}]}
        ],
    }
    with session_client.app.state.agent.override(model=streaming_model(script)):
        response = session_client.post("/api/chat", json=body)
    assert response.status_code == 200
    thread = session_client.get("/api/threads/thread-card/messages").json()
    cards = [
        part
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "data-card"
    ]
    assert cards and cards[0]["data"]["card"] == "query"


async def test_save_records_keeps_model_history_and_the_transcript(demo_ledger):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    thread = await create_thread(demo_ledger, title="saved")
    history = load_history(thread)
    from arno.agent.history import record_user_message

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


async def test_a_multi_step_turn_is_saved_as_one_assistant_message(demo_ledger):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from arno.agent.history import record_user_message

    thread = await create_thread(demo_ledger, title="steps")
    history = load_history(thread)
    await record_user_message(
        demo_ledger, thread_id=thread["id"], client_id="client-steps", text="balance"
    )
    started = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    finished = datetime(2026, 1, 1, 10, 2, 13, tzinfo=UTC)
    call = ToolCallPart("get_financial_context", {})
    turn = [
        ModelRequest(parts=[UserPromptPart("balance")]),
        ModelResponse(parts=[TextPart("Let me check."), call], timestamp=started),
        ModelRequest(
            parts=[ToolReturnPart("get_financial_context", "1", tool_call_id=call.tool_call_id)]
        ),
        ModelResponse(parts=[TextPart("Your balance is 1.")], timestamp=finished),
    ]
    await save_records(
        demo_ledger,
        thread_id=thread["id"],
        run_id="run_steps",
        history=history,
        turn_messages=turn,
        ending="complete",
        reply_id="msg_live_steps",
    )
    rows = await demo_ledger.fetch(
        "SELECT client_id, content FROM messages WHERE conversation_id = $1 AND role = 'assistant'",
        thread["id"],
    )
    assert len(rows) == 1
    stored = rows[0]["content"]
    assert rows[0]["client_id"] == "msg_live_steps"
    assert stored["id"] == "msg_live_steps"
    assert [part["type"] for part in stored["parts"]] == [
        "text",
        "tool-get_financial_context",
        "text",
    ]
    timing = stored["metadata"]["timing"]
    assert timing["streamStartTime"] == int(started.timestamp() * 1000)
    assert timing["totalStreamTime"] == 133_000
    assert timing["toolCallCount"] == 1
    assert stored["metadata"]["custom"]["runStatus"] == "complete"


async def test_a_reloaded_multi_step_turn_is_one_assistant_message(session_client, demo_ledger):
    def script(messages, info: AgentInfo) -> ModelResponse:
        done = any(
            getattr(part, "tool_name", None) == "run_query"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if done:
            return ModelResponse(parts=[TextPart("All done.")])
        return ModelResponse(
            parts=[TextPart("Checking."), ToolCallPart("run_query", {"sql": "SELECT 1"})]
        )

    body = {
        "id": "thread-steps",
        "trigger": "submit-message",
        "messages": [
            {"id": "client-steps", "role": "user", "parts": [{"type": "text", "text": "balance"}]}
        ],
    }
    with session_client.app.state.agent.override(model=streaming_model(script)):
        response = session_client.post("/api/chat", json=body)
    assert response.status_code == 200
    start = next(
        event
        for event in response.text.splitlines()
        if event.startswith("data: ") and '"type":"start"' in event
    )
    live_id = json.loads(start[len("data: ") :])["messageId"]

    thread = session_client.get("/api/threads/thread-steps/messages").json()
    assert [message["role"] for message in thread["messages"]] == ["user", "assistant"]
    assistant = thread["messages"][1]
    assert assistant["id"] == live_id
    assert [
        part["type"] for part in assistant["parts"] if not part["type"].startswith("data-")
    ] == ["text", "tool-run_query", "text"]
    assert assistant["metadata"]["timing"]["streamStartTime"] > 0
    assert assistant["metadata"]["timing"]["totalStreamTime"] >= 0
    assert assistant["metadata"]["custom"]["runStatus"] == "complete"


def test_saved_history_keeps_the_instructions_on_the_last_request_only():
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from arno.agent.history import _dump

    messages = [
        ModelRequest(parts=[UserPromptPart("one")], instructions="long instructions"),
        ModelResponse(parts=[TextPart("a")]),
        ModelRequest(parts=[UserPromptPart("two")], instructions="long instructions"),
    ]

    dumped = _dump(messages)

    assert dumped[0].get("instructions") is None
    assert dumped[2]["instructions"] == "long instructions"
