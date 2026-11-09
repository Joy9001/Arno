"""Parser task state for the import card, and the layouts' parser versions.

The task lives in the harness owner on `app.state.tasks`; the import rows, their progress,
the plan, and the parser versions live in Postgres. Nothing here trusts a client's text: the
answer text is composed from the account row.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai_harness.subagents import DelegationTask, DelegationTasks

from fin_agent.agent.parser import PARSE_USAGE_LIMITS
from fin_agent.api.deps import Database, current_session
from fin_agent.api.run_view import run_view
from fin_agent.importers.layouts import rollback_layout

router = APIRouter(prefix="/api", tags=["tasks"], dependencies=[Depends(current_session)])

ACCOUNT_KINDS = ("bank", "card", "wallet")

# A tool result can be a whole page of statement rows; the transcript is a receipt, not a dump.
RESULT_LIMIT = 1200

_TASK_ID = re.compile(r"[0-9a-f]{32}\Z")
_TASK_RECORD = TypeAdapter(DelegationTask)
_SAVE_LOCKS: dict[str, anyio.Lock] = {}


class TaskOwner(DelegationTasks):
    """The app's delegation tasks, saved so a second writer can never lose a saved record.

    The harness serialises saves per owner and writes one fixed `<id>.tmp` before renaming it,
    so two owners on one directory — a second app instance in this process, or a test's own
    owner — consume each other's temp file between the write and the rename, and the loser
    fails with `FileNotFoundError`. A temp name unique to this write, and a directory made
    again if it was cleaned up under us, keep the rename atomic for any number of writers. The
    per-directory lock keeps two of them off one destination at the same time, which Windows
    refuses; the app itself writes from one event loop.
    """

    async def save(self, record: DelegationTask) -> None:
        if self.directory is None:
            return
        if not _TASK_ID.fullmatch(record.id):
            raise ValueError("Invalid task identity")
        lock = _SAVE_LOCKS.setdefault(str(self.directory), anyio.Lock())
        async with lock:
            await anyio.Path(self.directory).mkdir(parents=True, exist_ok=True)
            temporary = anyio.Path(self.directory / f"{record.id}.{uuid4().hex}.tmp")
            try:
                await temporary.write_bytes(_TASK_RECORD.dump_json(record))
                await temporary.replace(anyio.Path(self.directory / f"{record.id}.json"))
            except BaseException:
                with suppress(OSError):
                    await temporary.unlink(missing_ok=True)
                raise


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


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= RESULT_LIMIT else f"{text[:RESULT_LIMIT]}…"


def _args(part: ToolCallPart) -> dict[str, Any]:
    try:
        args = part.args_as_dict()
    except TypeError, ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def _returns(messages: list[ModelMessage]) -> dict[str, str]:
    returns: dict[str, str] = {}
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart):
                returns[part.tool_call_id] = _short(part.content)
    return returns


def _content(part: Any, returns: dict[str, str]) -> dict[str, Any] | None:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.content} if part.content else None
    if not isinstance(part, ToolCallPart):
        return None
    args = _args(part)
    call: dict[str, Any] = {
        "type": "tool-call",
        "toolCallId": part.tool_call_id,
        "toolName": part.tool_name,
        "args": args,
        "argsText": json.dumps(args, ensure_ascii=False),
    }
    if part.tool_call_id in returns:
        call["result"] = returns[part.tool_call_id]
    return call


def _transcript(record: Any, messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """The child's conversation as assistant-ui `ThreadMessage` objects.

    Thinking, system prompts, and retries stay out; a tool call carries its result from
    the matching return so the transcript reads on its own.
    """
    fallback = datetime.fromtimestamp(record.started_at, tz=UTC)
    returns = _returns(messages)
    transcript: list[dict[str, Any]] = []
    for message in messages:
        created_at = (message.timestamp or fallback).isoformat()
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if (
                    isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                    and part.content.strip()
                ):
                    transcript.append(
                        {
                            "id": f"{record.id}:{len(transcript)}",
                            "role": "user",
                            "createdAt": created_at,
                            "content": [{"type": "text", "text": part.content}],
                        }
                    )
            continue
        if not isinstance(message, ModelResponse):
            continue
        content = [item for part in message.parts if (item := _content(part, returns)) is not None]
        if content:
            transcript.append(
                {
                    "id": f"{record.id}:{len(transcript)}",
                    "role": "assistant",
                    "createdAt": created_at,
                    "status": {"type": "complete", "reason": "stop"},
                    "content": content,
                }
            )
    return transcript


async def _imports(conn: Database, task_id: str, *, running: bool) -> list[dict[str, Any]]:
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
            # A stopped task's last progress line is stale; the card shows the import as stopped.
            "progress": row["progress"] if running else None,
            "counts": row["counts"],
        }
        for row in rows
    ]


# A wake starts within a second of the report; past this, the report waits for the next message.
FOLLOW_UP_SECONDS = 120


def _follow_up(record: Any, turns: Any) -> str | None:
    """Where the chat stands on this task's report: waiting for a turn, or being answered now.

    The open chat follows a `replying` turn it did not start, so the agent's words appear there.
    """
    if record.status != "finished" or record.parent_id is not None or not record.background:
        return None
    turn = turns.get(record.conversation_id) if turns is not None else None
    if (
        turn is not None
        and not turn.done.is_set()
        and turn.wake is not None
        and record.id in turn.deps.woken_by
    ):
        return "replying"
    fresh = time.time() - (record.finished_at or 0) < FOLLOW_UP_SECONDS
    if fresh and not record.delivered and not record.user_stopped:
        return "waiting"
    return None


async def task_payload(
    conn: Database,
    tasks: Any,
    task_id: str,
    *,
    answered: str | None = None,
    turns: Any = None,
) -> dict[str, Any]:
    record = _record(tasks, task_id)
    history = tasks.history(task_id)
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
        "conversation_id": record.conversation_id,
        "state": record.status,
        "outcome": record.outcome,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "report": _report(record.output),
        "question": question,
        "plan": await _plan(conn, task_id),
        "imports": await _imports(conn, task_id, running=record.status == "running"),
        "messages": _transcript(record, history),
        "model": record.model,
        "follow_up": _follow_up(record, turns),
        "run": await run_view(
            conn,
            task_id=task_id,
            messages=history,
            running=record.status == "running",
            outcome=record.outcome,
            limit=PARSE_USAGE_LIMITS.tool_calls_limit or 0,
        ),
    }


@router.get("/tasks/{task_id}")
async def task_detail(task_id: str, request: Request, conn: Database) -> dict[str, Any]:
    return await task_payload(
        conn, request.app.state.tasks, task_id, turns=getattr(request.app.state, "turns", None)
    )


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


__all__ = ["TaskOwner", "router", "task_payload"]
