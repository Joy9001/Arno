"""A statement attached in chat is handed to the parser subagent, never imported directly."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo

from arno.agent.parser import ParseReport
from tests.agent_helpers import parser_agent, streaming_model
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


async def test_the_model_sees_the_file_count_and_no_attachment_id(session_client, ledger):
    attachment = _upload(session_client)
    body = _body("client_statement_text", attachment["id"], "Review this statement")
    seen: list[Any] = []

    with session_client.app.state.agent.override(model=_model(seen)):
        response = session_client.post("/api/chat", json=body)

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
    # The host hands the parser this turn's files, so the main agent never copies an id.
    assert attachment["id"] not in str(seen)
    assert "1 statement file" in str(seen)

    thread = session_client.get("/api/threads/thread_statement/messages").json()
    assert "never-send-this" not in str(thread)
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert any(part["type"] == "data-statement" for part in user["parts"])


async def test_file_only_sends_a_prompt_and_starts_no_import(session_client, ledger):
    attachment = _upload(session_client)
    body = _body("client_statement_only", attachment["id"])

    with session_client.app.state.agent.override(model=_model([])):
        response = session_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0


async def test_replay_and_resend_start_no_import(session_client, ledger):
    attachment = _upload(session_client)
    body = _body("client_statement_replay", attachment["id"], "Review this statement")

    with session_client.app.state.agent.override(model=_model([])):
        first = session_client.post("/api/chat", json=body)
        replay = session_client.post("/api/chat", json=body)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0

    resent = _body("client_statement_resent", attachment["id"], "Try this again")
    with session_client.app.state.agent.override(model=_model([])):
        again = session_client.post("/api/chat", json=resent)

    assert again.status_code == 200
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0


async def test_an_unsupported_chat_statement_is_refused(session_client):
    response = session_client.post(
        "/api/attachments",
        files={"file": ("receipt.png", b"\x89PNG\r\n\x1a\nnot-a-statement", "image/png")},
        data={"purpose": "statement"},
    )
    assert response.status_code == 422
    assert "PDF, CSV" in response.json()["detail"]


async def test_a_stale_attachment_ref_is_refused_without_selecting_another(session_client, ledger):
    body = _body("client_statement_stale", "att_missing", "Review this statement")
    with session_client.app.state.agent.override(model=_model([])):
        response = session_client.post("/api/chat", json=body)

    assert response.status_code == 422
    assert "no longer available" in response.json()["detail"]
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0


async def test_a_busy_chat_starts_no_import(session_client, ledger):
    attachment = _upload(session_client)
    active = SimpleNamespace(done=SimpleNamespace(is_set=lambda: False))
    previous = session_client.app.state.turns.get
    session_client.app.state.turns.get = lambda _thread: active
    try:
        response = session_client.post(
            "/api/chat", json=_body("client_statement_busy", attachment["id"], "Review this")
        )
    finally:
        session_client.app.state.turns.get = previous
    assert response.status_code == 409
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0


# The unread statement is handed to the next turn, and not excused by an older parse ----------


def _turn_body(message_id: str, attachment_id: str | None, text: str | None = None) -> dict:
    parts: list[dict] = []
    if text is not None:
        parts.append({"type": "text", "text": text})
    if attachment_id is not None:
        parts.append(
            {
                "type": "data-statement",
                "data": {"attachment_id": attachment_id, "filename": "sbi.csv"},
            }
        )
    return {
        "id": "thread_unread",
        "trigger": "submit-message",
        "messageId": message_id,
        "messages": [{"id": message_id, "role": "user", "parts": parts}],
    }


def _ignoring_model(seen: list[str | None]):
    def script(messages, info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions)
        return ModelResponse(parts=[TextPart("No statement came through.")])

    return streaming_model(script)


def _notes(thread: dict) -> list[str]:
    return [
        str(part["data"]["note"])
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "data-ending"
    ]


async def _settle_turn(app, thread_id: str) -> None:
    """Wait for the turn's own run to finish, as a finished SSE body means it has."""
    turn = app.state.turns.get(thread_id)
    if turn is not None:
        await asyncio.wait_for(turn.done.wait(), timeout=10)


async def _settle_children(app) -> None:
    await asyncio.wait_for(app.state.tasks.wait_children(None), timeout=10)


