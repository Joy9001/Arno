"""Chat, threads, preferences, and flagging.

The chat endpoint builds the adapter, starts a detached turn, and hands back a stream that reads
from the turn's buffer. Reattach and Stop have their own endpoints; one turn runs per thread.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import closing
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from fin_agent.agent import history as history_mod
from fin_agent.agent.cards import cards_for_transcript, data_part
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.turns import TurnBusy, encode_saved_turn
from fin_agent.api.deps import Database, current_session
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


def _stream_response(iterator: Any, *, thread_id: str | None = None) -> StreamingResponse:
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    if thread_id:
        headers["X-Thread-Id"] = thread_id
    return StreamingResponse(iterator, media_type="text/event-stream", headers=headers)


@router.post("/chat")
async def post_chat(request: Request) -> Response:
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

    with closing(connect()) as conn:
        thread = None
        if thread_id:
            thread = history_mod.get_thread(conn, thread_id)
            if thread is None:
                # AI SDK always sends a chat id, including for a new chat, so the server creates
                # the row under it rather than asking the client for a second ID.
                if len(thread_id) > 100:
                    raise HTTPException(status_code=422, detail="That chat ID is too long.")
                thread = history_mod.create_thread(
                    conn, title=history_mod.title_for(text), thread_id=thread_id
                )
                conn.commit()
        else:
            thread = history_mod.create_thread(conn, title=history_mod.title_for(text))
            thread_id = str(thread["id"])
            conn.commit()

        existing = conn.execute(
            "SELECT * FROM messages WHERE client_id = %s", (message_id,)
        ).fetchone()
        if existing is not None and not approval_turn:
            # A message ID starts one turn, ever. During it, reattach; after it, replay the
            # saved reply instead of redoing the write.
            turn = app.state.turns.get(str(existing["conversation_id"]))
            if turn is not None and not turn.done.is_set() and turn.message_id == message_id:
                return _stream_response(turn.follow(0), thread_id=str(existing["conversation_id"]))
            saved = conn.execute(
                """
                SELECT content FROM messages
                WHERE conversation_id = %s AND role = 'assistant' AND run_id IS NOT NULL
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (str(existing["conversation_id"]),),
            ).fetchone()
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
                },
            )

        history, outside = history_mod.ensure_history(conn, thread)
        ttl = int(os.environ.get("CHAT_CACHE_TTL_SECONDS", "300"))
        history, outside_swap = history_mod.refresh_snapshot(
            conn, thread, history, cache_ttl_seconds=ttl
        )
        if approval_turn:
            user_row = conn.execute(
                "SELECT id, body_text FROM messages WHERE conversation_id = %s "
                "AND role = 'user' ORDER BY created_at DESC, id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            user_server_id = str(user_row["id"]) if user_row else None
            text = str(user_row["body_text"] or "") if user_row else ""
        else:
            history_mod.record_user_message(
                conn, thread_id=thread_id, client_id=message_id, text=text
            )
            user_row = conn.execute(
                "SELECT id FROM messages WHERE client_id = %s", (message_id,)
            ).fetchone()
            user_server_id = str(user_row["id"]) if user_row else None
        title = thread.get("title")
        run_id = f"run_{uuid.uuid4().hex}"
        history_mod.mark_run_started(conn, thread_id, run_id)
        conn.commit()
        flagged = thread.get("outside_text_at") is not None or outside or outside_swap

    from pydantic_ai.ui.vercel_ai import VercelAIAdapter

    deps = AgentDeps(
        conversation_id=thread_id,
        message_id=user_server_id,
        flagged=flagged,
        user_texts=(text,),
    )
    adapter = await VercelAIAdapter.from_request(
        request,
        agent=app.state.agent,
        sdk_version=7,
        manage_message_history="server",
        allowed_file_url_schemes=frozenset(),
    )
    try:
        turn = await app.state.turns.start(
            adapter=adapter,
            thread_id=thread_id,
            message_id=message_id,
            history=history,
            deps=deps,
            title=title,
            run_id=run_id,
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
    return {"stopped": stopped, "thread_id": thread_id}


@router.get("/threads")
def list_threads(
    conn: Database,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    if q:
        rows = list(
            conn.execute(
                """
                SELECT c.id, c.title, c.updated_at, c.outside_text_at, c.run_started_at,
                       max(m.created_at) AS last_message_at,
                       count(m.id) AS message_count,
                       max(ts_headline('simple', coalesce(m.body_text, ''),
                                       websearch_to_tsquery('simple', %s))) AS snippet
                FROM conversations c
                JOIN messages m ON m.conversation_id = c.id
                WHERE m.search @@ websearch_to_tsquery('simple', %s)
                GROUP BY c.id
                ORDER BY max(m.created_at) DESC
                LIMIT %s
                """,
                (q, q, limit),
            ).fetchall()
        )
    else:
        rows = list(
            conn.execute(
                """
                SELECT c.id, c.title, c.updated_at, c.outside_text_at, c.run_started_at,
                       max(m.created_at) AS last_message_at,
                       count(m.id) AS message_count,
                       NULL::text AS snippet
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                GROUP BY c.id
                ORDER BY coalesce(max(m.created_at), c.updated_at) DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
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
def get_thread_messages(request: Request, conn: Database, thread_id: str) -> dict[str, Any]:
    thread = history_mod.get_thread(conn, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="That thread does not exist.")
    rows = list(
        conn.execute(
            """
            SELECT id, client_id, role, content, created_at, trace_id, flagged_at
            FROM messages WHERE conversation_id = %s ORDER BY created_at, id
            """,
            (thread_id,),
        ).fetchall()
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
def rename_thread(conn: Database, thread_id: str, body: ThreadRename) -> dict[str, Any]:
    row = conn.execute(
        "UPDATE conversations SET title = %s, updated_at = now() WHERE id = %s RETURNING id, title",
        (body.title.strip(), thread_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="That thread does not exist.")
    conn.commit()
    return {"id": row["id"], "title": row["title"]}


@router.post("/messages/{message_id}/flag")
def flag_message(conn: Database, message_id: str) -> dict[str, Any]:
    row = conn.execute(
        "UPDATE messages SET flagged_at = now() WHERE id = %s RETURNING id, trace_id, flagged_at",
        (message_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="That message does not exist.")
    conn.commit()
    return {
        "id": row["id"],
        "trace_id": row["trace_id"],
        "flagged_at": row["flagged_at"].isoformat(),
    }


@router.get("/preferences")
def get_preferences(conn: Database) -> dict[str, Any]:
    return {
        "preferences": read_tools.active_preferences(conn),
        "as_of": ledger_today().isoformat(),
    }


@router.patch("/preferences/{preference_id}")
def patch_preference(conn: Database, preference_id: str, body: PreferenceUpdate) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM preferences WHERE id = %s AND deleted_at IS NULL", (preference_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="That preference does not exist.")
    with conn.transaction():
        after = conn.execute(
            "UPDATE preferences SET active = %s, updated_at = now() WHERE id = %s RETURNING *",
            (body.active, preference_id),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO audit_log (actor, group_id, table_name, row_id, before, after)
            VALUES ('user', pgledger_generate_id('und'), 'preferences', %s, %s, %s)
            """,
            (preference_id, json.dumps(row, default=str), json.dumps(after, default=str)),
        )
    conn.commit()
    return dict(after)


@router.delete("/preferences/{preference_id}")
def delete_preference(conn: Database, preference_id: str) -> dict[str, Any]:
    try:
        return write_tools.delete_preference_for_good(conn, preference_id)
    except write_tools.ToolInputError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
