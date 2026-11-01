"""Boot-time database tasks that are not schema migrations."""

from __future__ import annotations

import logging

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)


def set_role_password(conn: psycopg.Connection, role: str, password: str | None) -> None:
    """Set a login role's password from the environment.

    The role has no password until this runs, so it cannot log in by accident. A missing
    environment value is not fatal: the boundary role simply cannot connect.
    """
    if not password:
        logger.info("%s password is not set; the role has no password", role)
        return
    with conn.transaction():
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )


def set_agent_ro_password(conn: psycopg.Connection, password: str | None) -> None:
    set_role_password(conn, "agent_ro", password)


def set_backup_password(conn: psycopg.Connection, password: str | None) -> None:
    set_role_password(conn, "backup", password)
