"""Shared FastAPI dependencies."""

from __future__ import annotations

import hmac
import time
from collections.abc import AsyncIterator
from ipaddress import ip_address, ip_network
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from arno.agent.turns import Preparation, PreparationBusy
from arno.db.conn import Connection, connect

COOKIE_NAME = "arno_session"


async def db() -> AsyncIterator[Connection]:
    """One connection per request. Session state lives in Postgres, not in the pool."""
    async with connect() as conn:
        yield conn


Database = Annotated[Connection, Depends(db)]


def _signature(passphrase: str, expires: str) -> str:
    return hmac.new(passphrase.encode(), f"arno-session:{expires}".encode(), "sha256").hexdigest()


def sign_session(passphrase: str, ttl_days: int) -> str:
    expires = str(int(time.time()) + ttl_days * 86400)
    return f"{expires}.{_signature(passphrase, expires)}"


def _cookie_valid(passphrase: str, token: str) -> bool:
    expires, _, mac = token.partition(".")
    return (
        expires.isdigit()
        and hmac.compare_digest(mac.encode(), _signature(passphrase, expires).encode())
        and int(expires) > time.time()
    )


def refuse_hostile(request: Request) -> None:
    """Refuse requests the owner's browser did not make and requests from the sandbox network."""
    subnet = request.app.state.settings.sandbox_subnet
    sandboxed = False
    if subnet and request.client:
        try:
            sandboxed = ip_address(request.client.host) in ip_network(subnet)
        except ValueError:
            pass  # not an IP (the test client): outside the subnet
    # Tailscale Serve adds the identity header to any request from the owner's browser, so a
    # hostile page's request carries it too; the browser's own metadata gives that away.
    fetch_site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    forged = fetch_site in {"cross-site", "same-site"} or (
        fetch_site is None
        and origin is not None
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and urlsplit(origin).netloc != request.headers.get("host")
    )
    if sandboxed or forged:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Request refused.")


def identify(request: Request) -> dict[str, str | None] | None:
    settings = request.app.state.settings
    login = request.headers.get("tailscale-user-login", "").strip().lower()
    if settings.owner_email and login == settings.owner_email:
        return {"method": "tailscale", "login": login}
    token = request.cookies.get(COOKIE_NAME, "")
    if settings.passphrase and _cookie_valid(settings.passphrase, token):
        return {"method": "passphrase", "login": None}
    return None


async def current_session(request: Request) -> dict[str, str | None]:
    refuse_hostile(request)
    settings = request.app.state.settings
    if not settings.owner_email and not settings.passphrase:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "No sign-in is configured. Set ARNO_OWNER_EMAIL or ARNO_PASSPHRASE.",
        )
    session = identify(request)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    return session


Session = Annotated[dict[str, str | None], Depends(current_session)]


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
