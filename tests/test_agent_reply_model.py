"""Each assistant reply names the model that actually answered it."""

from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel

from arno.agent.builder import build_agent
from tests.agent_helpers import streaming_model


def _reply_model(model_name: str) -> FunctionModel:
    async def reply(messages, info) -> AsyncIterator[str]:
        yield "hello"

    return FunctionModel(stream_function=reply, model_name=model_name)


def _send(authed_client, thread: str, message: str) -> tuple[str, dict]:
    with authed_client.stream(
        "POST",
        "/api/chat",
        json={
            "id": thread,
            "trigger": "submit-message",
            "messages": [
                {"id": message, "role": "user", "parts": [{"type": "text", "text": "hi"}]}
            ],
        },
    ) as response:
        raw = "".join(response.iter_lines())
    return raw, authed_client.get(f"/api/threads/{thread}/messages").json()


def _part(thread: dict, type_name: str) -> dict:
    return next(
        part
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == type_name
    )


async def test_a_primary_reply_labels_the_actual_model_live_and_after_reload(
    authed_client, demo_ledger
):
    agent = build_agent(model=_reply_model("primary-model"))
    authed_client.app.state.agent = agent
    authed_client.app.state.turns.agent = agent

    raw, thread = _send(authed_client, "thread-primary-model", "client-primary")

    assert '"type":"data-reply-model"' in raw
    assert '"model":"primary-model"' in raw
    assert '"fallback":false' in raw
    part = _part(thread, "data-reply-model")
    assert part["data"] == {"model": "primary-model", "fallback": False}


async def test_a_fallback_reply_labels_the_backup_and_says_it_fell_back(authed_client, demo_ledger):
    def failing(messages, info) -> ModelResponse:
        raise ModelAPIError("primary", "provider is down")

    agent = build_agent(model=streaming_model(failing), backup=_reply_model("backup-model"))
    authed_client.app.state.agent = agent
    authed_client.app.state.turns.agent = agent

    raw, thread = _send(authed_client, "thread-reply-fallback", "client-fallback")

    assert '"type":"data-reply-model"' in raw
    assert '"model":"backup-model"' in raw
    assert '"fallback":true' in raw
    part = _part(thread, "data-reply-model")
    assert part["data"] == {"model": "backup-model", "fallback": True}
    assert _part(thread, "data-model-badge")["data"] == {"model": "backup-model"}
