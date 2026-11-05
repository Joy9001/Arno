"""Cards that a tool called inside `run_code` produced.

`VercelAIAdapter` streams a tool's metadata as a data part, but it does not look inside a
`run_code` return. Fin-Agent emits those cards itself: from `metadata['tool_returns']`, and again
when the turn is saved so the card survives a reload.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

RUN_CODE_TOOL_NAME = "run_code"


def _as_data_chunk(item: Any) -> DataChunk | None:
    if isinstance(item, DataChunk):
        return item
    if isinstance(item, dict) and str(item.get("type", "")).startswith("data-"):
        return DataChunk(
            type=str(item["type"]),
            id=item.get("id"),
            data=item.get("data"),
            transient=item.get("transient"),
        )
    return None


def _moment(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if isoformat else None


def _chart_source(chunk: DataChunk, script: str | None, computed_at: Any) -> DataChunk:
    """Attach the real script and calculation time to a chart card, never invented data."""
    data = chunk.data
    if not isinstance(data, dict) or data.get("card") != "chart":
        return chunk
    source = {"computed_at": _moment(computed_at)}
    if script:
        source["script"] = script
    enriched = {**data, **{key: value for key, value in source.items() if value is not None}}
    return chunk.model_copy(update={"data": enriched})


def _run_scripts(messages: list[ModelMessage]) -> dict[str, str]:
    scripts: dict[str, str] = {}
    for message in messages:
        for part in getattr(message, "parts", []):
            if not isinstance(part, ToolCallPart) or part.tool_name != RUN_CODE_TOOL_NAME:
                continue
            # A provider may keep args as a JSON string; the library normalizes both shapes and
            # keeps malformed legacy input out of the transcript instead of raising.
            code = part.args_as_dict().get("code")
            if isinstance(code, str) and code.strip():
                scripts[part.tool_call_id] = code
    return scripts


def nested_cards(part: ToolReturnPart, *, script: str | None = None) -> list[DataChunk]:
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
            chunk = _as_data_chunk(item)
            if chunk is not None:
                cards.append(_chart_source(chunk, script, nested.timestamp or part.timestamp))
    return cards


def _cards_from_messages(messages: list[ModelMessage], scripts: dict[str, str]) -> list[DataChunk]:
    cards: list[DataChunk] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == RUN_CODE_TOOL_NAME:
                cards.extend(nested_cards(part, script=scripts.get(part.tool_call_id)))
    return cards


def cards_from_messages(messages: list[ModelMessage]) -> list[DataChunk]:
    return _cards_from_messages(messages, _run_scripts(messages))


def cards_for_transcript(messages: list[ModelMessage]) -> list[DataChunk]:
    """Every card in stored history, including a direct write whose call was in an earlier turn."""
    cards = _cards_from_messages(messages, _run_scripts(messages))
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or part.tool_name == RUN_CODE_TOOL_NAME:
                continue
            metadata = part.metadata
            candidates = metadata if isinstance(metadata, (list, tuple)) else [metadata]
            for item in candidates:
                chunk = _as_data_chunk(item)
                if chunk is not None:
                    cards.append(_chart_source(chunk, None, part.timestamp))
    return cards


def ui_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """The persisted transcript for one turn, as Vercel AI UI messages."""
    return [
        message.model_dump(mode="json", by_alias=True)
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
    "cards_for_transcript",
    "cards_from_messages",
    "data_part",
    "nested_cards",
    "ui_messages",
    "with_data",
]
