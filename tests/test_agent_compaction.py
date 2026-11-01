"""Compaction keeps the newest turns and falls back for the summary call."""

from __future__ import annotations

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.compaction import (
    ClearToolResults,
    SummarizingCompaction,
    TieredCompaction,
    compact_now,
)


def _history(turns: int = 12):
    messages = []
    for index in range(turns):
        messages.append(ModelRequest(parts=[UserPromptPart(f"question {index} " + "x" * 200)]))
        messages.append(ModelResponse(parts=[TextPart(f"answer {index} " + "y" * 200)]))
    return messages


async def test_keep_tokens_protects_the_newest_user_turn():
    def summarizer(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("SUMMARY")])

    strategy = TieredCompaction(
        tiers=[
            ClearToolResults(max_tokens=1, keep_pairs=2),
            SummarizingCompaction(
                model=None,
                max_messages=1,
                keep_tokens=80,
                keep_messages=2,
                keep_user_messages=True,
                receipts=True,
            ),
        ],
        target_tokens=10,
    )
    history = _history()
    compacted = await compact_now(strategy, history, model=FunctionModel(summarizer))
    text = " ".join(
        str(part.content)
        for message in compacted
        for part in getattr(message, "parts", [])
        if hasattr(part, "content")
    )
    assert "SUMMARY" in text
    assert "question 11" in text


async def test_the_summary_uses_the_backup_when_the_main_model_is_down():
    def down(messages, info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("primary", "down")

    def summarizer(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("BACKUP SUMMARY")])

    summary = SummarizingCompaction(
        model=FallbackModel(FunctionModel(down), FunctionModel(summarizer)),
        max_messages=1,
        keep_tokens=80,
        keep_messages=2,
        keep_user_messages=True,
        receipts=True,
    )
    compacted = await compact_now(summary, _history(), model=FunctionModel(summarizer))
    text = " ".join(
        str(part.content)
        for message in compacted
        for part in getattr(message, "parts", [])
        if hasattr(part, "content")
    )
    assert "BACKUP SUMMARY" in text


async def test_a_sliding_window_answers_when_the_summary_fails():
    from pydantic_ai_harness.compaction import FallbackCompaction, SlidingWindowCompaction

    def down(messages, info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("primary", "down")

    strategy = FallbackCompaction(
        fallback_chain=[
            SummarizingCompaction(
                model=FunctionModel(down),
                max_messages=1,
                keep_tokens=80,
                keep_messages=2,
                keep_user_messages=True,
            ),
            SlidingWindowCompaction(max_messages=1, keep_tokens=80, receipts=True),
        ]
    )
    compacted = await compact_now(strategy, _history(), model=FunctionModel(down))
    assert len(compacted) < len(_history())
