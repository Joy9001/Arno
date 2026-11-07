"""Chat, threads, preferences, and flagging.

The chat endpoint builds the adapter, starts a detached turn, and hands back a stream that reads
from the turn's buffer. Reattach and Stop have their own endpoints; one turn runs per thread.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from fin_agent.agent import history as history_mod
from fin_agent.agent.cards import cards_for_transcript, data_part
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.quote import QuoteError, quote_from_messages, resolve_quote, with_quote
from fin_agent.agent.turns import TurnBusy, encode_saved_turn
from fin_agent.api.capture import log_photo
from fin_agent.api.deps import Database, PreparationDep, Session, current_session
from fin_agent.db.conn import connect
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools import read as read_tools
from fin_agent.tools import write as write_tools

router = APIRouter(
    prefix="/api",
    tags=["chat"],
    dependencies=[Depends(current_session)],
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreadRename(ApiModel):
    title: str = Field(min_length=1, max_length=200)


class PreferenceUpdate(ApiModel):
    active: bool


def _approval_submission(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("state") in {"approval-responded", "output-denied"}:
                return True
            approval = part.get("approval")
            if isinstance(approval, dict) and "approved" in approval:
                return True
    return False


def _last_user(messages: list[dict[str, Any]]) -> tuple[str, str] | None:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        identifier = str(message.get("id") or uuid.uuid4())
        text = "\n".join(
            str(part.get("text", ""))
            for part in message.get("parts") or []
            if isinstance(part, dict) and part.get("type") == "text"
        )
        return identifier, text
    return None


def _request_thread_id(body: dict[str, Any]) -> str | None:
    value = body.get("id")
    return str(value) if value else None


def _statement_refs(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """App-owned statement refs from the last user turn, never file URLs."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        refs: list[dict[str, str]] = []
        seen: set[str] = set()
        for part in message.get("parts") or []:
            if not isinstance(part, dict) or part.get("type") != "data-statement":
                continue
            data = part.get("data")
            if not isinstance(data, dict):
                continue
            attachment_id = data.get("attachment_id")
            filename = data.get("filename")
            if not isinstance(attachment_id, str) or not attachment_id:
                continue
            if len(attachment_id) > 100 or attachment_id in seen:
                continue
            seen.add(attachment_id)
            refs.append(
                {
                    "attachment_id": attachment_id,
                    "filename": str(filename or "statement")[:255],
                }
            )
        return refs
    return []


async def _validated_statements(conn: Any, refs: list[dict[str, str]]) -> list[dict[str, str]]:
    if not refs:
        return []
    ids = [ref["attachment_id"] for ref in refs]
    rows = await conn.fetch(
        """
        SELECT id, filename FROM attachments
        WHERE id = ANY($1) AND kind = 'statement' AND data IS NOT NULL
        """,
        ids,
    )
    found = {str(row["id"]): str(row["filename"] or "statement") for row in rows}
    missing = [attachment_id for attachment_id in ids if attachment_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="A statement attachment is no longer available. Attach the file again.",
        )
    return [
        {"attachment_id": attachment_id, "filename": found[attachment_id]} for attachment_id in ids
    ]


