"""Passkey auth: enrollment links, login, sessions, and passkey management."""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from fin_agent.api.deps import Database, Session
from fin_agent.api.problems import problem
from fin_agent.auth import passkeys
from fin_agent.auth.enrollment import enrollment_link
from fin_agent.auth.sessions import (
    COOKIE_NAME,
    create_session,
    revoke_all_sessions,
    revoke_session,
    session_from_token,
)
from fin_agent.auth.setup import (
    ADD_DEVICE,
    FIRST_RUN,
    issue_add_device_token,
    resolve_setup_token,
    setup_required,
)
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


class RenamePasskeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


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


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


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
        "rp_origin": _settings(request).rp_origin,
    }


@router.post("/setup/begin")
def setup_begin(body: SetupBeginBody, request: Request, conn: Database) -> Any:
    settings = _settings(request)
    token = resolve_setup_token(conn, body.token)
    if token is None:
        return problem(401, detail="That enrollment link is wrong, expired, or already used.")

    setup_open = setup_required(conn)
    if token.kind == FIRST_RUN:
        if not setup_open:
            return problem(
                409,
                detail=(
                    "Setup is already complete. Open Settings → Passkeys on a signed-in device "
                    "to add this one."
                ),
            )
        user = conn.execute(
            "SELECT id, webauthn_user_handle FROM users ORDER BY created_at LIMIT 1"
        ).fetchone()
    elif token.kind == ADD_DEVICE:
        if setup_open:
            return problem(409, detail="First-run setup is not complete yet.")
        if token.user_id is None:
            return problem(401, detail="That enrollment link is not valid.")
        user = conn.execute(
            "SELECT id, webauthn_user_handle FROM users WHERE id = %s",
            (token.user_id,),
        ).fetchone()
        if user is None:
            return problem(
                401,
                detail="That enrollment link belongs to a user that no longer exists.",
            )
    else:
        return problem(401, detail="That enrollment link is not valid.")

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
            setup_token_id=token.id,
        )
    except psycopg.Error:
        logger.exception("could not start passkey registration")
        return problem(503, detail="The database is unreachable.")
    return {
        "challenge_id": challenge_id,
        "options": options,
        "kind": token.kind,
    }


@router.post("/setup/verify")
def setup_verify(body: VerifyBody, request: Request, response: Response, conn: Database) -> Any:
    settings = _settings(request)
    try:
        user, kind = passkeys.finish_registration(
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
    logger.info("passkey registered for user %s (%s)", user["id"], kind)
    return {"user": _user_payload(user), "kind": kind}


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
def session(session: Session) -> dict[str, Any]:
    return {"authenticated": True, "user": _user_payload(session)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, conn: Database) -> Response:
    revoke_session(conn, request.cookies.get(COOKIE_NAME, ""))
    _clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(response: Response, session: Session, conn: Database) -> Response:
    """Sign out every device and cancel any outstanding add-device link."""
    revoke_all_sessions(conn, session["user_id"])
    _clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/passkeys")
def list_passkeys(session: Session, conn: Database) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id, name, created_at, last_used_at
        FROM webauthn_credentials
        WHERE user_id = %s
        ORDER BY created_at
        """,
        (session["user_id"],),
    ).fetchall()
    return {"passkeys": [dict(row) for row in rows]}


@router.patch("/passkeys/{passkey_id}")
def rename_passkey(
    passkey_id: str,
    body: RenamePasskeyBody,
    session: Session,
    conn: Database,
) -> Any:
    name = body.name.strip()
    if not name:
        return problem(422, detail="A passkey needs a name.")
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE webauthn_credentials SET name = %s
            WHERE id = %s AND user_id = %s
            RETURNING id, name, created_at, last_used_at
            """,
            (name, passkey_id, session["user_id"]),
        ).fetchone()
    if row is None:
        return problem(404, detail="That passkey is not registered on this instance.")
    return {"passkey": dict(row)}


@router.delete("/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_passkey(passkey_id: str, session: Session, conn: Database) -> Response:
    row = conn.execute(
        "SELECT id FROM webauthn_credentials WHERE id = %s AND user_id = %s",
        (passkey_id, session["user_id"]),
    ).fetchone()
    if row is None:
        return problem(404, detail="That passkey is not registered on this instance.")
    count = conn.execute(
        "SELECT count(*) AS n FROM webauthn_credentials WHERE user_id = %s",
        (session["user_id"],),
    ).fetchone()["n"]
    if count <= 1:
        return problem(
            409,
            detail="That is the last passkey. Add another device before deleting it.",
        )
    with conn.transaction():
        conn.execute(
            "DELETE FROM webauthn_credentials WHERE id = %s AND user_id = %s",
            (passkey_id, session["user_id"]),
        )
    # The count above opened a read transaction; commit the delete instead of letting close()
    # roll it back.
    conn.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/enrollments")
def create_enrollment(request: Request, session: Session, conn: Database) -> dict[str, Any]:
    """Create a 10-minute, single-use link that adds a passkey for the signed-in user."""
    settings = _settings(request)
    issued = issue_add_device_token(conn, session["user_id"], ttl_minutes=10)
    return {
        "id": issued.id,
        "link": enrollment_link(settings.rp_origin, issued.token),
        "expires_at": issued.expires_at,
        "kind": ADD_DEVICE,
    }
