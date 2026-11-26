"""Chat, threads, preferences, and flagging.

The chat endpoint builds the adapter, starts a detached turn, and hands back a stream that reads
from the turn's buffer. Reattach and Stop have their own endpoints; one turn runs per thread.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import ToolApproved
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from arno.agent import history as history_mod
from arno.agent.deps import AgentDeps
from arno.agent.images import with_photos
from arno.agent.turns import TurnBusy, encode_saved_turn, pending_approvals
from arno.api.deps import Database, PreparationDep, Session, current_session
from arno.db.conn import Connection
from arno.ledger.common import today as ledger_today
from arno.tools import read as read_tools
from arno.tools import write as write_tools
from arno.tools.write import MovementChanges, SettingsSetup

router = APIRouter(
    prefix="/api",
    tags=["chat"],
    dependencies=[Depends(current_session)],
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreadRename(ApiModel):
    title: str = Field(min_length=1, max_length=200)


class ThreadCreate(ApiModel):
    title: str | None = Field(default=None, max_length=200)


class PreferenceUpdate(ApiModel):
    active: bool


class PreferenceKindUpdate(ApiModel):
    kind: Literal["preference", "fact", "rule"]


def _approval_responses(messages: list[dict[str, Any]]) -> list[str]:
    """The tool call ids this request answers, waiting for the server to run them.

    The AI SDK keeps `approval.approved` on a tool part after its result arrives, so a settled
    part is not a submission: only `approval-responded` is still waiting. Matching any part that
    carries an approval made every later message in the thread look like an approval turn.
    """
    ids: list[str] = []
    for message in messages:
        for part in message.get("parts") or []:
            if not isinstance(part, dict) or part.get("state") != "approval-responded":
                continue
            approval = part.get("approval")
            if not isinstance(approval, dict) or not isinstance(approval.get("approved"), bool):
                continue
            tool_call_id = part.get("toolCallId") or part.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id:
                ids.append(tool_call_id)
    return ids


def _answered_tool_calls(history: list[Any]) -> set[str]:
    """The tool call ids the stored history already has a result for."""
    return {
        str(part.tool_call_id)
        for message in history
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", "") in {"tool-return", "retry-prompt"} and part.tool_call_id
    }


def _last_user(messages: list[dict[str, Any]]) -> tuple[str, str, list[Any]] | None:
    """The last user message's id, typed text, and parts."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        parts = [part for part in message.get("parts") or [] if isinstance(part, dict)]
        text = "\n".join(str(part.get("text", "")) for part in parts if part.get("type") == "text")
        return str(message.get("id") or uuid.uuid4()), text, parts
    return None


def _attachment_refs(parts: list[Any], part_type: str, kind: str) -> list[dict[str, str]]:
    """App-owned refs of one attachment kind from the last user turn, never file URLs."""
    found: dict[str, str] = {}
    imports: dict[str, str] = {}
    for part in parts:
        data = part.get("data") if part.get("type") == part_type else None
        if not isinstance(data, dict):
            continue
        attachment_id = data.get("attachment_id")
        if isinstance(attachment_id, str) and 0 < len(attachment_id) <= 100:
            found.setdefault(attachment_id, str(data.get("filename") or kind)[:255])
            import_id = data.get("import_id")
            if isinstance(import_id, str) and 0 < len(import_id) <= 100:
                imports.setdefault(attachment_id, import_id)
    return [
        {
            "attachment_id": key,
            "filename": name,
            **({"import_id": imports[key]} if key in imports else {}),
        }
        for key, name in found.items()
    ]


