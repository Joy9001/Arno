"""Database connections. One connection per use, no pool: see platform.md."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from fin_agent.config import get_settings


def connect(dsn: str | None = None, *, role: str | None = None) -> psycopg.Connection:
    """Open a connection with dict rows. Callers own the connection's lifetime."""
    settings = get_settings()
    conn = psycopg.connect(dsn or settings.database_url, row_factory=dict_row, connect_timeout=10)
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
