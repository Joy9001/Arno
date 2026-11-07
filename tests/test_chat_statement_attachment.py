"""A statement attached in chat is handed to the parser subagent, never imported directly."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo

from tests.agent_helpers import streaming_model
from tests.import_fixtures import sbi_csv


def _upload(client) -> dict:
    response = client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
        data={"purpose": "statement"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _body(message_id: str, attachment_id: str, text: str | None = None) -> dict:
    parts: list[dict] = []
    if text is not None:
        parts.append({"type": "text", "text": text})
    parts.append(
        {
            "type": "data-statement",
            "data": {
                "attachment_id": attachment_id,
                "filename": "sbi.csv",
                "detected_kind": "text",
                "password": "never-send-this",
            },
        }
    )
    return {
        "id": "thread_statement",
        "trigger": "submit-message",
        "messageId": message_id,
        "messages": [{"id": message_id, "role": "user", "parts": parts}],
    }


def _model(seen: list[Any]):
    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        seen.extend(messages)
        return ModelResponse(parts=[TextPart("I'll hand the statement to the parser.")])

    return streaming_model(script)


def _events(response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


async def test_the_model_sees_the_attachment_id_and_no_import_starts(authed_client, ledger):
    attachment = _upload(authed_client)
    body = _body("client_statement_text", attachment["id"], "Review this statement")
    seen: list[Any] = []

    with authed_client.app.state.agent.override(model=_model(seen)):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    events = _events(response)
    assert sum(event["type"] == "start" for event in events) == 1
    assert sum(event["type"] == "finish" for event in events) == 1
    assert sum(event["type"] == "data-card" for event in events) == 0
    assert "".join(event["delta"] for event in events if event["type"] == "text-delta") == (
        "I'll hand the statement to the parser."
    )
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0
    assert await ledger.fetchval("SELECT count(*) FROM jobs WHERE kind = 'import_statement'") == 0
    # The instructions name the attachment ID so the main agent can delegate it.
    assert attachment["id"] in str(seen)

    thread = authed_client.get("/api/threads/thread_statement/messages").json()
    assert "never-send-this" not in str(thread)
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert any(part["type"] == "data-statement" for part in user["parts"])


async def test_file_only_sends_a_prompt_and_starts_no_import(authed_client, ledger):
    attachment = _upload(authed_client)
    body = _body("client_statement_only", attachment["id"])

    with authed_client.app.state.agent.override(model=_model([])):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0


async def test_replay_and_resend_start_no_import(authed_client, ledger):
    attachment = _upload(authed_client)
    body = _body("client_statement_replay", attachment["id"], "Review this statement")

    with authed_client.app.state.agent.override(model=_model([])):
        first = authed_client.post("/api/chat", json=body)
        replay = authed_client.post("/api/chat", json=body)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0

    resent = _body("client_statement_resent", attachment["id"], "Try this again")
    with authed_client.app.state.agent.override(model=_model([])):
        again = authed_client.post("/api/chat", json=resent)

    assert again.status_code == 200
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0


async def test_an_unsupported_chat_statement_is_refused(authed_client):
    response = authed_client.post(
        "/api/attachments",
        files={"file": ("receipt.png", b"\x89PNG\r\n\x1a\nnot-a-statement", "image/png")},
        data={"purpose": "statement"},
    )
    assert response.status_code == 422
    assert "PDF, CSV" in response.json()["detail"]


async def test_a_stale_attachment_ref_is_refused_without_selecting_another(authed_client, ledger):
    body = _body("client_statement_stale", "att_missing", "Review this statement")
    with authed_client.app.state.agent.override(model=_model([])):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 422
    assert "no longer available" in response.json()["detail"]
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0


async def test_a_busy_chat_starts_no_import(authed_client, ledger):
    attachment = _upload(authed_client)
    active = SimpleNamespace(done=SimpleNamespace(is_set=lambda: False))
    previous = authed_client.app.state.turns.get
    authed_client.app.state.turns.get = lambda _thread: active
    try:
        response = authed_client.post(
            "/api/chat", json=_body("client_statement_busy", attachment["id"], "Review this")
        )
    finally:
        authed_client.app.state.turns.get = previous
    assert response.status_code == 409
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0
