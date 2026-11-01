"""A smoke test for the detached chat endpoint."""

from __future__ import annotations

from pydantic_ai.models.test import TestModel


def test_chat_streams_and_saves_both_records(authed_client, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Hello from the test model.")
    with authed_client.app.state.agent.override(model=model):
        response = authed_client.post(
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

    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    assert [m["role"] for m in thread["messages"]] == ["user", "assistant"]
    assert thread["messages"][1]["parts"][0]["text"] == "Hello from the test model."


def test_same_message_id_replays_the_saved_turn(authed_client, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Only once.")
    body = {
        "id": "thread-smoke-2",
        "trigger": "submit-message",
        "messages": [{"id": "client-2", "role": "user", "parts": [{"type": "text", "text": "hi"}]}],
    }
    with authed_client.app.state.agent.override(model=model):
        first = authed_client.post("/api/chat", json=body)
        second = authed_client.post("/api/chat", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert "Only once." in second.text
    thread_id = first.headers["x-thread-id"]
    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    assert len([m for m in thread["messages"] if m["role"] == "assistant"]) == 1


def test_threads_are_searchable_renameable_and_flagable(authed_client, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Chai was ₹50.")
    body = {
        "id": "thread-search",
        "trigger": "submit-message",
        "messages": [
            {"id": "client-search", "role": "user", "parts": [{"type": "text", "text": "chai"}]}
        ],
    }
    with authed_client.app.state.agent.override(model=model):
        response = authed_client.post("/api/chat", json=body)
    assert response.status_code == 200

    found = authed_client.get("/api/threads?q=chai").json()["threads"]
    assert any(thread["id"] == "thread-search" for thread in found)

    renamed = authed_client.patch("/api/threads/thread-search", json={"title": "Chai log"}).json()
    assert renamed["title"] == "Chai log"

    thread = authed_client.get("/api/threads/thread-search/messages").json()
    assistant = [m for m in thread["messages"] if m["role"] == "assistant"][-1]
    flagged = authed_client.post(f"/api/messages/{assistant['server_id']}/flag").json()
    assert flagged["flagged_at"]


def test_the_stream_endpoint_is_204_when_nothing_runs(authed_client):
    response = authed_client.get("/api/chat/no-such-thread/stream")
    assert response.status_code == 204


def test_preferences_can_be_turned_off_and_deleted_for_good(authed_client, demo_ledger):
    demo_ledger.execute(
        "INSERT INTO preferences (id, body, created_by) "
        "VALUES ('prf_test', 'No rent warnings', 'agent')"
    )
    demo_ledger.execute(
        """
        INSERT INTO audit_log (actor, group_id, table_name, row_id, before, after)
        VALUES ('agent', 'und_test', 'preferences', 'prf_test',
                '{"body": "No rent warnings"}'::jsonb, '{"body": "No rent warnings"}'::jsonb)
        """
    )
    demo_ledger.commit()
    listed = authed_client.get("/api/preferences").json()["preferences"]
    assert any(item["id"] == "prf_test" for item in listed)

    updated = authed_client.patch("/api/preferences/prf_test", json={"active": False})
    assert updated.status_code == 200
    assert updated.json()["active"] is False

    deleted = authed_client.delete("/api/preferences/prf_test")
    assert deleted.status_code == 200
    row = demo_ledger.execute(
        "SELECT count(*) AS n FROM preferences WHERE id = 'prf_test'"
    ).fetchone()
    assert row["n"] == 0
    audit = demo_ledger.execute(
        "SELECT before, after FROM audit_log "
        "WHERE table_name = 'preferences' AND row_id = 'prf_test'"
    ).fetchall()
    assert audit
    assert all("No rent warnings" not in str(item) for item in audit)
