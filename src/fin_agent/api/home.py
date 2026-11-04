"""The Home screen's single composed read: spendable, projection, inbox, and recent activity."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from fin_agent.api.deps import Database, current_session
from fin_agent.ledger import accounts, movements
from fin_agent.notifications import list_notifications
from fin_agent.tools import projection as projection_tools

router = APIRouter(
    prefix="/api",
    tags=["home"],
    dependencies=[Depends(current_session)],
)


@router.get("/home")
def get_home(
    conn: Database,
    horizon_days: int | None = Query(default=None, ge=1, le=3650),
) -> dict[str, Any]:
    try:
        projection = projection_tools.project(conn, horizon_days=horizon_days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    inbox = list_notifications(conn, limit=4)
    return {
        "as_of": projection["as_of"],
        "spendable_paise": accounts.account_summary(conn)["liquid_total_paise"],
        "projection": projection,
        "unread_count": inbox["unread_count"],
        "needs_you": inbox["notifications"],
        "recent": movements.list_movements(conn, limit=5)["items"],
    }