def _photo_refs(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """App-owned receipt photo refs from the last user turn, never file URLs."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        refs: list[dict[str, str]] = []
        seen: set[str] = set()
        for part in message.get("parts") or []:
            if not isinstance(part, dict) or part.get("type") != "data-photo":
                continue
            data = part.get("data")
            if not isinstance(data, dict):
                continue
            attachment_id = data.get("attachment_id")
            filename = data.get("filename")
            if not isinstance(attachment_id, str) or not attachment_id:
                continue
            if len(attachment_id) > 100 or attachment_id in seen:
                continue
            seen.add(attachment_id)
            refs.append(
                {
                    "attachment_id": attachment_id,
                    "filename": str(filename or "photo")[:255],
                }
            )
        return refs
    return []


async def _validated_photos(conn: Any, refs: list[dict[str, str]]) -> list[dict[str, str]]:
    if not refs:
        return []
    ids = [ref["attachment_id"] for ref in refs]
    rows = await conn.fetch(
        """
        SELECT id, filename FROM attachments
        WHERE id = ANY($1) AND kind = 'photo' AND data IS NOT NULL AND purged_at IS NULL
        """,
        ids,
    )
    found = {str(row["id"]): str(row["filename"] or "photo") for row in rows}
    missing = [attachment_id for attachment_id in ids if attachment_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="A photo attachment is no longer available. Attach the file again.",
        )
    return [
        {"attachment_id": attachment_id, "filename": found[attachment_id]} for attachment_id in ids
    ]


def _stream_response(iterator: Any, *, thread_id: str | None = None) -> StreamingResponse:
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    if thread_id:
        headers["X-Thread-Id"] = thread_id
        headers["x-resumable-stream-id"] = thread_id
    return StreamingResponse(iterator, media_type="text/event-stream", headers=headers)


@router.post("/chat")
async def post_chat(request: Request, session: Session, preparation: PreparationDep) -> Response:
    # `preparation` is a function-scoped dependency: it holds the thread's preparation guard
    # until this function returns and releases it before the stream is consumed.
    app = request.app
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="The chat body is not an object.")
    messages = body.get("messages") or []
    approval_turn = _approval_submission(messages)
    last = _last_user(messages)
    if last is None and not approval_turn:
        raise HTTPException(status_code=422, detail="A chat request needs a user message.")
    if last is None:
        last = (str(body.get("messageId") or uuid.uuid4()), "")
    message_id, text = last
    thread_id = _request_thread_id(body)
    statement_refs = [] if approval_turn else _statement_refs(messages)
    photo_refs = [] if approval_turn else _photo_refs(messages)
    try:
        quote = None if approval_turn else quote_from_messages(messages)
    except QuoteError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not text.strip():
        if statement_refs and photo_refs:
            text = "Import the attached statement and log the attached receipt or payment photo."
        elif statement_refs:
            text = "Import the attached statement."
        elif photo_refs:
            text = "Log the attached receipt or payment photo."

    async with connect() as conn:
        thread = None
        if thread_id:
            thread = await history_mod.get_thread(conn, thread_id)
            if thread is None:
                # AI SDK always sends a chat id, including for a new chat, so the server creates
                # the row under it rather than asking the client for a second ID.
                if len(thread_id) > 100:
                    raise HTTPException(status_code=422, detail="That chat ID is too long.")
                thread = await history_mod.create_thread(
                    conn, title=history_mod.title_for(text), thread_id=thread_id
                )
        else:
            thread = await history_mod.create_thread(conn, title=history_mod.title_for(text))
            thread_id = str(thread["id"])

        existing = await conn.fetchrow("SELECT * FROM messages WHERE client_id = $1", message_id)
        if existing is not None and not approval_turn:
            # A message ID starts one turn, ever. During it, reattach; after it, replay the
            # saved reply instead of redoing the write.
            turn = app.state.turns.get(str(existing["conversation_id"]))
            if turn is not None and not turn.done.is_set() and turn.message_id == message_id:
                return _stream_response(
                    turn.follow(0),
                    thread_id=str(existing["conversation_id"]),
                )
            saved = await conn.fetchrow(
                """
                SELECT content FROM messages
                WHERE conversation_id = $1 AND role = 'assistant' AND run_id IS NOT NULL
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                str(existing["conversation_id"]),
            )
            if saved is None:
                raise HTTPException(
                    status_code=409,
                    detail="That message ID was already used; send a new message.",
                )
            return StreamingResponse(
                iter(encode_saved_turn(saved["content"])),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Thread-Id": str(existing["conversation_id"]),
                    "x-resumable-stream-id": str(existing["conversation_id"]),
                },
            )

        active_turn = app.state.turns.get(thread_id)
        if active_turn is not None and not active_turn.done.is_set():
            raise HTTPException(
                status_code=409, detail="A reply is already running in this thread."
            )

        try:
            if quote is not None:
                quote = await resolve_quote(conn, thread_id, quote)
        except QuoteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # Validate every ref before any import or capture starts, so a stale file cannot
        # leave a partial turn. The pipeline itself dedupes by file hash, and each photo
        # capture keeps its own idempotency key, so a resend cannot write twice.
        statements = await _validated_statements(conn, statement_refs)
        photos = await _validated_photos(conn, photo_refs)
        initial_cards: list[dict[str, Any]] = []
        statement_attachments = tuple(statement["attachment_id"] for statement in statements)

        for photo in photos:
            row = await conn.fetchrow(
                "SELECT * FROM attachments WHERE id = $1 AND kind = 'photo'",
                photo["attachment_id"],
            )
            if row is None:
                raise HTTPException(
                    status_code=422,
                    detail="A photo attachment is no longer available. Attach the file again.",
                )
            result = await log_photo(conn, thread, dict(row), record_history=False)
            initial_cards.append(result["card"])

        history, outside = await history_mod.ensure_history(conn, thread)
        ttl = int(os.environ.get("CHAT_CACHE_TTL_SECONDS", "300"))
        history, outside_swap = await history_mod.refresh_snapshot(
            conn, thread, history, cache_ttl_seconds=ttl
        )
        if approval_turn:
            user_row = await conn.fetchrow(
                "SELECT id, body_text FROM messages WHERE conversation_id = $1 "
                "AND role = 'user' ORDER BY created_at DESC, id DESC LIMIT 1",
                thread_id,
            )
            user_server_id = str(user_row["id"]) if user_row else None
            text = str(user_row["body_text"] or "") if user_row else ""
        else:
            await history_mod.record_user_message(
                conn,
                thread_id=thread_id,
                client_id=message_id,
                text=text,
                statements=statements,
                photos=photos,
                quote=quote,
            )
            user_row = await conn.fetchrow(
                "SELECT id FROM messages WHERE client_id = $1", message_id
            )
            user_server_id = str(user_row["id"]) if user_row else None
        title = thread.get("title")
        run_id = f"run_{uuid.uuid4().hex}"
        await history_mod.mark_run_started(conn, thread_id, run_id)
        capture_ref = None
        capture_kind = "chat"
        voice_attachment_id = body.get("voice_attachment_id")
        if isinstance(voice_attachment_id, str) and voice_attachment_id:
            voice_row = await conn.fetchrow(
                """
                SELECT id FROM attachments
                WHERE id = $1 AND kind = 'audio' AND data IS NOT NULL
                """,
                voice_attachment_id,
            )
            if voice_row is not None:
                capture_ref = str(voice_row["id"])
                capture_kind = "voice"
        if capture_ref is None and photos:
            capture_ref = photos[0]["attachment_id"]
            capture_kind = "photo"
        flagged = thread.get("outside_text_at") is not None or outside or outside_swap

    from pydantic_ai.ui.vercel_ai import VercelAIAdapter

    deps = AgentDeps(
        conversation_id=thread_id,
        message_id=user_server_id,
        user_id=str(session["user_id"]),
        flagged=flagged,
        user_texts=(text,),
        capture_kind=capture_kind,
        capture_ref=capture_ref,
        statement_attachments=statement_attachments,
    )
    adapter = await VercelAIAdapter.from_request(
        request,
        agent=app.state.agent,
        sdk_version=7,
        manage_message_history="server",
        allowed_file_url_schemes=frozenset(),
    )
    if quote is not None:
        # The quote is context on this turn only; the client's own message stays untouched.
        adapter = replace(adapter, run_input=with_quote(adapter.run_input, quote))
    # The streamed reply carries the id it is saved under, so the browser can quote it at once.
    reply_id = f"msg_{uuid.uuid4().hex}"
    adapter = replace(adapter, server_message_id=reply_id)
    try:
        turn = await app.state.turns.start(
            adapter=adapter,
            thread_id=thread_id,
            message_id=message_id,
            history=history,
            deps=deps,
            title=title,
            run_id=run_id,
            initial_cards=initial_cards,
            reply_id=reply_id,
        )
    except TurnBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _stream_response(turn.follow(0), thread_id=thread_id)


