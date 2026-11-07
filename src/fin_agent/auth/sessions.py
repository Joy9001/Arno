"""Opaque session tokens, hashed in the database and sent in an HttpOnly cookie."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fin_agent.auth.setup import ADD_DEVICE
from fin_agent.db.conn import Connection

COOKIE_NAME = "fin_agent_session"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


async def create_session(
    conn: Connection,
    user_id: str,
    *,
    ttl_days: int,
    user_agent: str | None = None,
) -> tuple[str, datetime]:
    """Create a session row and return (plaintext token, expiry). Only the hash is stored."""
    token = new_token()
    expires_at = datetime.now(UTC) + timedelta(days=ttl_days)
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO sessions (user_id, token_hash, user_agent, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            user_id,
            hash_token(token),
            (user_agent or "")[:500] or None,
            expires_at,
        )
    return token, expires_at


async def session_from_token(conn: Connection, token: str) -> dict[str, Any] | None:
    """Return the live session and its user, or None. Touches last_seen_at once every 5 min."""
    if not token:
        return None
    row = await conn.fetchrow(
        """
        SELECT s.id AS session_id, s.expires_at, s.reverified_at, u.id AS user_id, u.display_name
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = $1
          AND s.revoked_at IS NULL
          AND s.expires_at > now()
        """,
        hash_token(token),
    )
    if row is None:
        return None
    await conn.execute(
        """
        UPDATE sessions SET last_seen_at = now()
        WHERE id = $1 AND last_seen_at < now() - interval '5 minutes'
        """,
        row["session_id"],
    )
    return row


async def revoke_session(conn: Connection, token: str) -> None:
    async with conn.transaction():
        await conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL",
            hash_token(token),
        )


async def mark_reverified(conn: Connection, session_id: str) -> None:
    """Record a fresh passkey check for this session. Export and passkey changes read it."""
    async with conn.transaction():
        await conn.execute("UPDATE sessions SET reverified_at = now() WHERE id = $1", session_id)


REVERIFY_TTL_MINUTES = 5


def is_fresh(session: dict[str, Any]) -> bool:
    value = session.get("reverified_at")
    if value is None:
        return False
    age = datetime.now(UTC) - value
    return timedelta(0) <= age <= timedelta(minutes=REVERIFY_TTL_MINUTES)


async def revoke_all_sessions(conn: Connection, user_id: str | None = None) -> int:
    """Revoke sessions and the matching add-device links.

    A link created from a session is cancelled when that user's sessions are revoked, so a
    logged-out or reset instance can't still be joined by an old QR code.
    """
    async with conn.transaction():
        if user_id is None:
            status = await conn.execute(
                "UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL"
            )
            await conn.execute(
                """
                UPDATE setup_tokens SET consumed_at = now()
                WHERE kind = $1 AND consumed_at IS NULL
                """,
                ADD_DEVICE,
            )
        else:
            status = await conn.execute(
                """
                UPDATE sessions SET revoked_at = now()
                WHERE user_id = $1 AND revoked_at IS NULL
                """,
                user_id,
            )
            await conn.execute(
                """
                UPDATE setup_tokens SET consumed_at = now()
                WHERE kind = $1 AND user_id = $2 AND consumed_at IS NULL
                """,
                ADD_DEVICE,
                user_id,
            )
    return int(status.split()[-1])
