"""Cards that a tool called inside `run_code` produced.

`VercelAIAdapter` streams a tool's metadata as a data part, but it does not look inside a
`run_code` return. Fin-Agent emits those cards itself: from `metadata['tool_returns']`, and again
when the turn is saved so the card survives a reload.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

RUN_CODE_TOOL_NAME = "run_code"


def nested_cards(part: ToolReturnPart) -> list[DataChunk]:
    """Data chunks from every tool return nested in a `run_code` result."""
    metadata = part.metadata
    if not isinstance(metadata, dict):
        return []
    returns = metadata.get("tool_returns")
    if not isinstance(returns, dict):
        return []
    cards: list[DataChunk] = []
    for nested in returns.values():
        if not isinstance(nested, ToolReturnPart):
            continue
        nested_metadata = nested.metadata
        candidates = (
            nested_metadata if isinstance(nested_metadata, (list, tuple)) else [nested_metadata]
        )
        for item in candidates:
            if isinstance(item, DataChunk) and item.type.startswith("data-"):
                cards.append(item)
    return cards


def cards_from_messages(messages: list[ModelMessage]) -> list[DataChunk]:
    cards: list[DataChunk] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == RUN_CODE_TOOL_NAME:
                cards.extend(nested_cards(part))
    return cards


def ui_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """The persisted transcript for one turn, as Vercel AI UI messages."""
    return [
        message.model_dump(mode="json")
        for message in VercelAIAdapter.dump_messages(messages, sdk_version=7)
    ]


def data_part(chunk: DataChunk) -> dict[str, Any]:
    return {
        "type": chunk.type,
        "id": chunk.id,
        "data": chunk.data,
        "transient": chunk.transient,
    }


def attach_to_last_assistant(
    messages: list[dict[str, Any]], chunks: list[DataChunk]
) -> list[dict[str, Any]]:
    """Add cards and notes to the last assistant message, where the UI renders them."""
    if not chunks:
        return messages
    for message in reversed(messages):
        if message.get("role") == "assistant":
            message.setdefault("parts", []).extend(data_part(chunk) for chunk in chunks)
            return messages
    return messages


def with_data(
    message: dict[str, Any], type_name: str, data: Any, identifier: str | None = None
) -> None:
    message.setdefault("parts", []).append(
        {"type": type_name, "id": identifier, "data": data, "transient": None}
    )


__all__ = [
    "attach_to_last_assistant",
    "cards_from_messages",
    "data_part",
    "nested_cards",
    "ui_messages",
    "with_data",
]
