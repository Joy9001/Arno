"""The deletion surface: what a delete touches, and the Settings-only erase-everything."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from arno.api.deps import Database, current_session
from arno.ledger import deletion

router = APIRouter(prefix="/api", tags=["deletion"], dependencies=[Depends(current_session)])


class EraseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str


@router.get("/deletion/preview")
async def get_deletion_preview(
    conn: Database,
    scope: str = Query(default="everything"),
    id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Rows a deletion removes, rows it keeps, and where the data still lives afterwards."""
    return await deletion.preview(conn, scope, id)


@router.post("/deletion/everything")
async def erase_everything(conn: Database, body: EraseRequest) -> dict[str, Any]:
    """Delete every financial and user row. The typed phrase is the second confirmation."""
    if body.confirm.strip().lower() != deletion.CONFIRM_PHRASE:
        raise HTTPException(status_code=400, detail='Type "delete everything" to confirm.')
    return await deletion.erase_everything(conn)
