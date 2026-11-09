"""Recaps: the period list, one period's stories, the rows behind a figure, and seen marks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from fin_agent.api.deps import Database, current_session
from fin_agent.tools import recaps

router = APIRouter(prefix="/api", tags=["recaps"], dependencies=[Depends(current_session)])


@router.get("/recaps")
async def get_recaps(conn: Database) -> dict[str, Any]:
    return await recaps.recap_list(conn)


@router.get("/recaps/unseen")
async def get_recaps_unseen(conn: Database) -> dict[str, int]:
    return {"unseen": await recaps.recap_unseen(conn)}


@router.get("/recaps/{period_id}")
async def get_recap(conn: Database, period_id: str) -> dict[str, Any]:
    try:
        return await recaps.recap(conn, period_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/recaps/{period_id}/rows")
async def get_recap_rows(
    conn: Database,
    period_id: str,
    story: str = Query(max_length=20),
    key: str | None = Query(default=None, max_length=300),
) -> dict[str, Any]:
    try:
        return await recaps.recap_rows(conn, period_id, story=story, key=key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/recaps/{period_id}/seen")
async def post_recap_seen(conn: Database, period_id: str) -> dict[str, Any]:
    try:
        await recaps.mark_seen(conn, period_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"period_id": period_id, "seen": True}


__all__ = ["router"]
