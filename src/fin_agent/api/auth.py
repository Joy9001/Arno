"""Passkey auth: first-run setup, login, sessions, and logout."""

from __future__ import annotations

import logging
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from fin_agent.api.deps import Database, current_session
from fin_agent.api.problems import problem
from fin_agent.auth import passkeys
from fin_agent.auth.sessions import COOKIE_NAME, create_session, revoke_session, session_from_token
from fin_agent.auth.setup import setup_required, verify_setup_token
from fin_agent.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SetupBeginBody(BaseModel):
    token: str = Field(min_length=8, max_length=200)


class LoginVerifyBody(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=100)
    credential: dict[str, Any]


class VerifyBody(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=100)
    credential: dict[str, Any]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _set_session_cookie(response: Response, token: str, ttl_days: int, secure: bool) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def _user_payload(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user.get("id") or user["user_id"],
        "display_name": user["display_name"],
    }


@router.get("/state")
def auth_state(request: Request, conn: Database) -> dict[str, Any]:
    session = session_from_token(conn, request.cookies.get(COOKIE_NAME, ""))
    return {
        "setup_required": setup_required(conn),
        "authenticated": session is not None,
        "user": _user_payload(session) if session else None,
    }


@router.post("/setup/begin")
def setup_begin(body: SetupBeginBody, request: Request, conn: Database) -> dict[str, Any]:
    settings = _settings(request)
    if not verify_setup_token(conn, body.token):
        return problem(401, detail="That setup token is wrong or has expired.")

    user = conn.execute(
        "SELECT id, webauthn_user_handle FROM users ORDER BY created_at LIMIT 1"
    ).fetchone()
    credential_ids: list[bytes] = []
    if user is not None:
        credential_ids = [
            row["credential_id"]
            for row in conn.execute(
                "SELECT credential_id FROM webauthn_credentials WHERE user_id = %s",
                (user["id"],),
            ).fetchall()
        ]
        handle = user["webauthn_user_handle"]
    else:
        handle = passkeys.new_user_handle()

    try:
        challenge_id, options = passkeys.begin_registration(
            conn,
            rp_id=settings.rp_id,
            rp_name=settings.rp_name,
            user_handle=handle,
            user_id=user["id"] if user else None,
            exclude_credential_ids=credential_ids,
        )
    except psycopg.Error:
        logger.exception("could not start passkey registration")
        return problem(503, detail="The database is unreachable.")
    return {"challenge_id": challenge_id, "options": options}


@router.post("/setup/verify")
def setup_verify(body: VerifyBody, request: Request, response: Response, conn: Database) -> Any:
    settings = _settings(request)
    try:
        user = passkeys.finish_registration(
            conn,
            rp_id=settings.rp_id,
            rp_origin=settings.rp_origin,
            challenge_id=body.challenge_id,
            credential=body.credential,
        )
    except passkeys.PasskeyError as exc:
        return problem(400, detail=str(exc))

    token, _ = create_session(
        conn,
        user["id"],
        ttl_days=settings.session_ttl_days,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, settings.session_ttl_days, settings.cookie_secure)
    logger.info("setup completed for user %s", user["id"])
    return {"user": _user_payload(user)}


@router.post("/login/begin")
def login_begin(request: Request, conn: Database) -> Any:
    settings = _settings(request)
    if setup_required(conn):
        return problem(409, detail="Setup is not complete. Open the setup page first.")
    user = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
    credential_ids = [
        row["credential_id"]
        for row in conn.execute(
            "SELECT credential_id FROM webauthn_credentials WHERE user_id = %s",
            (user["id"],),
        ).fetchall()
    ]
    try:
        challenge_id, options = passkeys.begin_authentication(
            conn,
            rp_id=settings.rp_id,
            user_id=user["id"],
            credential_ids=credential_ids,
        )
    except psycopg.Error:
        logger.exception("could not start passkey login")
        return problem(503, detail="The database is unreachable.")
    return {"challenge_id": challenge_id, "options": options}


@router.post("/login/verify")
def login_verify(
    body: LoginVerifyBody, request: Request, response: Response, conn: Database
) -> Any:
    settings = _settings(request)
    try:
        user = passkeys.finish_authentication(
            conn,
            rp_id=settings.rp_id,
            rp_origin=settings.rp_origin,
            challenge_id=body.challenge_id,
            credential=body.credential,
        )
    except passkeys.PasskeyError as exc:
        return problem(401, detail=str(exc))

    token, _ = create_session(
        conn,
        user["id"],
        ttl_days=settings.session_ttl_days,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, settings.session_ttl_days, settings.cookie_secure)
    return {"user": _user_payload(user)}


@router.get("/session")
def session(session: Annotated[dict[str, Any], Depends(current_session)]) -> dict[str, Any]:
    return {"authenticated": True, "user": _user_payload(session)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, conn: Database) -> Response:
    revoke_session(conn, request.cookies.get(COOKIE_NAME, ""))
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
