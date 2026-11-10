"""Threads, the pinned snapshot, and the two records saved on every ending.

`messages` is what you saw and never changes. `conversations.model_history` is what the model gets
and may be edited by compaction. Both are written in one transaction on a finish, a stop, and a
failure; `run_started_at` catches the one ending nobody can save (a crash).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from fin_agent.agent.cards import cards_from_messages, run_timing
from fin_agent.agent.presentation import keep_last_answer
from fin_agent.agent.redaction import redact_text
from fin_agent.db.conn import Connection
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools import read as read_tools

SNAPSHOT_REPLY = "Snapshot read."

RUN_STATUS = {"complete": "complete", "cancelled": "cancelled", "failed": "error"}


def _dump(messages: list[Any]) -> list[dict[str, Any]]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return json.loads(ModelMessagesTypeAdapter.dump_json(messages))


def _load(data: Any) -> list[Any]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    if not data:
        return []
    return ModelMessagesTypeAdapter.validate_python(data)


async def create_thread(
    conn: Connection, *, title: str | None = None, thread_id: str | None = None
) -> dict[str, Any]:
    if thread_id:
        row = await conn.fetchrow(
            "INSERT INTO conversations (id, title) VALUES ($1, $2) RETURNING *",
            thread_id,
            title,
        )
    else:
        row = await conn.fetchrow(
            "INSERT INTO conversations (title) VALUES ($1) RETURNING *", title
        )
    return row


async def get_thread(conn: Connection, thread_id: str) -> dict[str, Any] | None:
    return await conn.fetchrow("SELECT * FROM conversations WHERE id = $1", thread_id)


def title_for(text: str) -> str:
    # A title is stored and listed, so a pasted card number must not reach it.
    cleaned = " ".join(redact_text(text).split())
    return cleaned[:80] or "New chat"


async def snapshot_payload(conn: Connection) -> tuple[str, bool]:
    payload = await read_tools.get_financial_context(conn)
    outside = bool(payload.pop("outside_text", False))
    return json.dumps(payload, ensure_ascii=False, default=str), outside


def _pin(content: str) -> Any:
    from pydantic_ai_harness.compaction import pin

    return pin(content)


async def build_snapshot_history(conn: Connection) -> tuple[list[Any], bool]:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart

    content, outside = await snapshot_payload(conn)
    history = [
        ModelRequest(parts=[_pin(content)]),
        ModelResponse(parts=[TextPart(SNAPSHOT_REPLY)]),
    ]
    return history, outside


async def ensure_history(conn: Connection, thread: dict[str, Any]) -> tuple[list[Any], bool]:
    """Load the thread's history, creating the pinned snapshot exchange when it has none."""
    history = _load(thread.get("model_history"))
    outside = False
    if not history:
        history, outside = await build_snapshot_history(conn)
        row = await conn.fetchrow(
            """
            UPDATE conversations
            SET model_history = $1, snapshot_at = now(), updated_at = now(),
                outside_text_at = CASE WHEN $2 THEN coalesce(outside_text_at, now())
                                       ELSE outside_text_at END
            WHERE id = $3 RETURNING *
            """,
            _dump(history),
            outside,
            thread["id"],
        )
        thread.update(row)
    return history, outside


