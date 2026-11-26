"""A smoke test for the detached chat endpoint."""

from __future__ import annotations

from pydantic_ai.models.test import TestModel


async def test_chat_streams_and_saves_both_records(session_client, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Hello from the test model.")
    with session_client.app.state.agent.override(model=model):
        response = session_client.post(
            "/api/chat",
            json={
                "id": "thread-smoke-1",
                "trigger": "submit-message",
                "messages": [
                    {
                        "id": "client-1",
                        "role": "user",
                        "parts": [{"type": "text", "text": "hi"}],
                    }
                ],
            },
        )
    assert response.status_code == 200, response.text
    thread_id = response.headers["x-thread-id"]
    assert '"delta":"Hello "' in response.text
    assert "data: [DONE]" in response.text

    thread = session_client.get(f"/api/threads/{thread_id}/messages").json()
    assert [m["role"] for m in thread["messages"]] == ["user", "assistant"]
    assert thread["messages"][1]["parts"][0]["text"] == "Hello from the test model."


async def test_same_message_id_replays_the_saved_turn(session_client, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Only once.")
    body = {
        "id": "thread-smoke-2",
        "trigger": "submit-message",
        "messages": [{"id": "client-2", "role": "user", "parts": [{"type": "text", "text": "hi"}]}],
    }
    with session_client.app.state.agent.override(model=model):
        first = session_client.post("/api/chat", json=body)
        second = session_client.post("/api/chat", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert "Only once." in second.text
    thread_id = first.headers["x-thread-id"]
    thread = session_client.get(f"/api/threads/{thread_id}/messages").json()
    assert len([m for m in thread["messages"] if m["role"] == "assistant"]) == 1


async def test_threads_are_searchable_renameable_and_flagable(session_client, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Chai was ₹50.")
    body = {
        "id": "thread-search",
        "trigger": "submit-message",
        "messages": [
            {"id": "client-search", "role": "user", "parts": [{"type": "text", "text": "chai"}]}
        ],
    }
    with session_client.app.state.agent.override(model=model):
        response = session_client.post("/api/chat", json=body)
    assert response.status_code == 200

    found = session_client.get("/api/threads?q=chai").json()["threads"]
    assert any(thread["id"] == "thread-search" for thread in found)

    renamed = session_client.patch("/api/threads/thread-search", json={"title": "Chai log"}).json()
    assert renamed["title"] == "Chai log"

    thread = session_client.get("/api/threads/thread-search/messages").json()
    assistant = [m for m in thread["messages"] if m["role"] == "assistant"][-1]
    flagged = session_client.post(f"/api/messages/{assistant['server_id']}/flag").json()
    assert flagged["flagged_at"]


async def test_a_new_thread_gets_a_server_id_before_its_first_message(session_client):
    created = session_client.post("/api/threads", json={"title": "Budget"})

    assert created.status_code == 201
    thread = created.json()
    assert thread["id"].startswith("cnv_")
    assert thread["title"] == "Budget"
    messages = session_client.get(f"/api/threads/{thread['id']}/messages")
    assert messages.status_code == 200
    assert messages.json()["messages"] == []


async def test_the_stream_endpoint_is_204_when_nothing_runs(session_client):
    response = session_client.get("/api/chat/no-such-thread/stream")
    assert response.status_code == 204


async def test_an_answered_confirm_card_settles_on_the_reply_that_offered_it(demo_ledger):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.ui.vercel_ai.response_types import DataChunk

    from arno.agent.history import save_records

    card = {"card": "preference", "title": "Remembered", "text": "No rent warnings"}
    await demo_ledger.execute("INSERT INTO conversations (id, title) VALUES ('thr_settle', 'x')")
    asked = [
        ModelRequest(parts=[UserPromptPart("remember this")]),
        ModelResponse(parts=[ToolCallPart("preference", {"action": "remember"}, "call-1")]),
    ]
    await save_records(
        demo_ledger,
        thread_id="thr_settle",
        run_id="run_1",
        history=[],
        turn_messages=asked,
        ending="complete",
        reply_id="msg_ask",
    )
    waiting = await demo_ledger.fetchval("SELECT content FROM messages WHERE client_id = 'msg_ask'")
    assert waiting["parts"][0]["state"] == "approval-requested"

    answered = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="preference",
                    content={"status": "remembered"},
                    tool_call_id="call-1",
                    metadata=[DataChunk(type="data-card", data=card)],
                )
            ]
        ),
        ModelResponse(parts=[TextPart("Saved.")]),
    ]
    await save_records(
        demo_ledger,
        thread_id="thr_settle",
        run_id="run_2",
        history=asked,
        turn_messages=answered,
        ending="complete",
        reply_id="msg_done",
    )

    rows = {
        row["client_id"]: row["content"]
        for row in await demo_ledger.fetch(
            "SELECT client_id, content FROM messages WHERE conversation_id = 'thr_settle'"
        )
    }
    assert rows["msg_ask"]["parts"][0]["state"] == "output-available"
    assert rows["msg_ask"]["parts"][0]["output"] == {"status": "remembered"}
    cards = [p["data"] for p in rows["msg_done"]["parts"] if p.get("type") == "data-card"]
    assert cards == [card]
    assert not [p for p in rows["msg_ask"]["parts"] if p.get("type") == "data-card"]


async def test_preferences_can_be_turned_off_and_deleted_for_good(session_client, demo_ledger):
    await demo_ledger.execute(
        "INSERT INTO preferences (id, body, created_by) "
        "VALUES ('prf_test', 'No rent warnings', 'agent')"
    )
    await demo_ledger.execute(
        """
        INSERT INTO undo_log (id, actor, summary, snapshot)
        VALUES ('und_test', 'agent', 'Remembered: No rent warnings',
                '[{"table": "preferences", "key": "prf_test", "before": null,
                   "after": {"id": "prf_test", "body": "No rent warnings"}}]'::jsonb)
        """
    )
    listed = session_client.get("/api/preferences").json()["preferences"]
    assert any(item["id"] == "prf_test" for item in listed)

    updated = session_client.patch("/api/preferences/prf_test", json={"active": False})
    assert updated.status_code == 200
    assert updated.json()["active"] is False

    deleted = session_client.delete("/api/preferences/prf_test")
    assert deleted.status_code == 200
    assert await demo_ledger.fetchval("SELECT count(*) FROM preferences WHERE id = 'prf_test'") == 0
    audit = await demo_ledger.fetch("SELECT summary, snapshot FROM undo_log")
    assert audit
    assert all("No rent warnings" not in str(item) for item in audit)