@router.get("/chat/{thread_id}/stream")
async def get_chat_stream(request: Request, thread_id: str) -> Response:
    turn = request.app.state.turns.get(thread_id)
    if turn is None or turn.done.is_set():
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _stream_response(turn.follow(0), thread_id=thread_id)


@router.post("/chat/{thread_id}/stop")
async def post_chat_stop(request: Request, thread_id: str) -> dict[str, Any]:
    stopped = request.app.state.turns.stop(thread_id)
    return {
        "stopped": stopped,
        "thread_id": thread_id,
    }


@router.get("/threads")
async def list_threads(
    conn: Database,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    if q:
        rows = await conn.fetch(
            """
            SELECT c.id, c.title, c.updated_at, c.outside_text_at, c.run_started_at,
                   max(m.created_at) AS last_message_at,
                   count(m.id) AS message_count,
                   max(ts_headline('simple', coalesce(m.body_text, ''),
                                   websearch_to_tsquery('simple', $1))) AS snippet
            FROM conversations c
            JOIN messages m ON m.conversation_id = c.id
            WHERE m.search @@ websearch_to_tsquery('simple', $1)
            GROUP BY c.id
            ORDER BY max(m.created_at) DESC
            LIMIT $2
            """,
            q,
            limit,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT c.id, c.title, c.updated_at, c.outside_text_at, c.run_started_at,
                   max(m.created_at) AS last_message_at,
                   count(m.id) AS message_count,
                   NULL::text AS snippet
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY coalesce(max(m.created_at), c.updated_at) DESC
            LIMIT $1
            """,
            limit,
        )
    return {
        "threads": [
            {
                "id": row["id"],
                "title": row["title"] or "New chat",
                "updated_at": (row["last_message_at"] or row["updated_at"]).isoformat(),
                "message_count": int(row["message_count"] or 0),
                "snippet": row["snippet"],
                "running": row["run_started_at"] is not None,
                "flagged": row["outside_text_at"] is not None,
            }
            for row in rows
        ],
        "as_of": ledger_today().isoformat(),
    }


def _merge_saved_state(
    messages: list[dict[str, Any]], thread: dict[str, Any]
) -> list[dict[str, Any]]:
    """Merge cards and tool outcomes from `model_history` back into the transcript.

    `dump_messages` drops a tool card from an earlier turn, and a resumed approval's tool return
    may live without its original call. The stored model history still has both, so the thread
    view closes the card and restores the card that the live stream showed.
    """
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ToolReturnPart,
    )

    history = thread.get("model_history")
    if not history:
        return messages
    try:
        model_messages = ModelMessagesTypeAdapter.validate_python(history)
    except Exception:  # noqa: BLE001 - a bad row must not make the thread unreadable
        return messages

    returns: dict[str, ToolReturnPart] = {}
    for message in model_messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                returns[part.tool_call_id] = part

    for message in messages:
        for part in message.get("parts", []):
            if not isinstance(part, dict):
                continue
            tool_call_id = part.get("toolCallId") or part.get("tool_call_id")
            if not isinstance(tool_call_id, str) or tool_call_id not in returns:
                continue
            if part.get("state") == "approval-requested":
                ret = returns[tool_call_id]
                part["state"] = "output-available"
                part["output"] = ret.content
                part["toolCallId"] = tool_call_id
                approval = part.get("approval")
                if isinstance(approval, dict):
                    approval["approved"] = True
                part["callProviderMetadata"] = {
                    **(
                        part.get("callProviderMetadata") or part.get("call_provider_metadata") or {}
                    ),
                    "pydantic_ai": {"outcome": "approved"},
                }

    cards = cards_for_transcript(model_messages)
    if cards:
        last_assistant = next(
            (message for message in reversed(messages) if message.get("role") == "assistant"),
            None,
        )
        if last_assistant is not None:
            existing = {
                json.dumps(part.get("data"), sort_keys=True, default=str)
                for part in last_assistant.get("parts", [])
                if isinstance(part, dict) and part.get("type") == "data-card"
            }
            parts = last_assistant.setdefault("parts", [])
            for card in cards:
                key = json.dumps(card.data, sort_keys=True, default=str)
                if key not in existing:
                    parts.append(data_part(card))
                    existing.add(key)
    return messages


@router.get("/threads/{thread_id}/messages")
async def get_thread_messages(request: Request, conn: Database, thread_id: str) -> dict[str, Any]:
    thread = await history_mod.get_thread(conn, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="That thread does not exist.")
    rows = await conn.fetch(
        """
        SELECT id, client_id, role, content, created_at, trace_id, flagged_at
        FROM messages WHERE conversation_id = $1 ORDER BY created_at, id
        """,
        thread_id,
    )
    messages = []
    for row in rows:
        content = row["content"] or {}
        content = {
            **content,
            # The assistant's UI id is the server row id, so Flag needs no second lookup.
            "id": row["id"] if row["role"] == "assistant" else content.get("id", row["client_id"]),
            "server_id": row["id"],
            "metadata": {
                **(content.get("metadata") or {}),
                "server_id": row["id"],
                "flagged_at": row["flagged_at"].isoformat() if row["flagged_at"] else None,
            },
        }
        messages.append(content)
    turn = request.app.state.turns.get(thread_id)
    if turn is not None and not turn.done.is_set():
        # Fix 1 from step 10: the running turn's message is already stored at the start, so it is
        # in `messages`; mark the thread as running so the client can resume the stream.
        pass
    messages = _merge_saved_state(messages, thread)
    return {
        "thread": {
            "id": thread["id"],
            "title": thread["title"] or "New chat",
            "flagged": thread["outside_text_at"] is not None,
            "snapshot_at": thread["snapshot_at"].isoformat() if thread["snapshot_at"] else None,
        },
        "messages": messages,
        "running": bool(turn is not None and not turn.done.is_set()),
        "as_of": ledger_today().isoformat(),
    }


@router.patch("/threads/{thread_id}")
async def rename_thread(conn: Database, thread_id: str, body: ThreadRename) -> dict[str, Any]:
    row = await conn.fetchrow(
        "UPDATE conversations SET title = $1, updated_at = now() WHERE id = $2 RETURNING id, title",
        body.title.strip(),
        thread_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="That thread does not exist.")
    return {"id": row["id"], "title": row["title"]}


@router.post("/messages/{message_id}/flag")
async def flag_message(conn: Database, message_id: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        "UPDATE messages SET flagged_at = now() WHERE id = $1 RETURNING id, trace_id, flagged_at",
        message_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="That message does not exist.")
    return {
        "id": row["id"],
        "trace_id": row["trace_id"],
        "flagged_at": row["flagged_at"].isoformat(),
    }


@router.get("/preferences")
async def get_preferences(conn: Database) -> dict[str, Any]:
    return {
        "preferences": await read_tools.active_preferences(conn),
        "as_of": ledger_today().isoformat(),
    }


@router.patch("/preferences/{preference_id}")
async def patch_preference(
    conn: Database, preference_id: str, body: PreferenceUpdate
) -> dict[str, Any]:
    row = await conn.fetchrow(
        "SELECT * FROM preferences WHERE id = $1 AND deleted_at IS NULL", preference_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="That preference does not exist.")
    async with conn.transaction():
        after = await conn.fetchrow(
            "UPDATE preferences SET active = $1, updated_at = now() WHERE id = $2 RETURNING *",
            body.active,
            preference_id,
        )
        await conn.execute(
            """
            INSERT INTO audit_log (actor, group_id, table_name, row_id, before, after)
            VALUES ('user', pgledger_generate_id('und'), 'preferences', $1, $2, $3)
            """,
            preference_id,
            json.dumps(row, default=str),
            json.dumps(after, default=str),
        )
    return dict(after)


@router.delete("/preferences/{preference_id}")
async def delete_preference(conn: Database, preference_id: str) -> dict[str, Any]:
    try:
        return await write_tools.delete_preference_for_good(conn, preference_id)
    except write_tools.ToolInputError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