async def refresh_snapshot(
    conn: Connection,
    thread: dict[str, Any],
    history: list[Any],
    *,
    cache_ttl_seconds: int = 300,
) -> tuple[list[Any], bool]:
    """Swap in a fresh snapshot when the cache has expired and something has changed.

    The history has to be cached again anyway, so the swap costs nothing extra. The rest of the
    thread stays as it was, and no second pin piles up.
    """
    from pydantic_ai.messages import ModelRequest
    from pydantic_ai_harness.compaction import is_pinned

    snapshot_at = thread.get("snapshot_at")
    if snapshot_at is None:
        return history, False
    age = (datetime.now(UTC) - snapshot_at).total_seconds()
    if age < cache_ttl_seconds:
        return history, False
    changed = await conn.fetchrow(
        "SELECT 1 FROM audit_log WHERE created_at > $1 LIMIT 1", snapshot_at
    )
    if changed is None:
        return history, False

    content, outside = await snapshot_payload(conn)
    from pydantic_ai.messages import ModelResponse, TextPart

    replacement = [
        ModelRequest(parts=[_pin(content)]),
        ModelResponse(parts=[TextPart(SNAPSHOT_REPLY)]),
    ]
    rest = list(history)
    if rest and isinstance(rest[0], ModelRequest) and any(is_pinned(p) for p in rest[0].parts):
        rest = rest[1:]
        if rest and isinstance(rest[0], ModelResponse) and _is_snapshot_reply(rest[0]):
            rest = rest[1:]
    history = replacement + rest
    row = await conn.fetchrow(
        """
        UPDATE conversations
        SET model_history = $1, snapshot_at = now(), updated_at = now(),
            outside_text_at = CASE WHEN $2 THEN coalesce(outside_text_at, now())
                                   ELSE outside_text_at END
        WHERE id = $3 RETURNING *
        """,
        _dump(history),
        outside,
        thread["id"],
    )
    thread.update(row)
    return history, outside


def _is_snapshot_reply(message: Any) -> bool:
    from pydantic_ai.messages import TextPart

    return (
        len(message.parts) == 1
        and isinstance(message.parts[0], TextPart)
        and message.parts[0].content == SNAPSHOT_REPLY
    )


