"""L6 surfaces: the inbox, push subscriptions, scheduled questions, and job status."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from fin_agent import scheduled_questions as questions_mod
from fin_agent.agent import history as history_mod
from fin_agent.api.deps import Database, current_session
from fin_agent.jobs.schedules import (
    ensure_builtin_schedules,
    job_status,
    sync_scheduled_questions,
)
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.common import today as ledger_today
from fin_agent.notifications import (
    list_notifications,
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
def get_notifications(
    conn: Database,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return list_notifications(conn, limit=limit)


@router.post("/notifications/read-all")
def post_read_all(conn: Database) -> dict[str, Any]:
    updated = mark_all_read(conn)
    conn.commit()
    return {"updated": updated}


@router.post("/notifications/{notification_id}/read")
@router.patch("/notifications/{notification_id}", include_in_schema=False)
def post_read(notification_id: str, conn: Database) -> dict[str, Any]:
    row = mark_read(conn, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That notification does not exist.")
    conn.commit()
    return row


@router.post("/notifications/{notification_id}/chat")
def post_notification_chat(notification_id: str, conn: Database) -> dict[str, Any]:
    """Open a new chat seeded with this notification; the user can ask a follow-up."""
    row = conn.execute("SELECT * FROM notifications WHERE id = %s", (notification_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="That notification does not exist.")
    notification = dict(row)
    text, outside = seed_chat_text(notification)
    thread = history_mod.create_thread(
        conn,
        title=f"Inbox: {notification['title']}"[:80],
    )
    if outside:
        conn.execute(
            "UPDATE conversations SET outside_text_at = now(), updated_at = now() WHERE id = %s",
            (thread["id"],),
        )
    client_id = new_id(conn, "msg")
    history_mod.record_user_message(
        conn,
        thread_id=thread["id"],
        client_id=client_id,
        text=text,
    )
    mark_read(conn, notification_id)
    conn.commit()
    return {
        "thread_id": thread["id"],
        "title": thread["title"],
        "message": text,
        "outside_text": outside,
    }


@router.get("/push/key")
@router.get("/push/vapid", include_in_schema=False)
def get_push_key(conn: Database) -> dict[str, Any]:
    payload = public_push_key(conn)
    conn.commit()
    return payload


@router.post("/push/subscribe", status_code=201)
def post_push_subscribe(body: PushSubscribe, conn: Database) -> dict[str, Any]:
    try:
        row = subscribe(
            conn,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    conn.commit()
    return {"subscribed": True, "subscription": row}


@router.delete("/push/subscribe")
def delete_push_subscribe(body: PushUnsubscribe, conn: Database) -> dict[str, Any]:
    removed = unsubscribe(conn, endpoint=body.endpoint)
    conn.commit()
    return {"subscribed": False, "removed": removed}


@router.get("/scheduled-questions")
def get_scheduled_questions(conn: Database) -> dict[str, Any]:
    return {
        "questions": questions_mod.list_questions(conn),
        "default_spend_limit_usd": str(questions_mod.DEFAULT_SPEND_LIMIT_USD),
        "as_of": ledger_today().isoformat(),
    }


@router.post("/scheduled-questions", status_code=201)
def post_scheduled_question(body: QuestionCreate, conn: Database) -> dict[str, Any]:
    row = questions_mod.create_question(
        conn,
        prompt=body.prompt,
        rule=body.rule,
        spend_limit_usd=body.spend_limit_usd,
    )
    conn.commit()
    return row


@router.patch("/scheduled-questions/{question_id}")
def patch_scheduled_question(
    question_id: str, body: QuestionUpdate, conn: Database
) -> dict[str, Any]:
    row = questions_mod.update_question(
        conn,
        question_id,
        prompt=body.prompt,
        rule=body.rule,
        spend_limit_usd=body.spend_limit_usd,
        active=body.active,
    )
    conn.commit()
    return row


@router.delete("/scheduled-questions/{question_id}")
def delete_scheduled_question(question_id: str, conn: Database) -> dict[str, Any]:
    row = questions_mod.archive_question(conn, question_id)
    conn.commit()
    return row


@router.get("/jobs")
def get_jobs(conn: Database, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    ensure_builtin_schedules(conn)
    sync_scheduled_questions(conn)
    payload = job_status(conn, limit=limit)
    conn.commit()
    return payload


__all__ = ["router"]
