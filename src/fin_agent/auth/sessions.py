"""Opaque session tokens, hashed in the database and sent in an HttpOnly cookie."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from fin_agent.auth.setup import ADD_DEVICE

COOKIE_NAME = "fin_agent_session"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def create_session(
    conn: psycopg.Connection,
    user_id: str,
    *,
    ttl_days: int,
    user_agent: str | None = None,
) -> tuple[str, datetime]:
    """Create a session row and return (plaintext token, expiry). Only the hash is stored."""
    token = new_token()
    expires_at = datetime.now(UTC) + timedelta(days=ttl_days)
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO sessions (user_id, token_hash, user_agent, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, hash_token(token), (user_agent or "")[:500] or None, expires_at),
        )
    return token, expires_at


def session_from_token(conn: psycopg.Connection, token: str) -> dict[str, Any] | None:
    """Return the live session and its user, or None. Touches last_seen_at once every 5 min."""
    if not token:
        return None
    row = conn.execute(
        """
        SELECT s.id AS session_id, s.expires_at, u.id AS user_id, u.display_name
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = %s
          AND s.revoked_at IS NULL
          AND s.expires_at > now()
        """,
        (hash_token(token),),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        """
        UPDATE sessions SET last_seen_at = now()
        WHERE id = %s AND last_seen_at < now() - interval '5 minutes'
        """,
        (row["session_id"],),
    )
    conn.commit()
    return dict(row)


def revoke_session(conn: psycopg.Connection, token: str) -> None:
    with conn.transaction():
        conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
            (hash_token(token),),
        )


def revoke_all_sessions(conn: psycopg.Connection, user_id: str | None = None) -> int:
    """Revoke sessions and the matching add-device links.

    A link created from a session is cancelled when that user's sessions are revoked, so a
    logged-out or reset instance can't still be joined by an old QR code.
    """
    with conn.transaction():
        if user_id is None:
            cur = conn.execute("UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL")
            conn.execute(
                """
                UPDATE setup_tokens SET consumed_at = now()
                WHERE kind = %s AND consumed_at IS NULL
                """,
                (ADD_DEVICE,),
            )
        else:
            cur = conn.execute(
                """
                UPDATE sessions SET revoked_at = now()
                WHERE user_id = %s AND revoked_at IS NULL
                """,
                (user_id,),
            )
            conn.execute(
                """
                UPDATE setup_tokens SET consumed_at = now()
                WHERE kind = %s AND user_id = %s AND consumed_at IS NULL
                """,
                (ADD_DEVICE, user_id),
            )
    return cur.rowcount
