"""`GET /api/health`: the one public endpoint. No amounts, no identifiers."""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from fastapi import APIRouter, Request, Response

from fin_agent.api.problems import problem
from fin_agent.db.conn import connect
from fin_agent.db.migrate import current_version

logger = logging.getLogger(__name__)

router = APIRouter(tags=["instance"])


@router.get("/api/health", response_model=None)
def health(request: Request, response: Response) -> Any:
    try:
        with connect(request.app.state.settings.database_url) as conn:
            conn.execute("SELECT 1")
            schema_version = current_version(conn)
    except psycopg.Error as exc:
        logger.warning("health check: database unreachable (%s)", type(exc).__name__)
        return problem(503, detail="The database is unreachable.")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "database": "ok", "schema_version": schema_version}
