"""The read surface over the audit log: what changed, and what undo would say about it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from arno.api.deps import Database, current_session
from arno.ledger.audit import DEFAULT_LIMIT, MAX_LIMIT, list_history

router = APIRouter(
    prefix="/api",
    tags=["history"],
    dependencies=[Depends(current_session)],
)


@router.get("/history")
async def get_history(
    conn: Database,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> dict[str, Any]:
    return await list_history(conn, limit=limit, cursor=cursor)
