"""The output-cap note reaches the current SSE stream and the saved transcript once."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, override

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from arno.agent import turns
from arno.agent.deps import AgentDeps
from tests.agent_helpers import streaming_model


class _LengthModel(FunctionModel):
    """A FunctionModel whose streamed response ends the way a max_tokens cutoff does."""

    @asynccontextmanager
    @override
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response:
            response.finish_reason = "length"
            yield response


def _length_model(text: str) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncGenerator[str]:
        del messages, info
        yield text

    return _LengthModel(stream_function=stream)


def _body(thread_id: str, message_id: str) -> dict[str, Any]:
    return {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [
            {"id": message_id, "role": "user", "parts": [{"type": "text", "text": "go on"}]}
        ],
    }


def _events(raw: str) -> list[dict[str, Any]]:
    """The JSON SSE events in wire order; a buffered response keeps that order."""
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        events.append(event)
    return events


async def test_the_output_cap_note_is_saved_and_streamed_once(session_client):
    note = "The reply hit the output cap. Ask for the rest."
    body = _body("thread-cap", "msg-cap")
    with session_client.app.state.agent.override(model=_length_model("half an answer")):
        response = session_client.post("/api/chat", json=body)
    assert response.status_code == 200, response.text

    events = _events(response.text)
    endings = [index for index, event in enumerate(events) if event.get("type") == "data-ending"]
    finishes = [index for index, event in enumerate(events) if event.get("type") == "finish"]
    assert len(endings) == 1, events
    assert events[endings[0]]["data"]["note"] == note
    assert len(finishes) == 1
    # The note is spliced in on the way to finish: a client that sees finish stops reading, so
    # an ending card after it would never reach the browser.
    assert endings[0] < finishes[0]
    assert finishes[0] == len(events) - 1
    assert any(event.get("type") == "text-delta" for event in events[: endings[0]])
    assert any(event.get("type") == "data-reply-model" for event in events)

    thread = session_client.get("/api/threads/thread-cap/messages").json()
    parts = [
        part
        for message in thread["messages"]
        if message["role"] == "assistant"
        for part in message["parts"]
    ]
    saved = [
        part["data"]["note"]
        for part in parts
        if part.get("type") == "data-ending" and isinstance(part.get("data"), dict)
    ]
    assert saved == [note]
    assert any(
        part.get("type") == "text" and "half an answer" in part.get("text", "") for part in parts
    )


async def test_a_complete_reply_without_a_note_does_not_emit_one(session_client):
    body = _body("thread-complete", "msg-complete")
    with session_client.app.state.agent.override(
        model=streaming_model(lambda messages, info: ModelResponse(parts=[TextPart("done")]))
    ):
        response = session_client.post("/api/chat", json=body)
    assert response.status_code == 200, response.text
    assert not any(event.get("type") == "data-ending" for event in _events(response.text))

    thread = session_client.get("/api/threads/thread-complete/messages").json()
    assert not any(
        part.get("type") == "data-ending"
        for message in thread["messages"]
        for part in message["parts"]
    )


async def test_a_content_filter_finish_is_not_an_output_cap(monkeypatch):
    saved: dict = {}

    async def fake_save(conn, **values):
        saved.update(values)

    monkeypatch.setattr(turns, "save_records", fake_save)
    manager = turns.TurnManager(SimpleNamespace(model=SimpleNamespace(model_name="primary-model")))
    turn = turns.ChatTurn(
        thread_id="thread-cap",
        message_id="msg-cap",
        run_id="run-cap",
        history=[],
        deps=AgentDeps(conversation_id="thread-cap"),
    )

    await manager._save(
        turn,
        [
            ModelResponse(
                parts=[TextPart("done")],
                finish_reason="content_filter",
                model_name="primary-model",
            )
        ],
        ending="complete",
    )
    assert saved["save_note"] is None
    assert turn.note is None
