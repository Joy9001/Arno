"""Threads, the pinned snapshot, and the two records saved on every ending.

`messages` is what you saw and never changes. `conversations.model_history` is what the model gets
and may be edited by compaction. Both are written in one transaction on a finish, a stop, and a
failure; `run_started_at` catches the one ending nobody can save (a crash).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fin_agent.agent.cards import cards_from_messages
from fin_agent.agent.redaction import redact_text
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools import read as read_tools

SNAPSHOT_REPLY = "Snapshot read."


def _dump(messages: list[Any]) -> list[dict[str, Any]]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return json.loads(ModelMessagesTypeAdapter.dump_json(messages))


def _load(data: Any) -> list[Any]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    if not data:
        return []
    return ModelMessagesTypeAdapter.validate_python(data)


def create_thread(
    conn: psycopg.Connection, *, title: str | None = None, thread_id: str | None = None
) -> dict[str, Any]:
    if thread_id:
        row = conn.execute(
            "INSERT INTO conversations (id, title) VALUES (%s, %s) RETURNING *",
            (thread_id, title),
        ).fetchone()
    else:
        row = conn.execute(
            "INSERT INTO conversations (title) VALUES (%s) RETURNING *", (title,)
        ).fetchone()
    return row


def get_thread(conn: psycopg.Connection, thread_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM conversations WHERE id = %s", (thread_id,)).fetchone()


def title_for(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:80] or "New chat"


def snapshot_payload(conn: psycopg.Connection) -> tuple[str, bool]:
    payload = read_tools.get_financial_context(conn)
    outside = bool(payload.pop("outside_text", False))
    return json.dumps(payload, ensure_ascii=False, default=str), outside


def _pin(content: str) -> Any:
    from pydantic_ai_harness.compaction import pin

    return pin(content)


def build_snapshot_history(conn: psycopg.Connection) -> tuple[list[Any], bool]:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart

    content, outside = snapshot_payload(conn)
    history = [
        ModelRequest(parts=[_pin(content)]),
        ModelResponse(parts=[TextPart(SNAPSHOT_REPLY)]),
    ]
    return history, outside


def ensure_history(conn: psycopg.Connection, thread: dict[str, Any]) -> tuple[list[Any], bool]:
    """Load the thread's history, creating the pinned snapshot exchange when it has none."""
    history = _load(thread.get("model_history"))
    outside = False
    if not history:
        history, outside = build_snapshot_history(conn)
        row = conn.execute(
            """
            UPDATE conversations
            SET model_history = %s, snapshot_at = now(), updated_at = now(),
                outside_text_at = CASE WHEN %s THEN coalesce(outside_text_at, now())
                                       ELSE outside_text_at END
            WHERE id = %s RETURNING *
            """,
            (Jsonb(_dump(history)), outside, thread["id"]),
        ).fetchone()
        thread.update(row)
    return history, outside


