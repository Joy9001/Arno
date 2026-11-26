"""Threads and the two records saved on every ending.

`conversations.model_history` is the model's whole memory: the `ModelMessage` list, the one record
the agent reads back. `messages` holds what the transcript shows, one row per message, and never
changes after it is saved. Both are written in one transaction on a finish, a stop, and a failure;
`run_started_at` catches the one ending nobody can save (a crash).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from arno.db.conn import Connection
from arno.ledger.audit import new_id

RUN_STATUS = {"complete": "complete", "cancelled": "cancelled", "failed": "error"}
IMAGE_PLACEHOLDER = "[Image omitted from stored history]"


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


def with_data(
    message: dict[str, Any], type_name: str, data: Any, identifier: str | None = None
) -> None:
    part = {"type": type_name, "data": data}
    if identifier is not None:
        part["id"] = identifier
    message.setdefault("parts", []).append(part)


def _dump(messages: list[Any]) -> list[dict[str, Any]]:
    cleaned = _strip_binary_content(_latest_instructions_only(messages))
    dumped: list[dict[str, Any]] = json.loads(ModelMessagesTypeAdapter.dump_json(cleaned))
    return dumped


def _strip_binary_content(messages: list[Any]) -> list[Any]:
    """Never persist raw image bytes or base64 blobs inside `conversations.model_history`."""

    def clean(part: Any) -> Any:
        content = getattr(part, "content", None)
        if (
            isinstance(part, UserPromptPart | ToolReturnPart)
            and isinstance(content, (list, tuple))
            and any(isinstance(item, BinaryContent) for item in content)
        ):
            placeholders = (
                IMAGE_PLACEHOLDER if isinstance(i, BinaryContent) else i for i in content
            )
            return replace(part, content=type(content)(placeholders))
        return part

    return [
        replace(message, parts=[clean(part) for part in message.parts])
        if isinstance(message, ModelRequest)
        else message
        for message in messages
    ]


def _latest_instructions_only(messages: list[Any]) -> list[Any]:
    """Keep the system instructions on the last request only.

    Every request carries the full instructions (about 33 KB here), and the next run sends its own
    for the new request, so earlier copies only made each saved thread grow by that much per step:
    98 messages weighed 1 MB and the whole value is rewritten every turn.
    """
    last = max(
        (index for index, message in enumerate(messages) if isinstance(message, ModelRequest)),
        default=-1,
    )
    return [
        replace(message, instructions=None)
        if isinstance(message, ModelRequest) and index != last and message.instructions
        else message
        for index, message in enumerate(messages)
    ]


def _load(data: Any) -> list[Any]:
    if not data:
        return []
    # Rows saved before failed turns were cleaned can still hold an empty response.
    return without_empty_responses(ModelMessagesTypeAdapter.validate_python(data))


def without_empty_responses(messages: list[Any]) -> list[Any]:
    """Drop model responses that carry no answer and no tool call.

    A request the provider failed or the user stopped leaves a response with no parts, or with
    only reasoning. Kept, it is sent back on every later turn as an empty assistant message, which
    some providers reject and every model misreads.
    """

    def answers(part: Any) -> bool:
        if isinstance(part, TextPart):
            return bool(part.content.strip())
        return isinstance(part, ToolCallPart)

    return [
        message
        for message in messages
        if not isinstance(message, ModelResponse) or any(answers(part) for part in message.parts)
    ]


async def create_thread(
    conn: Connection, *, title: str | None = None, thread_id: str | None = None
) -> dict[str, Any]:
    if thread_id:
        row = await conn.fetchrow_required(
            "INSERT INTO conversations (id, title) VALUES ($1, $2) RETURNING *",
            thread_id,
            title,
        )
    else:
        row = await conn.fetchrow_required(
            "INSERT INTO conversations (title) VALUES ($1) RETURNING *", title
        )
    return row


async def get_thread(conn: Connection, thread_id: str) -> dict[str, Any] | None:
    return await conn.fetchrow("SELECT * FROM conversations WHERE id = $1", thread_id)


def title_for(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:80] or "New chat"


def load_history(thread: dict[str, Any]) -> list[Any]:
    """The model's history for a thread; empty for a new one."""
    return _load(thread.get("model_history"))