async def append_outside_entry(
    conn: Connection,
    thread: dict[str, Any],
    marked_text: str,
    *,
    reply: str = "Photo logged.",
) -> list[Any]:
    """Append a marked photo extraction to the model's history and set the flag.

    This is server code because the client must not be able to skip either step. The entry is a
    user-shaped exchange with the fields already wrapped by `mark()`, so the next turn sees them
    as data. `outside_text_at` is set in the same statement, never cleared.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    history, _ = await ensure_history(conn, thread)
    history = [
        *history,
        ModelRequest(parts=[UserPromptPart(content=marked_text)]),
        ModelResponse(parts=[TextPart(reply)]),
    ]
    row = await conn.fetchrow(
        """
        UPDATE conversations
        SET model_history = $1,
            outside_text_at = coalesce(outside_text_at, now()),
            updated_at = now()
        WHERE id = $2 RETURNING *
        """,
        _dump(history),
        thread["id"],
    )
    thread.update(row)
    return history


async def record_user_message(
    conn: Connection,
    *,
    thread_id: str,
    client_id: str,
    text: str,
    statements: list[dict[str, str]] | None = None,
    photos: list[dict[str, str]] | None = None,
    quote: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Store the user's message at the start, so a reload mid-reply keeps it."""
    safe = redact_text(text)
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
    content = {
        "id": client_id,
        "role": "user",
        "parts": parts,
    }
    if quote:
        content["metadata"] = {
            "custom": {
                "quote": {"text": quote["text"], "messageId": quote["messageId"]},
            }
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
    return row or await conn.fetchrow("SELECT * FROM messages WHERE client_id = $1", client_id)


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
    parts = message.get("parts") or []
    texts = [
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    # A blank line between two text parts: one newline renders them as one smashed sentence.
    return join_text(texts)


def join_text(parts: list[str]) -> str:
    """Two text parts in a row are two paragraphs, never one smashed sentence ("done.Next")."""
    return "\n\n".join(part.strip() for part in parts if part.strip())


def merge_text_parts(parts: list[Any]) -> list[Any]:
    """Collapse adjacent text parts of one message into one, separated by a blank line."""
    merged: list[Any] = []
    for part in parts:
        previous = merged[-1] if merged else None
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(previous, dict)
            and previous.get("type") == "text"
        ):
            merged[-1] = {
                **previous,
                "text": join_text([str(previous.get("text", "")), str(part.get("text", ""))]),
            }
            continue
        merged.append(part)
    return merged


def paragraphs(message: Any) -> Any:
    """One text part per response, its paragraphs a blank line apart.

    `dump_messages` joins consecutive text parts of a response with nothing between them, so
    "…done." and "Next…" used to reach the transcript as "…done.Next…".
    """
    from pydantic_ai.messages import ModelResponse, TextPart

    if not isinstance(message, ModelResponse):
        return message
    parts: list[Any] = []
    for part in message.parts:
        previous = parts[-1] if parts else None
        if isinstance(part, TextPart) and isinstance(previous, TextPart):
            parts[-1] = replace(previous, content=join_text([previous.content, part.content]))
            continue
        parts.append(part)
    if len(parts) == len(message.parts):
        return message
    return replace(message, parts=parts)


def _retried_calls(messages: list[Any]) -> set[str]:
    """The tool calls the model had to redo, so the attempt that made them was abandoned."""
    from pydantic_ai.messages import ModelRequest, RetryPromptPart, ToolReturnPart

    retried: set[str] = set()
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.outcome == "retried":
                retried.add(str(part.tool_call_id))
            elif isinstance(part, RetryPromptPart) and part.tool_call_id:
                # A transcript saved before retries travelled as tool returns.
                retried.add(str(part.tool_call_id))
    return retried


def without_retried_text(messages: list[Any]) -> list[Any]:
    """The transcript's words are the answer, not the attempt it abandoned.

    A response whose tool call came back as a retry was a draft: its text said what the model was
    about to do with data it never got. Only the response that answers survives, so a retried
    answer cannot read as two versions of one reply.
    """
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

    retried = _retried_calls(messages)
    if not retried:
        return messages
    kept: list[Any] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            kept.append(message)
            continue
        if not any(
            isinstance(part, ToolCallPart) and str(part.tool_call_id) in retried
            for part in message.parts
        ):
            kept.append(message)
            continue
        parts = [part for part in message.parts if not isinstance(part, TextPart)]
        kept.append(replace(message, parts=parts) if parts else message)
    return kept


async def save_records(
    conn: Connection,
    *,
    thread_id: str,
    run_id: str,
    ui_message_id: str,
    history: list[Any],
    turn_messages: list[Any],
    ending: str,
    trace_id: str | None = None,
    number_flags: list[str] | None = None,
    model_badge: str | None = None,
    reply_model: str | None = None,
    reply_fallback: bool = False,
    save_note: str | None = None,
    title: str | None = None,
    all_messages: list[Any] | None = None,
    extra_cards: list[dict[str, Any]] | None = None,
    reply_id: str | None = None,
) -> dict[str, Any]:
    """Write both records in one transaction and clear the running marker.

    `reply_id` is the id the stream already gave the browser; the reply is saved under it.
    """
    from fin_agent.agent.cards import attach_to_last_assistant, ui_messages, with_data

    history_json_messages = all_messages if all_messages is not None else [*history, *turn_messages]
    ui = ui_messages([paragraphs(message) for message in without_retried_text(turn_messages)])
    for message in ui:
        if message.get("role") == "assistant":
            # The transcript renders each text part on its own; two in a row must be separated.
            message["parts"] = merge_text_parts(list(message.get("parts") or []))
    run_started_at = await conn.fetchval(
        "SELECT run_started_at FROM conversations WHERE id = $1", thread_id
    )

    stored = await conn.fetchrow(
        "SELECT content, body_text FROM messages WHERE client_id = $1 AND conversation_id = $2",
        ui_message_id,
        thread_id,
    )
    stored_content = (stored["content"] if stored is not None else None) or {}
    attachments = [
        part
        for part in stored_content.get("parts", [])
        if isinstance(part, dict) and part.get("type") in {"data-statement", "data-photo"}
    ]
    stored_metadata = stored_content.get("metadata")

    def apply_user(message: dict[str, Any]) -> None:
        # The model sees the quote block; the transcript keeps the question the user typed.
        text = str(stored["body_text"] or "") if stored is not None else _body_text(message)
        safe = redact_text(text)
        message["parts"] = ([{"type": "text", "text": safe}] if safe else []) + attachments
        if stored_metadata:
            message["metadata"] = {**(message.get("metadata") or {}), **stored_metadata}

    for message in ui:
        if message.get("role") == "user" and message.get("id") == ui_message_id:
            apply_user(message)
            break
    else:
        for message in ui:
            if message.get("role") == "user":
                message["id"] = ui_message_id
                apply_user(message)
                break

    extra = keep_last_answer(cards_from_messages(turn_messages))
    ui = attach_to_last_assistant(ui, extra)
    last_assistant = next(
        (message for message in reversed(ui) if message.get("role") == "assistant"), None
    )
    if last_assistant is None and (save_note or extra_cards or ending in {"failed", "cancelled"}):
        last_assistant = {
            "id": await new_id(conn, "msg"),
            "role": "assistant",
            "parts": [],
        }
        ui.append(last_assistant)
    if last_assistant is not None:
        if reply_id:
            last_assistant["id"] = reply_id
        if extra_cards:
            existing = {
                json.dumps(part.get("data"), sort_keys=True, default=str)
                for part in last_assistant.get("parts", [])
                if isinstance(part, dict) and part.get("type") == "data-card"
            }
            existing_imports = {
                str(part.get("data", {}).get("import_id"))
                for part in last_assistant.get("parts", [])
                if isinstance(part, dict)
                and part.get("type") == "data-card"
                and isinstance(part.get("data"), dict)
                and part.get("data", {}).get("card") == "import"
            }
            for card in extra_cards:
                key = json.dumps(card, sort_keys=True, default=str)
                import_id = str(card.get("import_id")) if card.get("card") == "import" else ""
                if key in existing or (import_id and import_id in existing_imports):
                    continue
                with_data(last_assistant, "data-card", card)
                existing.add(key)
                if import_id:
                    existing_imports.add(import_id)

        if number_flags:
            with_data(last_assistant, "data-number-flags", {"amounts": number_flags})
        if model_badge:
            with_data(last_assistant, "data-model-badge", {"model": model_badge})
        if reply_model:
            with_data(
                last_assistant,
                "data-reply-model",
                {"model": reply_model, "fallback": reply_fallback},
            )
        if save_note:
            with_data(last_assistant, "data-ending", {"note": save_note})
        if ending == "cancelled":
            with_data(last_assistant, "data-ending", {"note": "Stopped."})
        if trace_id:
            last_assistant["trace_id"] = trace_id

    if last_assistant is not None:
        timing = run_timing(
            turn_messages,
            started_at=run_started_at,
            # A finished run ends at its last response; a stopped or failed one ends now.
            finished_at=None if ending == "complete" else datetime.now(UTC),
        )
        metadata = last_assistant.setdefault("metadata", {})
        if timing is not None:
            timing["toolCallCount"] = sum(
                1
                for part in last_assistant.get("parts", [])
                if isinstance(part, dict) and str(part.get("type", "")).startswith("tool-")
            )
            metadata["timing"] = {**timing, "totalChunks": 0}
        metadata.setdefault("custom", {})["runStatus"] = RUN_STATUS.get(ending, "incomplete")

    inserted: list[str] = []
    for message in ui:
        client_id = str(message.get("id") or "")
        if not client_id:
            client_id = await new_id(conn, "msg")
            message["id"] = client_id
        role = str(message.get("role") or "assistant")
        body = _body_text(message)
        if role == "user":
            await conn.execute(
                """
                UPDATE messages SET content = $1, body_text = $2
                WHERE client_id = $3 AND conversation_id = $4
                """,
                message,
                body,
                client_id,
                thread_id,
            )
            continue
        row = await conn.fetchrow(
            """
            INSERT INTO messages
                (conversation_id, client_id, role, content, body_text, run_id, trace_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (client_id) DO NOTHING
            RETURNING id
            """,
            thread_id,
            client_id,
            role,
            message,
            body,
            run_id,
            trace_id if message is last_assistant else None,
        )
        if row:
            inserted.append(str(row["id"]))

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
        _dump(history_json_messages),
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
                    await conn.fetchrow(
                        "SELECT count(*) AS n FROM audit_log WHERE created_at >= $1", started
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
    "append_outside_entry",
    "build_snapshot_history",
    "create_thread",
    "ensure_history",
    "get_thread",
    "mark_run_started",
    "recover_crashed_runs",
    "record_user_message",
    "refresh_snapshot",
    "save_records",
    "snapshot_payload",
    "title_for",
    "ledger_today",
]
