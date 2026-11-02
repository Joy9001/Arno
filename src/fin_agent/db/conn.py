"""Database connections. One connection per use, no pool: see platform.md."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from fin_agent.config import get_settings

# An eval trial points every connection in its task at its own cloned database. A context
# variable, rather than an environment variable, keeps concurrent trials apart: each case
# lifecycle sets it in its own task and evaluators still see it.
_DATABASE_URL_OVERRIDE: ContextVar[str | None] = ContextVar(
    "fin_agent_database_url_override", default=None
)


def use_database_url(dsn: str) -> Token[str | None]:
    """Point `connect()` at a trial database for the current task. Reset with the token."""
    return _DATABASE_URL_OVERRIDE.set(dsn)


def reset_database_url(token: Token[str | None]) -> None:
    _DATABASE_URL_OVERRIDE.reset(token)


def current_database_url() -> str:
    return _DATABASE_URL_OVERRIDE.get() or get_settings().database_url


def connect(dsn: str | None = None, *, role: str | None = None) -> psycopg.Connection:
    """Open a connection with dict rows. Callers own the connection's lifetime."""
    conn = psycopg.connect(dsn or current_database_url(), row_factory=dict_row, connect_timeout=10)
    if role is not None:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
    return conn


@contextmanager
def connection(dsn: str | None = None) -> Iterator[psycopg.Connection]:
    conn = connect(dsn)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
