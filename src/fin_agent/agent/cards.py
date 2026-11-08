"""Cards that a tool called inside `run_code` produced.

`VercelAIAdapter` streams a tool's metadata as a data part, but it does not look inside a
`run_code` return. Fin-Agent emits those cards itself: from `metadata['tool_returns']`, and again
when the turn is saved so the card survives a reload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
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
    if not isinstance(data, dict):
        return chunk
    if data.get("card") == "answer":
        return _answer_sources(chunk, data, script, computed_at)
    if data.get("card") != "chart":
        return chunk
    source = {"computed_at": _moment(computed_at)}
    if script:
        source["script"] = script
    enriched = {**data, **{key: value for key, value in source.items() if value is not None}}
    return chunk.model_copy(update={"data": enriched})


def _answer_sources(
    chunk: DataChunk, data: dict[str, Any], script: str | None, computed_at: Any
) -> DataChunk:
    """A composed Chart keeps its calculation time; its script is linked by chart id below."""
    del script
    results = data.get("results")
    if not isinstance(results, list):
        return chunk
    enriched: list[Any] = []
    for result in results:
        if not isinstance(result, dict) or result.get("kind") != "chart":
            enriched.append(result)
            continue
        chart = result.get("chart")
        if not isinstance(chart, dict):
            enriched.append(result)
            continue
        moment = _moment(computed_at)
        enriched.append(
            {
                **result,
                "chart": {**chart, **({"computed_at": moment} if moment else {})},
            }
        )
    return chunk.model_copy(update={"data": {**data, "results": enriched}})


def _link_answer_charts(cards: list[DataChunk]) -> list[DataChunk]:
    """Copy each native chart's script into the composed snapshot that names the same chart."""
    charts = {
        str(card.data["chart_id"]): card.data
        for card in cards
        if isinstance(card.data, dict)
        and card.data.get("card") == "chart"
        and card.data.get("chart_id")
    }
    if not charts:
        return cards
    linked: list[DataChunk] = []
    for card in cards:
        data = card.data
        if not isinstance(data, dict) or data.get("card") != "answer":
            linked.append(card)
            continue
        results = data.get("results")
        if not isinstance(results, list):
            linked.append(card)
            continue
        enriched: list[Any] = []
        for result in results:
            chart = result.get("chart") if isinstance(result, dict) else None
            native = charts.get(str(chart.get("chart_id"))) if isinstance(chart, dict) else None
            if native is None:
                enriched.append(result)
                continue
            source = {key: native[key] for key in ("script", "computed_at") if key in native}
            enriched.append({**result, "chart": {**chart, **source}})
        linked.append(card.model_copy(update={"data": {**data, "results": enriched}}))
    return linked


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
    return _link_answer_charts(cards)


def _cards_from_messages(messages: list[ModelMessage], scripts: dict[str, str]) -> list[DataChunk]:
    cards: list[DataChunk] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name == RUN_CODE_TOOL_NAME:
                cards.extend(nested_cards(part, script=scripts.get(part.tool_call_id)))
    return _link_answer_charts(cards)


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
    return _link_answer_charts(cards)


def merge_assistant_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One assistant UI message per run: a turn's responses are consecutive."""
    merged: list[dict[str, Any]] = []
    for message in messages:
        previous = merged[-1] if merged else None
        if (
            message.get("role") == "assistant"
            and previous is not None
            and previous.get("role") == "assistant"
        ):
            previous.setdefault("parts", []).extend(message.get("parts") or [])
            continue
        merged.append(message)
    return merged


def _epoch_ms(value: Any) -> int | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return int((value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp() * 1000)


def run_timing(
    messages: list[ModelMessage],
    *,
    started_at: Any = None,
    finished_at: Any = None,
) -> dict[str, int] | None:
    """The run's recorded span, for `"Worked for …"` on a reload.

    `started_at` is the run marker the thread already keeps; the responses' own timestamps are
    the fallback when a turn is saved outside the chat endpoint.
    """
    moments = [
        message.timestamp
        for message in messages
        if isinstance(message, ModelResponse) and message.timestamp is not None
    ]
    start = _epoch_ms(started_at) or _epoch_ms(moments[0] if moments else None)
    end = _epoch_ms(finished_at) or _epoch_ms(moments[-1] if moments else None)
    if start is None or end is None:
        return None
    return {"streamStartTime": start, "totalStreamTime": max(0, end - start)}


def ui_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """The persisted transcript for one turn, as Vercel AI UI messages."""
    dumped = [
        message.model_dump(mode="json", by_alias=True)
        for message in VercelAIAdapter.dump_messages(messages, sdk_version=7)
    ]
    return merge_assistant_messages(dumped)


def data_part(chunk: DataChunk) -> dict[str, Any]:
    payload = {"type": chunk.type, "data": chunk.data}
    # The UI chunk schema makes these optional, not nullable.
    if chunk.id is not None:
        payload["id"] = chunk.id
    if chunk.transient is not None:
        payload["transient"] = chunk.transient
    return payload


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
    part = {"type": type_name, "data": data}
    if identifier is not None:
        part["id"] = identifier
    message.setdefault("parts", []).append(part)


__all__ = [
    "attach_to_last_assistant",
    "cards_for_transcript",
    "cards_from_messages",
    "data_part",
    "merge_assistant_messages",
    "nested_cards",
    "run_timing",
    "ui_messages",
    "with_data",
]
