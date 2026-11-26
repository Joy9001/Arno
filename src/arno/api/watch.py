"""L6 surfaces: the inbox and scheduled questions."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

from arno import scheduled_questions as questions_mod
from arno.agent import history as history_mod
from arno.agent.deps import AgentDeps
from arno.agent.turns import PreparationBusy, TurnBusy
from arno.api.deps import Database, Session, current_session
from arno.ledger.audit import new_id
from arno.ledger.common import today as ledger_today
from arno.notifications import list_page, mark_all_read, mark_read, seed_chat_text

logger = logging.getLogger("arno.watch")

router = APIRouter(
    prefix="/api",
    tags=["watch"],
    dependencies=[Depends(current_session)],
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionCreate(ApiModel):
    prompt: str = Field(min_length=1, max_length=2000)
    rule: str = Field(min_length=1, max_length=500)


class QuestionUpdate(ApiModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    rule: str | None = Field(default=None, min_length=1, max_length=500)
    active: bool | None = None


@router.get("/notifications")
async def get_notifications(
    conn: Database,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    try:
        return await list_page(conn, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/notifications/read-all")
async def post_read_all(conn: Database) -> dict[str, Any]:
    updated = await mark_all_read(conn)
    return {"updated": updated}


@router.post("/notifications/{notification_id}/read")
async def post_read(notification_id: str, conn: Database) -> dict[str, Any]:
    row = await mark_read(conn, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That notification does not exist.")
    return row


async def _start_seeded_turn(
    app: Any, conn: Any, *, thread: dict[str, Any], deps: AgentDeps, prompt: tuple[str, str]
) -> None:
    """Start the turn for a seeded user message (client id, text) already stored in the thread.

    The preparation guard is held until `turns.start` registers the turn, so this cannot
    overwrite a running turn's run marker. Raises `TurnBusy` or `PreparationBusy` first.
    """
    turns = app.state.turns
    thread_id = str(thread["id"])
    active = turns.get(thread_id)
    if active is not None and not active.done.is_set():
        raise TurnBusy("A reply is already running in this thread.")
    guard = turns.prepare(thread_id)
    await guard.acquire()
    run_id = f"run_{uuid.uuid4().hex}"
    try:
        await history_mod.mark_run_started(conn, thread_id, run_id)
        reply_id = f"msg_{uuid.uuid4().hex}"
        adapter = VercelAIAdapter(
            agent=app.state.agent,
            run_input=SubmitMessage(
                id=thread_id,
                messages=[UIMessage(id=prompt[0], role="user", parts=[TextUIPart(text=prompt[1])])],
                trigger="submit-message",
            ),
            sdk_version=7,
            server_message_id=reply_id,
        )
        await turns.start(
            adapter=adapter,
            thread_id=thread_id,
            message_id=prompt[0],
            history=history_mod.load_history(thread),
            deps=deps,
            title=thread.get("title"),
            run_id=run_id,
            reply_id=reply_id,
        )
    except BaseException:
        # The run never started, so the thread must not look busy until the next boot.
        await conn.execute(
            "UPDATE conversations SET run_started_at = NULL, active_run_id = NULL "
            "WHERE id = $1 AND active_run_id = $2",
            thread_id,
            run_id,
        )
        raise
    finally:
        guard.release()


@router.post("/notifications/{notification_id}/chat")
async def post_notification_chat(
    notification_id: str, request: Request, session: Session, conn: Database
) -> dict[str, Any]:
    """Open a new chat seeded with this notification, and have Arno answer it.

    The seeded message ends with a request, so a thread left waiting on the user would never
    reply. The server starts the turn here, as an import retry does; the client follows the
    reply it did not open.
    """
    row = await conn.fetchrow("SELECT * FROM notifications WHERE id = $1", notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That notification does not exist.")
    notification = dict(row)
    text = seed_chat_text(notification)
    thread = await history_mod.create_thread(
        conn,
        title=f"Inbox: {notification['title']}"[:80],
    )
    client_id = await new_id(conn, "msg")
    message = await history_mod.record_user_message(
        conn,
        thread_id=thread["id"],
        client_id=client_id,
        text=text,
    )
    await mark_read(conn, notification_id)
    started = False
    if getattr(request.app.state, "turns", None) is not None:
        try:
            await _start_seeded_turn(
                request.app,
                conn,
                thread=thread,
                deps=AgentDeps(
                    conversation_id=str(thread["id"]),
                    message_id=str(message["id"]),
                    user_id=session["login"],
                ),
                prompt=(client_id, text),
            )
            started = True
        except TurnBusy, PreparationBusy:
            # A reply already runs here; the seeded message waits for the user's next turn.
            pass
        except Exception:  # noqa: BLE001 - the seeded thread is still usable by hand
            logger.exception("could not start a turn for the notification in %s", thread["id"])
    return {
        "thread_id": thread["id"],
        "title": thread["title"],
        "message": text,
        "started": started,
    }


@router.get("/scheduled-questions")
async def get_scheduled_questions(
    conn: Database, include_inactive: bool = Query(default=False)
) -> dict[str, Any]:
    return {
        "questions": await questions_mod.list_questions(conn, include_inactive=include_inactive),
        "as_of": ledger_today().isoformat(),
    }


@router.post("/scheduled-questions", status_code=201)
async def post_scheduled_question(body: QuestionCreate, conn: Database) -> dict[str, Any]:
    return await questions_mod.create_question(
        conn,
        prompt=body.prompt,
        rule=body.rule,
    )


@router.patch("/scheduled-questions/{question_id}")
async def patch_scheduled_question(
    question_id: str, body: QuestionUpdate, conn: Database
) -> dict[str, Any]:
    return await questions_mod.update_question(
        conn,
        question_id,
        prompt=body.prompt,
        rule=body.rule,
        active=body.active,
    )


@router.delete("/scheduled-questions/{question_id}")
async def delete_scheduled_question(question_id: str, conn: Database) -> dict[str, Any]:
    return await questions_mod.archive_question(conn, question_id)


__all__ = ["router"]
