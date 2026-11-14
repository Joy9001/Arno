"""Boot-time database tasks that are not schema migrations."""

from __future__ import annotations

import logging

from arno.db.conn import Connection, quote_ident, quote_literal

logger = logging.getLogger(__name__)


async def _set_role_password(conn: Connection, role: str, password: str | None) -> None:
    """Set a login role's password from the environment.

    The role has no password until this runs, so it cannot log in by accident. A missing
    environment value is not fatal: the boundary role simply cannot connect.
    """
    if not password:
        logger.info("%s password is not set; the role has no password", role)
        return
    async with conn.transaction():
        await conn.execute(
            f"ALTER ROLE {quote_ident(role)} WITH LOGIN PASSWORD {quote_literal(password)}"
        )


async def set_agent_ro_password(conn: Connection, password: str | None) -> None:
    await _set_role_password(conn, "agent_ro", password)


async def set_backup_password(conn: Connection, password: str | None) -> None:
    await _set_role_password(conn, "backup", password)


async def warn_if_superuser(conn: Connection) -> bool:
    """Log one warning at boot if the connected role is still a Postgres superuser."""
    row = await conn.fetchrow_required(
        "SELECT current_user AS role, rolsuper FROM pg_roles WHERE rolname = current_user"
    )
    if bool(row["rolsuper"]):
        logger.warning(
            "Database role %r is a PostgreSQL superuser; run deploy/demote-app.sh to convert "
            "it to a normal owner role.",
            row["role"],
        )
        return True
    return False