def _delegating_model(seen: list[str | None] | None = None):
    calls = {"n": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if seen is not None:
            seen.append(info.instructions)
        if calls["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": "parser",
                            "task": "Import the statement files from this turn.",
                            "background": True,
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Handed it to the parser.")])

    return streaming_model(script)


def _reporting_parser_model():
    def script(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(ParseReport(status="ready", blocking=None).model_dump_json())]
        )

    return streaming_model(script)


async def test_a_turn_that_ignores_the_statement_says_the_file_was_not_read(session_client, ledger):
    from arno.agent.turns import UNREAD_STATEMENT_NOTE_PREFIX

    attachment = _upload(session_client)
    seen: list[str | None] = []

    with session_client.app.state.agent.override(model=_ignoring_model(seen)):
        response = session_client.post(
            "/api/chat", json=_turn_body("msg_ignored", attachment["id"], "Import this statement")
        )

    assert response.status_code == 200, response.text
    thread = session_client.get("/api/threads/thread_unread/messages").json()
    notes = _notes(thread)
    assert any(UNREAD_STATEMENT_NOTE_PREFIX in note for note in notes), notes
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0
    # The model still saw the file: the note is the host's, after the turn.
    assert "1 statement file" in str(seen)


async def test_the_next_turn_carries_the_unread_statement_again(session_client, ledger):
    from arno.agent.turns import UNREAD_STATEMENT_NOTE_PREFIX

    attachment = _upload(session_client)
    seen: list[str | None] = []

    with session_client.app.state.agent.override(model=_ignoring_model(seen)):
        first = session_client.post(
            "/api/chat", json=_turn_body("msg_unread_1", attachment["id"], "Import this statement")
        )
        await _settle_turn(session_client.app, "thread_unread")
        # "try again" has no attachment of its own: the host keeps the unread file with the thread.
        second = session_client.post(
            "/api/chat", json=_turn_body("msg_unread_2", None, "try again")
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert "1 statement file" in str(seen[0])
    assert "1 statement file" in str(seen[1])
    thread = session_client.get("/api/threads/thread_unread/messages").json()
    assert sum(UNREAD_STATEMENT_NOTE_PREFIX in note for note in _notes(thread)) == 2
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0
    # The retry's own message carries no file part; it is handed over, not re-attached.
    user = [message for message in thread["messages"] if message["role"] == "user"][-1]
    assert not any(part["type"] == "data-statement" for part in user["parts"])


async def test_a_turn_that_delegates_gets_no_unread_note(session_client, ledger):
    from arno.agent.turns import UNREAD_STATEMENT_NOTE_PREFIX

    attachment = _upload(session_client)
    app = session_client.app
    tasks = app.state.tasks
    before = frozenset(tasks.records)
    # No server-started turn for the parser's report: this test reads the same thread again.
    app.state.turns.waker = None

    with (
        app.state.agent.override(model=_delegating_model()),
        parser_agent(app).override(model=_reporting_parser_model()),
    ):
        response = session_client.post(
            "/api/chat", json=_turn_body("msg_delegated", attachment["id"], "Import this statement")
        )
        assert response.status_code == 200, response.text
        await _settle_children(app)

    thread = session_client.get("/api/threads/thread_unread/messages").json()
    assert not [note for note in _notes(thread) if UNREAD_STATEMENT_NOTE_PREFIX in note]
    # One upload is still one task, however the file reached the parser.
    assert len([key for key in tasks.records if key not in before]) == 1


async def test_a_parse_in_the_history_does_not_excuse_a_later_upload(session_client, ledger):
    from arno.agent.turns import UNREAD_STATEMENT_NOTE_PREFIX

    app = session_client.app
    app.state.turns.waker = None
    first_file = _upload(session_client)

    with (
        app.state.agent.override(model=_delegating_model()),
        parser_agent(app).override(model=_reporting_parser_model()),
    ):
        first = session_client.post(
            "/api/chat",
            json=_turn_body("msg_first_file", first_file["id"], "Import this statement"),
        )
        assert first.status_code == 200, first.text
        await _settle_children(app)
        await _settle_turn(app, "thread_unread")

    second_file = _upload(session_client)
    with app.state.agent.override(model=_ignoring_model([])):
        second = session_client.post(
            "/api/chat",
            json=_turn_body("msg_second_file", second_file["id"], "Import this one too"),
        )

    assert second.status_code == 200, second.text
    thread = session_client.get("/api/threads/thread_unread/messages").json()
    notes = [note for note in _notes(thread) if UNREAD_STATEMENT_NOTE_PREFIX in note]
    # The first file's parser call is in the history; the second file is still unread.
    assert len(notes) == 1, notes


async def test_the_uploaded_statement_note_reaches_a_later_turn(session_client):
    from pydantic_ai.models.function import FunctionModel

    from arno.agent.builder import _instructions, build_agent
    from arno.agent.deps import AgentDeps

    seen: list[str | None] = []

    def script(messages, info):
        seen.append(info.instructions)
        return ModelResponse(parts=[TextPart("ok")])

    agent = build_agent(model=FunctionModel(script), parser_agent=None)
    deps = AgentDeps(conversation_id="", statement_attachments=("att_1",))
    first = await agent.run("first", deps=deps)
    await agent.run("second", deps=deps, message_history=first.all_messages())

    assert all("Uploaded statement files" in (text or "") for text in seen)
    note = await _instructions(SimpleNamespace(deps=deps))
    assert "Never say a file did not arrive" in note
    assert "This turn has 1 statement file." in note
