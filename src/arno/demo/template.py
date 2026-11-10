"""Create a template database from a seeded demo database.

Postgres `CREATE DATABASE ... TEMPLATE ...` gives every eval trial a fresh clone with no reset
code. Only database names are interpolated, and they are validated as plain identifiers.
"""

from __future__ import annotations

import re

from arno.db.conn import connect, conninfo_to_dict, make_conninfo, quote_ident

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def database_dsn(base_dsn: str, name: str) -> str:
    """The same server and credentials, with another database name."""
    if not NAME_RE.match(name):
        raise ValueError("Database names must be lower-case letters, digits and underscores")
    info = conninfo_to_dict(base_dsn)
    info["dbname"] = name
    return make_conninfo(**info)


def _admin_dsn(admin_dsn: str) -> str:
    info = conninfo_to_dict(admin_dsn)
    info["dbname"] = "postgres"
    return make_conninfo(**info)


async def create_database(admin_dsn: str, target: str) -> None:
    if not NAME_RE.match(target):
        raise ValueError("Database names must be lower-case letters, digits and underscores")
    async with connect(_admin_dsn(admin_dsn)) as conn:
        await conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(target)} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {quote_ident(target)}")


async def drop_database(admin_dsn: str, target: str) -> None:
    if not NAME_RE.match(target):
        raise ValueError("Database names must be lower-case letters, digits and underscores")
    async with connect(_admin_dsn(admin_dsn)) as conn:
        await conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(target)} WITH (FORCE)")


async def clone_database(
    admin_dsn: str,
    *,
    source: str,
    target: str,
    terminate: bool = True,
) -> None:
    if not NAME_RE.match(source) or not NAME_RE.match(target):
        raise ValueError("Database names must be lower-case letters, digits and underscores")
    async with connect(_admin_dsn(admin_dsn)) as conn:
        if terminate:
            await conn.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = $1 AND pid <> pg_backend_pid()
                """,
                source,
            )
        await conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(target)} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {quote_ident(target)} TEMPLATE {quote_ident(source)}")