async def record_user_message(
    conn: Connection,
    *,
    thread_id: str,
    client_id: str,
    text: str,
    statements: list[dict[str, str]] | None = None,
    photos: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Store the user's message at the start, so a reload mid-reply keeps it."""
    safe = text
    parts: list[dict[str, Any]] = []
    if safe:
        parts.append({"type": "text", "text": safe})
    for statement in statements or []:
        parts.append(
            {
                "type": "data-statement",
                "data": {
                    "attachment_id": statement["attachment_id"],
                    "filename": statement["filename"],
                    **({"import_id": statement["import_id"]} if "import_id" in statement else {}),
                },
            }
        )
    for photo in photos or []:
        parts.append(
            {
                "type": "data-photo",
                "data": {
                    "attachment_id": photo["attachment_id"],
                    "filename": photo["filename"],
                },
            }
        )
    content: dict[str, Any] = {
        "id": client_id,
        "role": "user",
        "parts": parts,
    }
    row = await conn.fetchrow(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES ($1, $2, 'user', $3, $4)
        ON CONFLICT (client_id) DO NOTHING
        RETURNING *
        """,
        thread_id,
        client_id,
        content,
        safe,
    )
    if row is None:
        # `ON CONFLICT` keeps the row an earlier retry of this message already stored.
        row = await conn.fetchrow_required("SELECT * FROM messages WHERE client_id = $1", client_id)
    return row


async def mark_run_started(conn: Connection, thread_id: str, run_id: str) -> None:
    await conn.execute(
        """
        UPDATE conversations
        SET run_started_at = now(), active_run_id = $1, updated_at = now()
        WHERE id = $2
        """,
        run_id,
        thread_id,
    )


def _body_text(message: dict[str, Any]) -> str:
    """The message's words, a blank line between text parts so none run together."""
    texts = (
        str(part.get("text", "")).strip()
        for part in message.get("parts") or []
        if part.get("type") == "text"
    )
    return "\n\n".join(text for text in texts if text)


def _transcript_view(messages: list[Any]) -> list[Any]:
    """The turn's messages as the transcript should show them.

    `dump_messages` runs a response's consecutive text parts together ("done.Next"), so they get a
    blank line between them first. A response whose tool call came back as a retry was a draft
    that talked about data it never got, so its words are dropped and only the answer survives.
    """
    retried = {
        str(part.tool_call_id)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.outcome == "retried"
    }
    view: list[Any] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            view.append(message)
            continue
        draft = any(str(call.tool_call_id) in retried for call in message.tool_calls)
        parts: list[Any] = []
        for part in message.parts:
            if isinstance(part, TextPart):
                if draft:
                    continue
                if parts and isinstance(parts[-1], TextPart):
                    joined = f"{parts[-1].content.strip()}\n\n{part.content.strip()}"
                    parts[-1] = replace(parts[-1], content=joined)
                    continue
            parts.append(part)
        view.append(replace(message, parts=parts) if parts else message)
    return view


def _answers_to_earlier_calls(turn_messages: list[Any]) -> list[ToolReturnPart]:
    """The tool results this turn holds for calls an earlier reply made: a resumed confirm card."""
    own = {
        call.tool_call_id
        for message in turn_messages
        if isinstance(message, ModelResponse)
        for call in message.tool_calls
    }
    return [
        part
        for message in turn_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id not in own
    ]


async def _settle_answered_cards(
    conn: Connection,
    thread_id: str,
    history: list[Any],
    turn_messages: list[Any],
    answers: list[ToolReturnPart],
) -> None:
    """Settle the confirm cards this turn answered, on the replies that offered them.

    A resumed run carries only the tool result; its call lives in an earlier reply that is still
    saved as waiting. The transcript of history plus this turn has both, so the waiting part is
    replaced by the settled one.
    """
    answered = {part.tool_call_id for part in answers}
    if not answered:
        return
    settled = {
        part["toolCallId"]: part
        for message in ui_messages(_transcript_view([*history, *turn_messages]))
        for part in message.get("parts", [])
        if part.get("toolCallId") in answered and part.get("state") != "approval-requested"
    }
    rows = await conn.fetch(
        "SELECT id, content FROM messages WHERE conversation_id = $1 AND role = 'assistant'",
        thread_id,
    )
    for row in rows:
        content = row["content"]
        parts = content.get("parts", [])
        fresh = [
            settled.get(part.get("toolCallId"), part)
            if part.get("state") == "approval-requested"
            else part
            for part in parts
        ]
        if fresh != parts:
            await conn.execute(
                "UPDATE messages SET content = $1 WHERE id = $2",
                {**content, "parts": fresh},
                row["id"],
            )


async def save_records(
    conn: Connection,
    *,
    thread_id: str,
    run_id: str,
    history: list[Any],
    turn_messages: list[Any],
    ending: str,
    trace_id: str | None = None,
    model_badge: str | None = None,
    reply_model: str | None = None,
    reply_fallback: bool = False,
    save_note: str | None = None,
    title: str | None = None,
    all_messages: list[Any] | None = None,
    reply_id: str | None = None,
) -> dict[str, Any]:
    """Write both records in one transaction and clear the running marker.

    The user's own message is stored when the turn starts and is never rewritten. `reply_id` is
    the id the stream already gave the browser; the reply is saved under it.
    """
    run_started_at = await conn.fetchval(
        "SELECT run_started_at FROM conversations WHERE id = $1", thread_id
    )
    ui = [
        message
        for message in ui_messages(_transcript_view(turn_messages))
        if message.get("role") == "assistant"
    ]
    # Resumed calls have their original call in a previous turn; keep their UI metadata.
    answers = _answers_to_earlier_calls(turn_messages)
    for part in answers:
        chunks = part.metadata if isinstance(part.metadata, (list, tuple)) else [part.metadata]
        if ui:
            for item in chunks:
                if isinstance(item, DataChunk):
                    ui[-1].setdefault("parts", []).append(data_part(item))
    reply: dict[str, Any] | None = ui[-1] if ui else None
    if reply is None and (save_note or ending in {"failed", "cancelled"}):
        reply = {"id": await new_id(conn, "msg"), "role": "assistant", "parts": []}
        ui.append(reply)
    if reply is not None:
        if reply_id:
            reply["id"] = reply_id
        if model_badge:
            with_data(reply, "data-model-badge", {"model": model_badge})
        if reply_model:
            with_data(reply, "data-reply-model", {"model": reply_model, "fallback": reply_fallback})
        if save_note:
            with_data(reply, "data-ending", {"note": save_note})
        if ending == "cancelled":
            with_data(reply, "data-ending", {"note": "Stopped."})
        if trace_id:
            reply["trace_id"] = trace_id
        timing = run_timing(
            turn_messages,
            started_at=run_started_at,
            # A finished run ends at its last response; a stopped or failed one ends now.
            finished_at=None if ending == "complete" else datetime.now(UTC),
        )
        metadata: dict[str, Any] = reply.get("metadata") or {}
        reply["metadata"] = metadata
        if timing is not None:
            tools = sum(str(part.get("type", "")).startswith("tool-") for part in reply["parts"])
            metadata["timing"] = {**timing, "toolCallCount": tools, "totalChunks": 0}
        metadata.setdefault("custom", {})["runStatus"] = RUN_STATUS.get(ending, "incomplete")

    inserted: list[str] = []
    for message in ui:
        client_id = str(message.get("id") or "") or await new_id(conn, "msg")
        message["id"] = client_id
        row = await conn.fetchrow(
            """
            INSERT INTO messages
                (conversation_id, client_id, role, content, body_text, run_id, trace_id)
            VALUES ($1, $2, 'assistant', $3, $4, $5, $6)
            ON CONFLICT (client_id) DO NOTHING
            RETURNING id
            """,
            thread_id,
            client_id,
            message,
            _body_text(message),
            run_id,
            trace_id if message is reply else None,
        )
        if row:
            inserted.append(str(row["id"]))

    await _settle_answered_cards(conn, thread_id, history, turn_messages, answers)
    await conn.execute(
        """
        UPDATE conversations
        SET model_history = $1,
            run_started_at = NULL,
            active_run_id = NULL,
            updated_at = now(),
            title = coalesce(title, $2)
        WHERE id = $3
        """,
        _dump(all_messages if all_messages is not None else [*history, *turn_messages]),
        title,
        thread_id,
    )
    return {"messages": inserted, "ui": ui}


async def recover_crashed_runs(conn: Connection) -> list[dict[str, Any]]:
    """At boot, a thread still marked running crashed; tell the user whether a write landed."""
    rows = await conn.fetch(
        """
        SELECT id, run_started_at FROM conversations
        WHERE run_started_at IS NOT NULL OR active_run_id IS NOT NULL
        """
    )
    recovered: list[dict[str, Any]] = []
    for row in rows:
        started = row["run_started_at"]
        writes = 0
        if started is not None:
            writes = int(
                (
                    await conn.fetchrow_required(
                        "SELECT count(*) AS n FROM undo_log WHERE created_at >= $1", started
                    )
                )["n"]
            )
        note = "The last turn was interrupted by a restart. " + (
            f"{writes} write(s) landed and are in the ledger."
            if writes
            else "No write from that turn landed."
        )
        client_id = await new_id(conn, "msg")
        content = {
            "id": client_id,
            "role": "assistant",
            "parts": [{"type": "data-ending", "data": {"note": note}}],
        }
        await conn.execute(
            """
            INSERT INTO messages (conversation_id, client_id, role, content, body_text)
            VALUES ($1, $2, 'assistant', $3, $4)
            """,
            row["id"],
            client_id,
            content,
            note,
        )
        await conn.execute(
            """
            UPDATE conversations
            SET run_started_at = NULL, active_run_id = NULL, updated_at = now()
            WHERE id = $1
            """,
            row["id"],
        )
        recovered.append({"thread_id": row["id"], "writes": writes})
    return recovered


__all__ = [
    "create_thread",
    "load_history",
    "get_thread",
    "mark_run_started",
    "recover_crashed_runs",
    "record_user_message",
    "save_records",
    "title_for",
]
