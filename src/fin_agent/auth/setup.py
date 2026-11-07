"""One-time enrollment tokens, stored as SHA-256 hashes.

A token is either `first_run` (create the one user and its first passkey) or `add_device` (add a
passkey for an existing user). There is one live token per kind; issuing a new one retires the old.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fin_agent.db.conn import Connection

FIRST_RUN = "first_run"
ADD_DEVICE = "add_device"
SETUP_TOKEN_TTL = timedelta(hours=24)
ADD_DEVICE_TOKEN_TTL = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    id: str
    token: str
    kind: str
    user_id: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SetupToken:
    id: str
    kind: str
    user_id: str | None
    expires_at: datetime


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


async def setup_required(conn: Connection) -> bool:
    """Setup is open until a user exists with at least one passkey."""
    row = await conn.fetchrow(
        """
        SELECT EXISTS (SELECT 1 FROM users) AS has_user,
               EXISTS (SELECT 1 FROM webauthn_credentials) AS has_credential
        """
    )
    return not (row["has_user"] and row["has_credential"])


async def _insert_token(
    conn: Connection,
    *,
    kind: str,
    user_id: str | None,
    ttl: timedelta,
) -> IssuedToken:
    token = secrets.token_urlsafe(24)
    async with conn.transaction():
        # One live token per kind. The unique index is the backstop.
        await conn.execute(
            "DELETE FROM setup_tokens WHERE kind = $1 AND consumed_at IS NULL",
            kind,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO setup_tokens (token_hash, kind, user_id, expires_at)
            VALUES ($1, $2, $3, now() + $4)
            RETURNING id, expires_at
            """,
            _hash(token),
            kind,
            user_id,
            ttl,
        )
    return IssuedToken(
        id=row["id"],
        token=token,
        kind=kind,
        user_id=user_id,
        expires_at=row["expires_at"],
    )


async def issue_setup_token(conn: Connection, *, ttl_hours: int = 24) -> IssuedToken:
    """Rotate the first-run token and return it with its plaintext, once."""
    return await _insert_token(
        conn,
        kind=FIRST_RUN,
        user_id=None,
        ttl=timedelta(hours=ttl_hours),
    )


async def issue_add_device_token(
    conn: Connection,
    user_id: str,
    *,
    ttl_minutes: int = 10,
) -> IssuedToken:
    """Rotate this user's add-device token. The link can be shown once, then only its hash lives."""
    return await _insert_token(
        conn,
        kind=ADD_DEVICE,
        user_id=user_id,
        ttl=timedelta(minutes=ttl_minutes),
    )


async def ensure_setup_token(conn: Connection, *, ttl_hours: int = 24) -> str | None:
    """Keep a valid first-run token across restarts.

    Returns the plaintext only when a new token had to be issued. If one is already live, returns
    None: its plaintext is gone, so the caller tells the owner to run `fin-agent setup`.
    """
    if not await setup_required(conn):
        return None
    row = await conn.fetchrow(
        """
        SELECT EXISTS (
            SELECT 1 FROM setup_tokens
            WHERE kind = $1 AND consumed_at IS NULL AND expires_at > now()
        ) AS active
        """,
        FIRST_RUN,
    )
    if row["active"]:
        return None
    return (await issue_setup_token(conn, ttl_hours=ttl_hours)).token


async def resolve_setup_token(conn: Connection, token: str) -> SetupToken | None:
    """Find a live token by hash. Every live row is compared to keep the work constant-time."""
    if not token:
        return None
    rows = await conn.fetch(
        """
        SELECT id, token_hash, kind, user_id, expires_at
        FROM setup_tokens
        WHERE consumed_at IS NULL AND expires_at > now()
        """
    )
    candidate = _hash(token)
    found: SetupToken | None = None
    for row in rows:
        if hmac.compare_digest(row["token_hash"], candidate):
            found = SetupToken(
                id=row["id"],
                kind=row["kind"],
                user_id=row["user_id"],
                expires_at=row["expires_at"],
            )
    return found


async def consume_setup_token(conn: Connection, token_id: str) -> None:
    async with conn.transaction():
        await conn.execute(
            "UPDATE setup_tokens SET consumed_at = now() WHERE id = $1 AND consumed_at IS NULL",
            token_id,
        )
