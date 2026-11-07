"""Parser task state for the import card, and the layouts' parser versions.

The task lives in the harness owner on `app.state.tasks`; the import rows, their progress,
the plan, and the parser versions live in Postgres. Nothing here trusts a client's text: the
answer text is composed from the account row.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from fin_agent.api.deps import Database, current_session
from fin_agent.importers.layouts import rollback_layout

router = APIRouter(prefix="/api", tags=["tasks"], dependencies=[Depends(current_session)])

ACCOUNT_KINDS = ("bank", "card", "wallet")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnswerIn(ApiModel):
    question_id: str = Field(min_length=1, max_length=200)
    account_id: str = Field(min_length=1, max_length=200)


class RollbackIn(ApiModel):
    version: int = Field(ge=1)


def _record(tasks: Any, task_id: str) -> Any:
    record = tasks.records.get(task_id)
    if record is None or record.agent_name != "parser":
        raise HTTPException(status_code=404, detail="No such parser task")
    return record


def _report(output: str) -> dict[str, Any] | None:
    try:
        value = json.loads(output)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def _plan(conn: Database, task_id: str) -> list[dict[str, Any]]:
    exists = await conn.fetchval("SELECT to_regclass('public.plan_items') IS NOT NULL")
    if not exists:
        return []
    rows = await conn.fetch(
        """
        SELECT id, content, status, active_form, parent_id
        FROM plan_items WHERE session = $1 ORDER BY seq
        """,
        task_id,
    )
    return [dict(row) for row in rows]


async def _imports(conn: Database, task_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT i.id, i.status, i.reason, i.progress, i.counts, a.filename
        FROM imports i
        LEFT JOIN LATERAL (
            SELECT filename FROM attachments
            WHERE import_id = i.id ORDER BY created_at DESC LIMIT 1
        ) a ON true
        WHERE i.task_id = $1
        ORDER BY i.created_at, i.id
        """,
        task_id,
    )
    return [
        {
            "import_id": row["id"],
            "filename": row["filename"],
            "status": row["status"],
            "reason": row["reason"],
            "progress": row["progress"],
            "counts": row["counts"],
        }
        for row in rows
    ]


async def task_payload(
    conn: Database, tasks: Any, task_id: str, *, answered: str | None = None
) -> dict[str, Any]:
    record = _record(tasks, task_id)
    pending = tasks.pending_question(task_id)
    if pending is not None and pending[0] != answered:
        question = {"question_id": pending[0], "text": pending[1]}
    elif pending is None and record.pending_question:
        # Settled with a question still recorded: the card shows the words, chat answers it.
        question = {"question_id": None, "text": record.pending_question}
    else:
        question = None
    return {
        "task_id": record.id,
        "agent_name": record.agent_name,
        "state": record.status,
        "outcome": record.outcome,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "report": _report(record.output),
        "question": question,
        "plan": await _plan(conn, task_id),
        "imports": await _imports(conn, task_id),
    }


@router.get("/tasks/{task_id}")
async def task_detail(task_id: str, request: Request, conn: Database) -> dict[str, Any]:
    return await task_payload(conn, request.app.state.tasks, task_id)


@router.post("/tasks/{task_id}/answer")
async def task_answer(
    task_id: str, body: AnswerIn, request: Request, conn: Database
) -> dict[str, Any]:
    tasks = request.app.state.tasks
    _record(tasks, task_id)
    pending = tasks.pending_question(task_id)
    if pending is None or pending[0] != body.question_id:
        raise HTTPException(status_code=409, detail="That task is not waiting on this question.")
    account = await conn.fetchrow(
        """
        SELECT id, name, kind, institution FROM accounts
        WHERE id = $1 AND active AND kind = ANY($2::text[])
        """,
        body.account_id,
        list(ACCOUNT_KINDS),
    )
    if account is None:
        raise HTTPException(
            status_code=422, detail="Choose an active bank, card, or wallet account."
        )
    answer = (
        f"The statement is for the account {account['name']} "
        f"({account['institution'] or account['kind']}), id {account['id']}."
    )
    if not tasks.answer(task_id, question_id=body.question_id, answer=answer):
        raise HTTPException(status_code=409, detail="That task is not waiting on this question.")
    return await task_payload(conn, tasks, task_id, answered=body.question_id)


def _versions(rows: list[Any], active_id: str | None) -> list[dict[str, Any]]:
    return [
        {
            "version": row["version"],
            "created_at": row["created_at"].isoformat(),
            "helper_version": row["helper_version"],
            "active": row["id"] == active_id,
        }
        for row in rows
    ]


async def _layouts(conn: Database) -> list[dict[str, Any]]:
    layouts = await conn.fetch("SELECT * FROM statement_layouts ORDER BY institution, kind")
    versions = await conn.fetch("SELECT * FROM parser_versions ORDER BY layout_id, version DESC")
    by_layout: dict[str, list[Any]] = {}
    for row in versions:
        by_layout.setdefault(row["layout_id"], []).append(row)
    items: list[dict[str, Any]] = []
    for layout in layouts:
        rows = by_layout.get(layout["id"], [])
        active = next(
            (row["version"] for row in rows if row["id"] == layout["active_version_id"]), None
        )
        items.append(
            {
                "id": layout["id"],
                "institution": layout["institution"],
                "kind": layout["kind"],
                "variants": list(layout["variants"] or []),
                "active_version": active,
                "versions": _versions(rows, layout["active_version_id"]),
            }
        )
    return items


@router.get("/layouts")
async def layouts_list(conn: Database) -> dict[str, Any]:
    return {"items": await _layouts(conn)}


@router.post("/layouts/{layout_id}/rollback")
async def layout_rollback(layout_id: str, body: RollbackIn, conn: Database) -> dict[str, Any]:
    layout = await conn.fetchrow("SELECT id FROM statement_layouts WHERE id = $1", layout_id)
    if layout is None:
        raise HTTPException(status_code=404, detail="No such layout")
    try:
        await rollback_layout(conn, layout_id=layout_id, version=body.version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for item in await _layouts(conn):
        if item["id"] == layout_id:
            return item
    raise HTTPException(status_code=404, detail="No such layout")


__all__ = ["router", "task_payload"]
