"""The Home screen's single composed read: spendable, projection, inbox, and recent activity."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from fin_agent.api.deps import Database, current_session
from fin_agent.api.notification_query import count_unread, unread_first
from fin_agent.ledger import accounts, movements
from fin_agent.tools import projection as projection_tools

router = APIRouter(
    prefix="/api",
    tags=["home"],
    dependencies=[Depends(current_session)],
)


@router.get("/home")
async def get_home(
    conn: Database,
    horizon_days: int | None = Query(default=None, ge=1, le=3650),
) -> dict[str, Any]:
    try:
        projection = await projection_tools.project(conn, horizon_days=horizon_days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    summary = await accounts.account_summary(conn)
    unread = await count_unread(conn)
    needs_you = await unread_first(conn, limit=4)
    recent = await movements.list_movements(conn, limit=5)
    return {
        "as_of": projection["as_of"],
        "spendable_paise": summary["liquid_total_paise"],
        "cash_quality": summary["cash_quality"],
        "projection": projection,
        "unread_count": unread,
        "needs_you": needs_you,
        "recent": recent["items"],
    }