def refresh_snapshot(
    conn: psycopg.Connection,
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
    changed = conn.execute(
        "SELECT 1 FROM audit_log WHERE created_at > %s LIMIT 1", (snapshot_at,)
    ).fetchone()
    if changed is None:
        return history, False

    content, outside = snapshot_payload(conn)
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
    row = conn.execute(
        """
        UPDATE conversations
        SET model_history = %s, snapshot_at = now(), updated_at = now(),
            outside_text_at = CASE WHEN %s THEN coalesce(outside_text_at, now())
                                   ELSE outside_text_at END
        WHERE id = %s RETURNING *
        """,
        (Jsonb(_dump(history)), outside, thread["id"]),
    ).fetchone()
    thread.update(row)
    return history, outside


def _is_snapshot_reply(message: Any) -> bool:
    from pydantic_ai.messages import TextPart

    return (
        len(message.parts) == 1
        and isinstance(message.parts[0], TextPart)
        and message.parts[0].content == SNAPSHOT_REPLY
    )


def append_outside_entry(
    conn: psycopg.Connection,
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

    history, _ = ensure_history(conn, thread)
    history = [
        *history,
        ModelRequest(parts=[UserPromptPart(content=marked_text)]),
        ModelResponse(parts=[TextPart(reply)]),
    ]
    row = conn.execute(
        """
        UPDATE conversations
        SET model_history = %s,
            outside_text_at = coalesce(outside_text_at, now()),
            updated_at = now()
        WHERE id = %s RETURNING *
        """,
        (Jsonb(_dump(history)), thread["id"]),
    ).fetchone()
    thread.update(row)
    return history


def record_user_message(
    conn: psycopg.Connection,
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
    row = conn.execute(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES (%s, %s, 'user', %s, %s)
        ON CONFLICT (client_id) DO NOTHING
        RETURNING *
        """,
        (thread_id, client_id, Jsonb(content), safe),
    ).fetchone()
    return (
        row or conn.execute("SELECT * FROM messages WHERE client_id = %s", (client_id,)).fetchone()
    )


def mark_run_started(conn: psycopg.Connection, thread_id: str, run_id: str) -> None:
    conn.execute(
        """
        UPDATE conversations
        SET run_started_at = now(), active_run_id = %s, updated_at = now()
        WHERE id = %s
        """,
        (run_id, thread_id),
    )


def _body_text(message: dict[str, Any]) -> str:
    parts = message.get("parts") or []
    return "\n".join(
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


def save_records(
    conn: psycopg.Connection,
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
) -> dict[str, Any]:
    """Write both records in one transaction and clear the running marker."""
    from fin_agent.agent.cards import attach_to_last_assistant, ui_messages, with_data

    history_json_messages = all_messages if all_messages is not None else [*history, *turn_messages]
    ui = ui_messages(turn_messages)

    stored = conn.execute(
        "SELECT content, body_text FROM messages WHERE client_id = %s AND conversation_id = %s",
        (ui_message_id, thread_id),
    ).fetchone()
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

    extra = cards_from_messages(turn_messages)
    ui = attach_to_last_assistant(ui, extra)
    last_assistant = next(
        (message for message in reversed(ui) if message.get("role") == "assistant"), None
    )
    if last_assistant is None and (save_note or extra_cards or ending in {"failed", "cancelled"}):
        last_assistant = {
            "id": new_id(conn, "msg"),
            "role": "assistant",
            "parts": [],
        }
        ui.append(last_assistant)
    if last_assistant is not None:
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

    inserted: list[str] = []
    for message in ui:
        client_id = str(message.get("id") or "")
        if not client_id:
            client_id = new_id(conn, "msg")
            message["id"] = client_id
        role = str(message.get("role") or "assistant")
        body = _body_text(message)
        if role == "user":
            conn.execute(
                """
                UPDATE messages SET content = %s, body_text = %s
                WHERE client_id = %s AND conversation_id = %s
                """,
                (Jsonb(message), body, client_id, thread_id),
            )
            continue
        row = conn.execute(
            """
            INSERT INTO messages
                (conversation_id, client_id, role, content, body_text, run_id, trace_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_id) DO NOTHING
            RETURNING id
            """,
            (
                thread_id,
                client_id,
                role,
                Jsonb(message),
                body,
                run_id,
                trace_id if message is last_assistant else None,
            ),
        ).fetchone()
        if row:
            inserted.append(str(row["id"]))

    history_json = Jsonb(_dump(history_json_messages))
    conn.execute(
        """
        UPDATE conversations
        SET model_history = %s,
            run_started_at = NULL,
            active_run_id = NULL,
            updated_at = now(),
            title = coalesce(title, %s)
        WHERE id = %s
        """,
        (history_json, title, thread_id),
    )
    return {"messages": inserted, "ui": ui}


def recover_crashed_runs(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """At boot, a thread still marked running crashed; tell the user whether a write landed."""
    rows = list(
        conn.execute(
            """
            SELECT id, run_started_at FROM conversations
            WHERE run_started_at IS NOT NULL OR active_run_id IS NOT NULL
            """
        ).fetchall()
    )
    recovered: list[dict[str, Any]] = []
    for row in rows:
        started = row["run_started_at"]
        writes = 0
        if started is not None:
            writes = int(
                conn.execute(
                    "SELECT count(*) AS n FROM audit_log WHERE created_at >= %s", (started,)
                ).fetchone()["n"]
            )
        note = "The last turn was interrupted by a restart. " + (
            f"{writes} write(s) landed and are in the ledger."
            if writes
            else "No write from that turn landed."
        )
        client_id = new_id(conn, "msg")
        content = {
            "id": client_id,
            "role": "assistant",
            "parts": [{"type": "data-ending", "data": {"note": note}}],
        }
        conn.execute(
            """
            INSERT INTO messages (conversation_id, client_id, role, content, body_text)
            VALUES (%s, %s, 'assistant', %s, %s)
            """,
            (row["id"], client_id, Jsonb(content), note),
        )
        conn.execute(
            """
            UPDATE conversations
            SET run_started_at = NULL, active_run_id = NULL, updated_at = now()
            WHERE id = %s
            """,
            (row["id"],),
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
