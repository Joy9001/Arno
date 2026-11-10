"""Database connections. One connection per use, no pool: see platform.md."""

from __future__ import annotations

import json
import shlex
from contextvars import ContextVar, Token
from typing import Any, Literal, override
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import asyncpg

from fin_agent.config import Settings, get_settings

Role = Literal["app", "agent_ro", "backup"]

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


def quote_ident(name: str) -> str:
    """Quote a PostgreSQL identifier."""
    if "\x00" in name:
        raise ValueError("identifier contains a NUL byte")
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Quote a PostgreSQL string literal."""
    if "\x00" in value:
        raise ValueError("literal contains a NUL byte")
    if "\\" in value:
        return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    return "'" + value.replace("'", "''") + "'"


def conninfo_to_dict(dsn: str) -> dict[str, str]:
    """Parse a PostgreSQL URI or keyword=value string into a dict."""
    if "://" in dsn:
        parsed = urlparse(dsn)
        if parsed.scheme not in {"postgresql", "postgres"}:
            raise ValueError(f"Invalid PostgreSQL scheme: {parsed.scheme}")
        info: dict[str, str] = {}
        if parsed.username is not None:
            info["user"] = unquote(parsed.username)
        if parsed.password is not None:
            info["password"] = unquote(parsed.password)
        if parsed.hostname:
            info["host"] = parsed.hostname
        if parsed.port:
            info["port"] = str(parsed.port)
        if parsed.path and parsed.path != "/":
            info["dbname"] = unquote(parsed.path.lstrip("/"))
        for key, values in parse_qs(parsed.query).items():
            if values:
                info[key] = values[-1]
        return info

    info = {}
    for token in shlex.split(dsn):
        if "=" not in token:
            raise ValueError(f"Invalid connection string token: {token!r}")
        key, value = token.split("=", 1)
        info[key] = value
    return info


def make_conninfo(**kwargs: Any) -> str:
    """Build a PostgreSQL URI from connection parameters."""
    info = {key: value for key, value in kwargs.items() if value is not None and value != ""}
    user = info.pop("user", None)
    password = info.pop("password", None)
    host = str(info.pop("host", "localhost"))
    port = info.pop("port", None)
    dbname = info.pop("dbname", None)

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    auth = ""
    if user is not None:
        auth = quote(str(user), safe="")
        if password is not None:
            auth += ":" + quote(str(password), safe="")
        auth += "@"

    netloc = auth + host
    if port is not None:
        netloc += f":{port}"

    path = f"/{quote(str(dbname), safe='')}" if dbname is not None else ""
    query = urlencode(info)
    return f"postgresql://{netloc}{path}" + (f"?{query}" if query else "")


class Connection(asyncpg.Connection):
    """An asyncpg connection that returns dict rows and closes with `async with`."""

    async def __aenter__(self) -> Connection:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    @override
    async def fetch(
        self,
        query: str,
        *args: Any,
        timeout: float | None = None,
        record_class: Any = None,
    ) -> list[dict[str, Any]]:
        records = await super().fetch(query, *args, timeout=timeout, record_class=record_class)
        return [dict(record) for record in records]

    @override
    async def fetchrow(
        self,
        query: str,
        *args: Any,
        timeout: float | None = None,
        record_class: Any = None,
    ) -> dict[str, Any] | None:
        record = await super().fetchrow(query, *args, timeout=timeout, record_class=record_class)
        return dict(record) if record is not None else None

    async def fetchrow_required(
        self, query: str, *args: Any, timeout: float | None = None
    ) -> dict[str, Any]:
        """`fetchrow` for SQL that always yields a row: `INSERT ... RETURNING`, an aggregate with
        no `GROUP BY`, or a row this transaction already locked. A row the caller's input may not
        match is a user-facing case; use `fetchrow` and handle `None` there instead."""
        row = await self.fetchrow(query, *args, timeout=timeout)
        if row is None:
            raise LookupError("query that always returns a row returned none")
        return row


def role_dsn(base_dsn: str, role: Role, settings: Settings | None = None) -> str:
    """Return the DSN for one of the database roles."""
    if role == "app":
        return base_dsn
    settings = settings or get_settings()
    info = conninfo_to_dict(base_dsn)
    info["user"] = role
    password = settings.agent_ro_password if role == "agent_ro" else settings.backup_password
    if password:
        info["password"] = password
    else:
        info.pop("password", None)
    return make_conninfo(**info)


def _encode_json(value: Any) -> str:
    """Encode a JSON/JSONB value, leaving one already serialized alone."""
    return value if isinstance(value, str) else json.dumps(value)


async def _open_connection(
    dsn: str | None = None,
    *,
    role: Role | None = None,
    connect_timeout: int = 10,
) -> Connection:
    target = dsn or current_database_url()
    if role is not None:
        target = role_dsn(target, role)
    conn: Connection = await asyncpg.connect(
        target, timeout=connect_timeout, connection_class=Connection
    )
    await conn.set_type_codec(
        "jsonb",
        encoder=_encode_json,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )
    await conn.set_type_codec(
        "json",
        encoder=_encode_json,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )
    return conn


class _ConnectContext:
    """A connection factory usable with both `await` and `async with`."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        role: Role | None = None,
        connect_timeout: int = 10,
    ) -> None:
        self._dsn = dsn
        self._role = role
        self._connect_timeout = connect_timeout
        self._conn: Connection | None = None

    def __await__(self) -> Any:
        return _open_connection(
            self._dsn, role=self._role, connect_timeout=self._connect_timeout
        ).__await__()

    async def __aenter__(self) -> Connection:
        self._conn = await _open_connection(
            self._dsn, role=self._role, connect_timeout=self._connect_timeout
        )
        return self._conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._conn is not None:
            await self._conn.close()


def connect(
    dsn: str | None = None,
    *,
    role: Role | None = None,
    connect_timeout: int = 10,
) -> _ConnectContext:
    """Open a connection. Callers own the connection's lifetime."""
    return _ConnectContext(dsn, role=role, connect_timeout=connect_timeout)
