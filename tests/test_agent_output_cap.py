"""The output-cap note reaches the current stream and the saved transcript once."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from fin_agent.agent import turns
from fin_agent.agent.deps import AgentDeps


class _FinishAdapter:  # a test double for VercelAIAdapter: only `encode_stream` is called
    def encode_stream(self, stream):
        async def chunks():
            yield 'data: {"type":"finish"}\n\n'

        return chunks()


def _manager() -> turns.TurnManager:
    return turns.TurnManager(SimpleNamespace(model=SimpleNamespace(model_name="primary-model")))


def _turn() -> turns.ChatTurn:
    return turns.ChatTurn(
        thread_id="thread-cap",
        message_id="msg-cap",
        run_id="run-cap",
        history=[],
        deps=AgentDeps(conversation_id="thread-cap"),
    )


async def test_the_output_cap_note_is_saved_and_streamed_once(monkeypatch):
    saved: dict = {}

    async def fake_save(conn, **values):
        saved.update(values)

    monkeypatch.setattr(turns, "save_records", fake_save)
    manager = _manager()
    turn = _turn()

    await manager._save(
        turn,
        [
            ModelResponse(
                parts=[TextPart("half an answer")],
                finish_reason="length",
                model_name="primary-model",
            )
        ],
        ending="complete",
    )

    note = "The reply hit the output cap. Ask for the rest."
    assert saved["save_note"] == note
    assert saved["reply_model"] == "primary-model"
    assert saved["reply_fallback"] is False
    assert turn.note == note

    chunks = [
        chunk
        async for chunk in manager._encoded(
            cast(VercelAIAdapter, _FinishAdapter()), None, [[]], turn
        )
    ]
    endings = [chunk for chunk in chunks if '"type":"data-ending"' in chunk]
    assert len(endings) == 1
    assert note in endings[0]
    assert any('"type":"data-reply-model"' in chunk for chunk in chunks)


async def test_a_complete_reply_without_a_note_does_not_emit_one(monkeypatch):
    async def fake_save(conn, **values):
        del conn, values

    monkeypatch.setattr(turns, "save_records", fake_save)
    manager = _manager()
    turn = _turn()

    await manager._save(
        turn,
        [ModelResponse(parts=[TextPart("done")], model_name="primary-model")],
        ending="complete",
    )

    chunks = [
        chunk
        async for chunk in manager._encoded(
            cast(VercelAIAdapter, _FinishAdapter()), None, [[]], turn
        )
    ]
    assert not any('"type":"data-ending"' in chunk for chunk in chunks)
