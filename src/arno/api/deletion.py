"""The deletion surface: what a delete touches, and the Settings-only erase-everything."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from arno.api.deps import Database, FreshSession, current_session
from arno.ledger import deletion
from arno.ledger.errors import ConflictLedgerError

router = APIRouter(prefix="/api", tags=["deletion"], dependencies=[Depends(current_session)])

# Parser task records are files, not rows; the preview lists them with the table entries.
TASK_RECORDS = "parser_task_records"


class EraseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str


def _tasks(request: Request) -> Any:
    return getattr(request.app.state, "tasks", None)


def _task_removal(count: int) -> dict[str, Any]:
    return {"table": TASK_RECORDS, "count": count}


@router.get("/deletion/preview")
async def get_deletion_preview(
    request: Request,
    conn: Database,
    scope: str = Query(default="everything"),
    id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Rows a deletion removes, rows it keeps, and where the data still lives afterwards."""
    payload = await deletion.preview(conn, scope, id)
    tasks = _tasks(request)
    if scope == "everything" and tasks is not None and (count := tasks.saved_count()):
        payload["removes"].append(_task_removal(count))
    return payload


@router.post("/deletion/everything")
async def erase_everything(
    request: Request, conn: Database, fresh: FreshSession, body: EraseRequest
) -> dict[str, Any]:
    """Delete every financial and user row. The typed phrase is the second confirmation."""
    del fresh
    if body.confirm.strip().lower() != deletion.CONFIRM_PHRASE:
        raise HTTPException(status_code=400, detail='Type "delete everything" to confirm.')
    tasks = _tasks(request)
    if tasks is not None and tasks.running():
        raise ConflictLedgerError(
            "A statement import is still running. Wait for it to finish, then delete everything."
        )
    result = await deletion.erase_everything(conn)
    if tasks is not None and (cleared := await tasks.clear_all()):
        result["removes"].append(_task_removal(cleared))
        result["total"] = sum(int(item["count"]) for item in result["removes"])
    return result
