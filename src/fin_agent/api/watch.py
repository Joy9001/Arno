"""L6 surfaces: the inbox, push subscriptions, scheduled questions, and job status."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from fin_agent import scheduled_questions as questions_mod
from fin_agent.agent import history as history_mod
from fin_agent.api.deps import Database, current_session
from fin_agent.api.notification_query import list_page as list_notification_page
from fin_agent.jobs.schedules import (
    ensure_builtin_schedules,
    job_status,
    job_views,
    sync_scheduled_questions,
)
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import today as ledger_today
from fin_agent.notifications import (
    mark_all_read,
    mark_read,
    public_push_key,
    seed_chat_text,
    subscribe,
    unsubscribe,
)

router = APIRouter(
    prefix="/api",
    tags=["watch"],
    dependencies=[Depends(current_session)],
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PushKeys(ApiModel):
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushSubscribe(ApiModel):
    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys


class PushUnsubscribe(ApiModel):
    endpoint: str = Field(min_length=1, max_length=2048)


class QuestionCreate(ApiModel):
    prompt: str = Field(min_length=1, max_length=2000)
    rule: str = Field(min_length=1, max_length=500)
    spend_limit_usd: Decimal | None = Field(default=None, ge=0)


class QuestionUpdate(ApiModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    rule: str | None = Field(default=None, min_length=1, max_length=500)
    spend_limit_usd: Decimal | None = Field(default=None, ge=0)
    active: bool | None = None


@router.get("/notifications")
async def get_notifications(
    conn: Database,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    try:
        return await list_notification_page(conn, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/notifications/read-all")
async def post_read_all(conn: Database) -> dict[str, Any]:
    updated = await mark_all_read(conn)
    return {"updated": updated}


@router.post("/notifications/{notification_id}/read")
@router.patch("/notifications/{notification_id}", include_in_schema=False)
async def post_read(notification_id: str, conn: Database) -> dict[str, Any]:
    row = await mark_read(conn, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That notification does not exist.")
    return row


@router.post("/notifications/{notification_id}/chat")
async def post_notification_chat(notification_id: str, conn: Database) -> dict[str, Any]:
    """Open a new chat seeded with this notification; the user can ask a follow-up."""
    row = await conn.fetchrow("SELECT * FROM notifications WHERE id = $1", notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That notification does not exist.")
    notification = dict(row)
    text, outside = seed_chat_text(notification)
    thread = await history_mod.create_thread(
        conn,
        title=f"Inbox: {notification['title']}"[:80],
    )
    if outside:
        await conn.execute(
            "UPDATE conversations SET outside_text_at = now(), updated_at = now() WHERE id = $1",
            thread["id"],
        )
    client_id = await new_id(conn, "msg")
    await history_mod.record_user_message(
        conn,
        thread_id=thread["id"],
        client_id=client_id,
        text=text,
    )
    await mark_read(conn, notification_id)
    return {
        "thread_id": thread["id"],
        "title": thread["title"],
        "message": text,
        "outside_text": outside,
    }


@router.get("/push/key")
@router.get("/push/vapid", include_in_schema=False)
async def get_push_key(conn: Database) -> dict[str, Any]:
    return await public_push_key(conn)


@router.post("/push/subscribe", status_code=201)
async def post_push_subscribe(body: PushSubscribe, conn: Database) -> dict[str, Any]:
    try:
        row = await subscribe(
            conn,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"subscribed": True, "subscription": row}


@router.delete("/push/subscribe")
async def delete_push_subscribe(body: PushUnsubscribe, conn: Database) -> dict[str, Any]:
    removed = await unsubscribe(conn, endpoint=body.endpoint)
    return {"subscribed": False, "removed": removed}


@router.get("/scheduled-questions")
async def get_scheduled_questions(
    conn: Database, include_inactive: bool = Query(default=False)
) -> dict[str, Any]:
    return {
        "questions": await questions_mod.list_questions(conn, include_inactive=include_inactive),
        "default_spend_limit_usd": str(questions_mod.DEFAULT_SPEND_LIMIT_USD),
        "as_of": ledger_today().isoformat(),
    }


@router.post("/scheduled-questions", status_code=201)
async def post_scheduled_question(body: QuestionCreate, conn: Database) -> dict[str, Any]:
    return await questions_mod.create_question(
        conn,
        prompt=body.prompt,
        rule=body.rule,
        spend_limit_usd=body.spend_limit_usd,
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
        spend_limit_usd=body.spend_limit_usd,
        active=body.active,
    )


@router.delete("/scheduled-questions/{question_id}")
async def delete_scheduled_question(question_id: str, conn: Database) -> dict[str, Any]:
    return await questions_mod.archive_question(conn, question_id)


@router.get("/jobs")
async def get_jobs(conn: Database, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    await ensure_builtin_schedules(conn)
    await sync_scheduled_questions(conn)
    return await job_status(conn, limit=limit)


@router.get("/jobs/activity")
async def get_job_activity(conn: Database, since: datetime | None = None) -> dict[str, Any]:
    """A pure read the app polls: what is running, and what finished after `since`.

    The cursor is read first and runs a little behind, so a job that finishes during this read
    or commits late is reported on the next poll, never lost. The app dedupes the overlap.
    """
    now = await conn.fetchval("SELECT now() - interval '2 seconds'")
    active_rows = await conn.fetch(
        """
        SELECT * FROM jobs
        WHERE status IN ('pending', 'running')
        ORDER BY (status = 'running') DESC, run_at, id
        """
    )
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    finished_rows = (
        await conn.fetch(
            """
            SELECT * FROM jobs
            WHERE status IN ('done', 'failed') AND finished_at > $1
            ORDER BY finished_at, id
            LIMIT 50
            """,
            since,
        )
        if since is not None
        else []
    )
    views = await job_views(conn, [*active_rows, *finished_rows])
    split = len(active_rows)
    return {
        "active": views[:split],
        "finished": views[split:],
        "now": now.isoformat(),
    }


@router.post("/jobs/{job_id}/retry")
async def post_job_retry(job_id: str, conn: Database) -> dict[str, Any]:
    """Run a failed job again on the same row, and clear the alert the user acted on.

    The old alert gives up its dedupe key, so failing again raises a fresh one.
    """
    row = await conn.fetchrow(
        """
        UPDATE jobs
        SET status = 'pending', attempts = 0, run_at = now(), started_at = NULL,
            finished_at = NULL, last_error = NULL, timed_out_at = NULL, result = NULL,
            updated_at = now()
        WHERE id = $1 AND status = 'failed'
        RETURNING *
        """,
        job_id,
    )
    if row is None:
        if await conn.fetchval("SELECT 1 FROM jobs WHERE id = $1", job_id) is None:
            raise HTTPException(status_code=404, detail="That job does not exist.")
        raise HTTPException(status_code=409, detail="Only a failed job can be retried.")
    await conn.execute(
        """
        UPDATE notifications
        SET read_at = coalesce(read_at, now()), dedupe_key = NULL
        WHERE dedupe_key = $1
        """,
        f"job_failed:{job_id}",
    )
    views = await job_views(conn, [row])
    return views[0]


__all__ = ["router"]
