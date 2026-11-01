"""Boot-time database tasks that are not schema migrations."""

from __future__ import annotations

import logging

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)


def set_agent_ro_password(conn: psycopg.Connection, password: str | None) -> None:
    """Set `agent_ro`'s password from the environment.

    The role has no password until this runs, so it cannot log in by accident. A missing
    AGENT_RO_PASSWORD is not fatal at L0: nothing connects as `agent_ro` until the read tools
    land at L2.
    """
    if not password:
        logger.info("AGENT_RO_PASSWORD is not set; agent_ro has no password")
        return
    with conn.transaction():
        conn.execute(sql.SQL("ALTER ROLE agent_ro WITH PASSWORD {}").format(sql.Literal(password)))