async def _validated(
    conn: Connection, refs: list[dict[str, str]], kind: str
) -> list[dict[str, str]]:
    """The refs that still name a stored file of that kind; a stale one refuses the request."""
    if not refs:
        return []
    ids = [ref["attachment_id"] for ref in refs]
    rows = await conn.fetch(
        """
        SELECT id, filename FROM attachments
        WHERE id = ANY($1) AND kind = $2 AND data IS NOT NULL AND purged_at IS NULL
        """,
        ids,
        kind,
    )
    found = {str(row["id"]): str(row["filename"] or kind) for row in rows}
    if any(attachment_id not in found for attachment_id in ids):
        raise HTTPException(
            status_code=422,
            detail=f"A {kind} attachment is no longer available. Attach the file again.",
        )
    # A statement's import card needs the import the upload opened; only a real one is kept.
    wanted = [ref["import_id"] for ref in refs if ref.get("import_id")]
    real = {
        str(row["id"])
        for row in await conn.fetch("SELECT id FROM imports WHERE id = ANY($1)", wanted)
    }
    return [
        {
            "attachment_id": ref["attachment_id"],
            "filename": found[ref["attachment_id"]],
            **({"import_id": ref["import_id"]} if ref.get("import_id") in real else {}),
        }
        for ref in refs
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


async def _saved_reply(conn: Connection, thread_id: str) -> StreamingResponse | None:
    """The thread's last saved reply, replayed instead of running a turn again."""
    saved = await conn.fetchrow(
        """
        SELECT content FROM messages
        WHERE conversation_id = $1 AND role = 'assistant' AND run_id IS NOT NULL
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        thread_id,
    )
    if saved is None:
        return None
    return _stream_response(iter(encode_saved_turn(saved["content"])), thread_id=thread_id)


def _check_approvals(history: list[Any], approval_ids: list[str]) -> None:
    """Refuse a confirm answer that does not match exactly what the last run is waiting on.

    Every confirm card of that run must be answered together: the model cannot resume a run with a
    result missing, and a card left behind is a change the user asked for and never got. Nothing
    is applied, and every card stays where it was.
    """
    waiting = {
        str(call.tool_call_id) for call in pending_approvals(history, _answered_tool_calls(history))
    }
    if unanswered := [call_id for call_id in approval_ids if call_id not in waiting]:
        raise HTTPException(
            status_code=409,
            detail=(
                "That confirmation is not one this thread is waiting for "
                f"({', '.join(unanswered)}); nothing was applied."
            ),
        )
    if missing := sorted(waiting - set(approval_ids)):
        raise HTTPException(
            status_code=409,
            detail=(
                "That turn asked for more than one confirmation, so nothing was applied "
                "yet. Answer every card in one go; still waiting for: "
                f"{', '.join(missing)}."
            ),
        )


def _approval_edits(
    edits: Any, messages: list[dict[str, Any]], history: list[Any]
) -> dict[str, ToolApproved]:
    """Only edit fields offered by a stored pending call that the user approved."""
    if not isinstance(edits, dict) or not edits:
        raise HTTPException(status_code=422, detail="Invalid approval edits.")
    approved = {
        part.get("toolCallId")
        for message in messages
        for part in message.get("parts") or []
        if isinstance(part, dict)
        and isinstance(part.get("toolCallId"), str)
        and part.get("state") == "approval-responded"
        and isinstance(part.get("approval"), dict)
        and part["approval"].get("approved") is True
    }
    pending = {
        call.tool_call_id: call
        for call in pending_approvals(history, _answered_tool_calls(history))
    }
    overrides: dict[str, ToolApproved] = {}
    for call_id, edit in edits.items():
        call = pending.get(call_id)
        if call is None or call_id not in approved or not isinstance(edit, dict):
            raise HTTPException(status_code=422, detail="Invalid approval edit.")
        args = call.args_as_dict()
        try:
            if call.tool_name == "correct_movement" and set(edit) == {"category"}:
                changes = args.get("changes")
                category = edit["category"]
                if (
                    not isinstance(changes, dict)
                    or "category" not in changes
                    or not isinstance(category, str)
                    or not category.strip()
                ):
                    raise ValueError("Invalid category")
                args = {**args, "changes": {**changes, "category": category.strip()}}
                MovementChanges.model_validate(args["changes"])
            elif call.tool_name == "update_setup" and set(edit) == {"floor_rupees"}:
                data = args.get("data")
                floor = edit["floor_rupees"]
                if (
                    args.get("entity") != "settings"
                    or args.get("action") != "update"
                    or not isinstance(data, dict)
                    or "floor_rupees" not in data
                    or isinstance(floor, bool)
                    or not isinstance(floor, (int, float))
                ):
                    raise ValueError("Invalid floor")
                args = {**args, "data": {**data, "floor_rupees": floor}}
                SettingsSetup.model_validate(args["data"])
                write_tools._balance_rupees(floor, "floor_rupees")
            else:
                raise ValueError("Edit does not match the pending action")
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid approval edit.") from exc
        overrides[call_id] = ToolApproved(override_args=args)
    return overrides


@router.post("/chat")
async def post_chat(
    request: Request, session: Session, preparation: PreparationDep, conn: Database
) -> Response:
    # `preparation` is a function-scoped dependency: it holds the thread's preparation guard
    # until this function returns and releases it before the stream is consumed.
    app = request.app
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="The chat body is not an object.")
    messages = body.get("messages") or []
    approval_ids = _approval_responses(messages)
    # A confirm answer ends on the assistant's own message, with no new user turn after it.
    approval_turn = bool(approval_ids and messages and messages[-1].get("role") == "assistant")
    last = _last_user(messages)
    if last is None and not approval_turn:
        raise HTTPException(status_code=422, detail="A chat request needs a user message.")
    no_parts: list[Any] = []
    message_id, text, parts = last or (str(body.get("messageId") or uuid.uuid4()), "", no_parts)
    thread_id = str(body["id"]) if body.get("id") else None
    if approval_turn:
        parts = no_parts
    statement_refs = _attachment_refs(parts, "data-statement", "statement")
    photo_refs = _attachment_refs(parts, "data-photo", "photo")
    if not text.strip():
        image_noun = "image" if len(photo_refs) == 1 else "images"
        if statement_refs and photo_refs:
            text = f"I attached a statement and {'an image' if len(photo_refs) == 1 else 'images'}."
        elif statement_refs:
            text = "I attached a statement."
        elif photo_refs:
            text = f"See the attached {image_noun}."

    thread = await history_mod.get_thread(conn, thread_id) if thread_id else None
    if thread is None:
        # AI SDK always sends a chat id, including for a new chat, so the server creates the row
        # under it rather than asking the client for a second ID.
        if thread_id and len(thread_id) > 100:
            raise HTTPException(status_code=422, detail="That chat ID is too long.")
        thread = await history_mod.create_thread(
            conn, title=history_mod.title_for(text), thread_id=thread_id
        )
        thread_id = str(thread["id"])
    assert thread_id is not None

    existing = await conn.fetchrow("SELECT * FROM messages WHERE client_id = $1", message_id)
    if existing is not None and not approval_turn:
        # A message ID starts one turn, ever. During it, reattach; after it, replay the saved
        # reply instead of redoing the write.
        existing_thread = str(existing["conversation_id"])
        turn = app.state.turns.get(existing_thread)
        if turn is not None and not turn.done.is_set() and turn.message_id == message_id:
            return _stream_response(turn.follow(0), thread_id=existing_thread)
        replay = await _saved_reply(conn, existing_thread)
        if replay is None:
            raise HTTPException(
                status_code=409, detail="That message ID was already used; send a new message."
            )
        return replay

    active_turn = app.state.turns.get(thread_id)
    if active_turn is not None and not active_turn.done.is_set():
        raise HTTPException(status_code=409, detail="A reply is already running in this thread.")

    # Validate every ref before the turn starts, so a stale file cannot leave a partial turn.
    # Each photo capture keeps its own idempotency key, so a resend cannot write twice.
    statements = await _validated(conn, statement_refs, "statement")
    photos = await _validated(conn, photo_refs, "photo")

    history = history_mod.load_history(thread)
    overrides: dict[str, ToolApproved] = {}
    if "approval_edits" in body and not approval_turn:
        raise HTTPException(status_code=422, detail="Approval edits require an approval.")
    if approval_turn:
        if set(approval_ids) <= _answered_tool_calls(history):
            # A retry or a duplicate POST of a tap that already landed. Replaying its reply keeps
            # the write applied once.
            replay = await _saved_reply(conn, thread_id)
            if replay is not None:
                return replay
        _check_approvals(history, approval_ids)
        if "approval_edits" in body:
            overrides = _approval_edits(body["approval_edits"], messages, history)
        user_row = await conn.fetchrow(
            "SELECT id FROM messages WHERE conversation_id = $1 AND role = 'user' "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            thread_id,
        )
    else:
        user_row = await history_mod.record_user_message(
            conn,
            thread_id=thread_id,
            client_id=message_id,
            text=text,
            statements=statements,
            photos=photos,
        )
    run_id = f"run_{uuid.uuid4().hex}"
    await history_mod.mark_run_started(conn, thread_id, run_id)

    capture_ref, capture_kind = None, "chat"
    voice_attachment_id = body.get("voice_attachment_id")
    if isinstance(voice_attachment_id, str) and voice_attachment_id:
        capture_ref = await conn.fetchval(
            "SELECT id FROM attachments WHERE id = $1 AND kind = 'audio' AND data IS NOT NULL",
            voice_attachment_id,
        )
        if capture_ref is not None:
            capture_kind = "voice"
    if capture_ref is None and photos:
        capture_ref, capture_kind = photos[0]["attachment_id"], "photo"

    reply_id = f"msg_{uuid.uuid4().hex}"
    adapter = await VercelAIAdapter.from_request(
        request,
        agent=app.state.agent,
        sdk_version=7,
        # The streamed reply carries the id it is saved under, so the browser can quote it at once.
        server_message_id=reply_id,
        manage_message_history="server",
        allowed_file_url_schemes=frozenset(),
    )
    if overrides:
        assert adapter.deferred_tool_results is not None
        adapter.deferred_tool_results.approvals.update(overrides)
    if photos:
        # Markers ride on the model request; ImageAttachments hydrates BinaryContent right
        # before dispatch.
        adapter = replace(
            adapter, run_input=with_photos(adapter.run_input, photos, fallback_text=text)
        )
    deps = AgentDeps(
        conversation_id=thread_id,
        message_id=str(user_row["id"]) if user_row else None,
        user_id=session["login"],
        capture_kind=capture_kind,
        capture_ref=capture_ref,
        image_attachments=tuple(photo["attachment_id"] for photo in photos),
    )
    try:
        turn = await app.state.turns.start(
            adapter=adapter,
            thread_id=thread_id,
            message_id=message_id,
            history=history,
            deps=deps,
            title=thread.get("title"),
            run_id=run_id,
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
        # The reply keeps the id the live stream used, so a reload matches it; `server_id`
        # stays the row id for Flag and deep links.
        message_id = content.get("id") or (
            row["id"] if row["role"] == "assistant" else row["client_id"]
        )
        content = {
            **content,
            "id": message_id,
            "server_id": row["id"],
            "metadata": {
                **(content.get("metadata") or {}),
                "server_id": row["id"],
                "flagged_at": row["flagged_at"].isoformat() if row["flagged_at"] else None,
            },
        }
        messages.append(content)
    turn = request.app.state.turns.get(thread_id)
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


@router.post("/threads", status_code=201)
async def create_thread(conn: Database, body: ThreadCreate | None = None) -> dict[str, Any]:
    """Create a thread row before its first message, so the client never keys it by a local id."""
    title = body.title.strip() if body and body.title and body.title.strip() else None
    thread = await history_mod.create_thread(conn, title=title)
    return {"id": thread["id"], "title": thread["title"] or "New chat"}


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
    after, _undo_id = await write_tools.edit_preference(
        conn, preference_id, actor="user", active=body.active
    )
    return after


@router.patch("/preferences/{preference_id}/kind")
async def patch_preference_kind(
    conn: Database, preference_id: str, body: PreferenceKindUpdate
) -> dict[str, Any]:
    after, undo_id = await write_tools.edit_preference(
        conn, preference_id, actor="user", kind=body.kind
    )
    return {**after, "undo_id": undo_id}


@router.delete("/preferences/{preference_id}")
async def delete_preference(conn: Database, preference_id: str) -> dict[str, Any]:
    try:
        return await write_tools.delete_preference_for_good(conn, preference_id)
    except write_tools.ToolInputError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
