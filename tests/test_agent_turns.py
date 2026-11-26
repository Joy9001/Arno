"""Detached turns: reattach, Stop, failure saves, crash recovery, and the deploy drain.

These run a real uvicorn server, because an ASGI test transport buffers the whole response and
would hide exactly the behavior under test.
"""

from __future__ import annotations

import json

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from arno.agent.capabilities import MODEL_SETTINGS, TURN_LIMITS
from arno.agent.history import recover_crashed_runs
from tests.agent_helpers import streaming_model
from tests.fixtures.chat_server import first_text_delta, live_server, wait_until


def _body(thread: str, message: str, text: str = "hello") -> dict:
    return {
        "id": thread,
        "trigger": "submit-message",
        "messages": [{"id": message, "role": "user", "parts": [{"type": "text", "text": text}]}],
    }


async def _text_after(response, prefix: str = "") -> str:
    text = prefix
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        raw = line[6:].strip()
        if raw == "[DONE]":
            break
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text-delta":
            text += event.get("delta", "")
    return text


async def test_reattach_replays_the_running_turn(app, db, monkeypatch):
    model = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("one two three four five")]),
        text_chunks=5,
        delay=0.3,
    )
    async with live_server(app, monkeypatch, model) as client:
        async with client.stream("POST", "/api/chat", json=_body("thread-re", "msg-re")) as first:
            await first_text_delta(first)
        # The reader left; the run continues and a second reader gets it from the start.
        async with client.stream("GET", "/api/chat/thread-re/stream") as second:
            assert second.status_code == 200
            text = await _text_after(second)
        assert text == "one two three four five"
        thread = (await client.get("/api/threads/thread-re/messages")).json()
        assert any(
            part.get("type") == "text" and part.get("text") == "one two three four five"
            for message in thread["messages"]
            for part in message.get("parts", [])
        )


async def test_stop_saves_the_partial_reply(app, db, monkeypatch):
    model = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("alpha beta gamma delta epsilon")]),
        text_chunks=5,
        delay=0.5,
    )
    async with live_server(app, monkeypatch, model) as client:
        async with client.stream(
            "POST", "/api/chat", json=_body("thread-stop", "msg-stop")
        ) as response:
            await first_text_delta(response)
            stopped = await client.post("/api/chat/thread-stop/stop")
            assert stopped.json()["stopped"] is True
        await wait_until(lambda: app.state.turns.get("thread-stop") is None)
        thread = (await client.get("/api/threads/thread-stop/messages")).json()
        assistant = [m for m in thread["messages"] if m["role"] == "assistant"]
        assert assistant
        assert thread["running"] is False
        notes = [
            part
            for message in assistant
            for part in message.get("parts", [])
            if part.get("type") == "data-ending"
        ]
        assert notes


async def test_a_model_failure_saves_a_plain_note(app, db, monkeypatch):
    async def broken(messages, info):
        raise RuntimeError("provider exploded at 10.0.0.5:5432")
        yield ""  # pragma: no cover

    async with live_server(app, monkeypatch, FunctionModel(stream_function=broken)) as client:
        async with client.stream(
            "POST", "/api/chat", json=_body("thread-fail", "msg-fail")
        ) as response:
            raw = "".join([line async for line in response.aiter_lines()])
        assert "10.0.0.5" not in raw
        assert '"type":"error"' in raw
        thread = (await client.get("/api/threads/thread-fail/messages")).json()
        assert any(
            part.get("type") == "data-ending"
            for message in thread["messages"]
            for part in message.get("parts", [])
        )
        assert all(
            part.get("type") != "data-reply-model"
            for message in thread["messages"]
            for part in message.get("parts", [])
        )


async def test_a_failed_turn_saves_only_itself_not_the_history_before_it(app, db, monkeypatch):
    calls = {"n": 0}

    async def first_ok_then_broken(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            yield "The first answer."
            return
        raise RuntimeError("provider dropped the stream")
        yield ""  # pragma: no cover

    model = FunctionModel(stream_function=first_ok_then_broken)
    async with live_server(app, monkeypatch, model) as client:
        for message, text in (("msg-a", "first"), ("msg-b", "second")):
            async with client.stream(
                "POST", "/api/chat", json=_body("thread-twice", message, text)
            ) as response:
                [line async for line in response.aiter_lines()]
        thread = (await client.get("/api/threads/thread-twice/messages")).json()

    texts = [
        part.get("text")
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "text"
    ]
    # The failure saved its own turn only: the first answer is not copied a second time.
    assert texts.count("The first answer.") == 1
    history = await db.fetchval("SELECT model_history FROM conversations WHERE id = 'thread-twice'")
    assert json.dumps(history).count("The first answer.") == 1


async def test_crash_recovery_leaves_a_note_and_clears_the_marker(db):
    await db.execute(
        "INSERT INTO conversations (id, title, run_started_at, active_run_id) "
        "VALUES ('thread-crash', 'crash', now(), 'run_x')"
    )
    recovered = await recover_crashed_runs(db)
    assert recovered and recovered[0]["thread_id"] == "thread-crash"
    row = await db.fetchrow(
        "SELECT run_started_at, active_run_id FROM conversations WHERE id = 'thread-crash'"
    )
    assert row["run_started_at"] is None
    assert row["active_run_id"] is None
    message = await db.fetchrow(
        "SELECT content FROM messages WHERE conversation_id = 'thread-crash'"
    )
    assert "interrupted by a restart" in json.dumps(message["content"])


async def test_deploy_drain_stops_and_saves(app, db, monkeypatch):
    model = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("slow " * 50)]),
        text_chunks=50,
        delay=0.3,
    )
    async with live_server(app, monkeypatch, model) as client:
        app.state.turns.drain_seconds = 0.1
        async with client.stream(
            "POST", "/api/chat", json=_body("thread-drain", "msg-drain")
        ) as response:
            await first_text_delta(response)
            await app.state.turns.drain()
        await wait_until(lambda: app.state.turns.get("thread-drain") is None)
    assert app.state.turns.get("thread-drain") is None
    row = await db.fetchrow("SELECT run_started_at FROM conversations WHERE id = 'thread-drain'")
    assert row["run_started_at"] is None


async def test_a_chat_turn_has_no_total_time_limit(app, db, monkeypatch):
    """Stop, usage limits and the stall timeout bound a turn; a wall clock does not."""
    seen: dict = {}
    original = VercelAIAdapter.run_stream

    def recording(self, **kwargs):
        seen.update(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(VercelAIAdapter, "run_stream", recording)
    model = streaming_model(lambda messages, info: ModelResponse(parts=[TextPart("hello")]))
    async with live_server(app, monkeypatch, model) as client:
        async with client.stream(
            "POST", "/api/chat", json=_body("thread-limit", "msg-limit")
        ) as response:
            assert await _text_after(response) == "hello"
    assert "timeout" not in seen
    assert seen["model_settings"] is MODEL_SETTINGS
    assert seen["usage_limits"] is TURN_LIMITS
