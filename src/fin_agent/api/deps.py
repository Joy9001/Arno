"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from fin_agent.agent.turns import Preparation, PreparationBusy
from fin_agent.auth.sessions import COOKIE_NAME, is_fresh, session_from_token
from fin_agent.db.conn import Connection, connect


async def db() -> AsyncIterator[Connection]:
    """One connection per request. Session state lives in Postgres, not in the pool."""
    async with connect() as conn:
        yield conn


Database = Annotated[Connection, Depends(db)]


async def current_session(request: Request, conn: Database) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME, "")
    session = await session_from_token(conn, token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with your passkey.",
        )
    return session


Session = Annotated[dict[str, Any], Depends(current_session)]


async def turn_preparation(request: Request, session: Session) -> AsyncIterator[Preparation]:
    """Hold a thread's preparation guard until the request has registered its turn.

    The exit runs at function scope, before a streaming reply is sent, so a duplicate or a
    reattach does not wait behind the response. A request without a chat id is independent.
    """
    thread_id = request.path_params.get("thread_id")
    if not thread_id:
        try:
            body = await request.json()
        except ValueError:
            body = None
        value = body.get("id") if isinstance(body, dict) else None
        thread_id = str(value) if value else None
    guard = request.app.state.turns.prepare(thread_id)
    try:
        await guard.acquire()
    except PreparationBusy as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    try:
        yield guard
    finally:
        guard.release()


PreparationDep = Annotated[Preparation, Depends(turn_preparation, scope="function")]


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
