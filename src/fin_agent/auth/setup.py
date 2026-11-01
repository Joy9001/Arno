"""First-run setup tokens: one active token at a time, stored as a SHA-256 hash."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

import psycopg

SETUP_TOKEN_TTL = timedelta(hours=24)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def setup_required(conn: psycopg.Connection) -> bool:
    """Setup is open until a user exists with at least one passkey."""
    row = conn.execute(
        """
        SELECT EXISTS (SELECT 1 FROM users) AS has_user,
               EXISTS (SELECT 1 FROM webauthn_credentials) AS has_credential
        """
    ).fetchone()
    return not (row["has_user"] and row["has_credential"])


def issue_setup_token(conn: psycopg.Connection, *, ttl_hours: int = 24) -> str:
    """Replace any existing setup token and return the new plaintext token."""
    token = secrets.token_urlsafe(24)
    with conn.transaction():
        conn.execute("DELETE FROM setup_tokens")
        conn.execute(
            """
            INSERT INTO setup_tokens (token_hash, expires_at)
            VALUES (%s, now() + make_interval(hours => %s))
            """,
            (_hash(token), ttl_hours),
        )
    # A caller may already have an open read transaction; commit so the token isn't rolled back
    # when a short-lived CLI process closes the connection.
    conn.commit()
    return token


def verify_setup_token(conn: psycopg.Connection, token: str) -> bool:
    if not token:
        return False
    rows = conn.execute(
        """
        SELECT token_hash FROM setup_tokens
        WHERE consumed_at IS NULL AND expires_at > now()
        """
    ).fetchall()
    candidate = _hash(token)
    return any(hmac.compare_digest(row["token_hash"], candidate) for row in rows)


def consume_setup_tokens(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute("UPDATE setup_tokens SET consumed_at = now() WHERE consumed_at IS NULL")
