"""Detached turns: reattach, Stop, failure saves, crash recovery, and the deploy drain.

These run a real uvicorn server, because an ASGI test transport buffers the whole response and
would hide exactly the behavior under test.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from contextlib import asynccontextmanager

import httpx
import uvicorn
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai_harness.compaction import is_pinned

from fin_agent.agent.builder import build_agent
from fin_agent.agent.capabilities import MODEL_SETTINGS
from fin_agent.agent.history import recover_crashed_runs
from fin_agent.api import app as app_module
from fin_agent.api.deps import current_session
from fin_agent.ledger.accounts import create_account
from tests.agent_helpers import streaming_model


def _body(thread: str, message: str, text: str = "hello") -> dict:
    return {
        "id": thread,
        "trigger": "submit-message",
        "messages": [{"id": message, "role": "user", "parts": [{"type": "text", "text": text}]}],
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def live_server(app, monkeypatch, model):
    monkeypatch.setattr(
        app_module, "build_agent", lambda **kwargs: build_agent(model=model, **kwargs)
    )
    app.dependency_overrides[current_session] = lambda: {"user_id": "test"}
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    )
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        for _ in range(100):
            try:
                response = await client.get("/api/health")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
        else:  # pragma: no cover
            raise RuntimeError("server did not start")
        try:
            yield client
        finally:
            server.should_exit = True
            thread.join(timeout=10)


async def _first_text_delta(response):
    async for line in response.aiter_lines():
        if '"type":"text-delta"' in line:
            return


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


async def _until(predicate, timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.02)
    return predicate()


async def test_reattach_replays_the_running_turn(app, db, monkeypatch):
    model = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("one two three four five")]),
        text_chunks=5,
        delay=0.3,
    )
    async with live_server(app, monkeypatch, model) as client:
        async with client.stream("POST", "/api/chat", json=_body("thread-re", "msg-re")) as first:
            await _first_text_delta(first)
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
            await _first_text_delta(response)
            stopped = await client.post("/api/chat/thread-stop/stop")
            assert stopped.json()["stopped"] is True
        await _until(lambda: app.state.turns.get("thread-stop") is None)
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
            await _first_text_delta(response)
            await app.state.turns.drain()
        await _until(lambda: app.state.turns.get("thread-drain") is None)
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


CARD_LIMIT = "₹1,00,000"
INVENTED_LIMIT = "₹2,50,000"


def _limit_model() -> FunctionModel:
    """Answer the last user prompt: a limit from the snapshot, or an invented one."""

    def script(messages, info):
        prompt = ""
        for message in reversed(messages):
            if not isinstance(message, ModelRequest):
                continue
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart) and not is_pinned(part):
                    text = part.content if isinstance(part.content, str) else ""
                    if text.startswith("<system>"):  # the per-turn reminder follows the prompt
                        continue
                    prompt = text
                    break
            if prompt:
                break
        amount = INVENTED_LIMIT if "invent" in prompt else CARD_LIMIT
        return ModelResponse(parts=[TextPart(f"Your card limit is {amount}.")])

    return streaming_model(script)


def _flag_amounts(thread: dict) -> list[list[str]]:
    return [
        part["data"]["amounts"]
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "data-number-flags"
    ]


async def test_a_snapshot_only_card_limit_is_grounded_but_inventions_are_flagged(
    app, ledger, monkeypatch
):
    await create_account(
        ledger,
        kind="card",
        name="Slice card",
        last4="2207",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    async with live_server(app, monkeypatch, _limit_model()) as client:

        async def ask(message_id: str, text: str) -> str:
            async with client.stream(
                "POST", "/api/chat", json=_body("thread-card", message_id, text)
            ) as response:
                return await _text_after(response)

        async def saved() -> dict:
            return (await client.get("/api/threads/thread-card/messages")).json()

        limit = f"Your card limit is {CARD_LIMIT}."
        assert await ask("msg-card-1", "what is my card limit") == limit
        assert _flag_amounts(await saved()) == []

        # A continued turn sees the snapshot only in its saved history.
        assert await ask("msg-card-2", "say it again") == limit
        assert _flag_amounts(await saved()) == []

        invented = f"Your card limit is {INVENTED_LIMIT}."
        async with client.stream(
            "POST", "/api/chat", json=_body("thread-card", "msg-card-3", "invent a limit")
        ) as response:
            live = "".join([line async for line in response.aiter_lines()])
        assert '"type":"data-number-flags"' in live
        assert INVENTED_LIMIT in live
        assert _flag_amounts(await saved()) == [[INVENTED_LIMIT]]

        async with client.stream(
            "POST", "/api/chat", json=_body("thread-card", "msg-card-3", "invent a limit")
        ) as replay:
            raw = "".join([line async for line in replay.aiter_lines()])
        assert '"type":"data-number-flags"' in raw
        assert INVENTED_LIMIT in raw

        # Turn 3's own reply is now in history; it must not become evidence.
        assert await ask("msg-card-4", "invent it once more") == invented
        assert _flag_amounts(await saved()) == [[INVENTED_LIMIT], [INVENTED_LIMIT]]
