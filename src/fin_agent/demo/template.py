"""Create a template database from a seeded demo database.

Postgres `CREATE DATABASE ... TEMPLATE ...` gives every eval trial a fresh clone with no reset
code. Only database names are interpolated, and they are validated as plain identifiers.
"""

from __future__ import annotations

import re

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def database_dsn(base_dsn: str, name: str) -> str:
    """The same server and credentials, with another database name."""
    if not NAME_RE.match(name):
        raise ValueError("Database names must be lower-case letters, digits and underscores")
    info = conninfo_to_dict(base_dsn)
    info["dbname"] = name
    return make_conninfo(**info)


def _admin_connection(admin_dsn: str) -> psycopg.Connection:
    info = conninfo_to_dict(admin_dsn)
    info["dbname"] = "postgres"
    return psycopg.connect(make_conninfo(**info), autocommit=True)


def create_database(admin_dsn: str, target: str) -> None:
    if not NAME_RE.match(target):
        raise ValueError("Database names must be lower-case letters, digits and underscores")
    with _admin_connection(admin_dsn) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(target))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))


def drop_database(admin_dsn: str, target: str) -> None:
    if not NAME_RE.match(target):
        raise ValueError("Database names must be lower-case letters, digits and underscores")
    with _admin_connection(admin_dsn) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(target))
        )


def clone_database(
    admin_dsn: str,
    *,
    source: str,
    target: str,
    terminate: bool = True,
) -> None:
    if not NAME_RE.match(source) or not NAME_RE.match(target):
        raise ValueError("Database names must be lower-case letters, digits and underscores")
    with _admin_connection(admin_dsn) as conn:
        if terminate:
            conn.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (source,),
            )
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(target))
        )
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(target), sql.Identifier(source)
            )
        )
