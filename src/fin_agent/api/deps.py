"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from typing import Annotated, Any

import psycopg
from fastapi import Depends, HTTPException, Request, status

from fin_agent.auth.sessions import COOKIE_NAME, is_fresh, session_from_token
from fin_agent.db.conn import connect


def db() -> Iterator[psycopg.Connection]:
    """One connection per request. Session state lives in Postgres, not in the pool."""
    with closing(connect()) as conn:
        yield conn


Database = Annotated[psycopg.Connection, Depends(db)]


def current_session(request: Request, conn: Database) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME, "")
    session = session_from_token(conn, token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with your passkey.",
        )
    return session


Session = Annotated[dict[str, Any], Depends(current_session)]


def fresh_session(session: Session) -> dict[str, Any]:
    """A session that has re-authenticated with a passkey in the last five minutes."""
    if not is_fresh(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Confirm with your passkey to continue.",
            headers={"X-Fin-Agent-Step-Up": "passkey"},
        )
    return session


FreshSession = Annotated[dict[str, Any], Depends(fresh_session)]
