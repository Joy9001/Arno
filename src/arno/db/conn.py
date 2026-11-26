"""Database connections: one asyncpg pool per event loop, direct connections otherwise.

The FastAPI lifespan opens the pool with `open_pool`. `connect()` acquires from the pool bound to
the running loop when it targets the pool's database, and opens a direct connection for any other
target: a `dsn=` or `role=` override (the `agent_ro` boundary) or an eval trial database. See
`platform.md`.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from contextvars import ContextVar, Token
from typing import Any, Literal, cast, override
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import asyncpg

from arno.config import Settings, get_settings

Role = Literal["app", "agent_ro", "backup"]
POOL_MAX_SIZE = 20

# An eval trial points every connection in its task at its own cloned database. A context
# variable, rather than an environment variable, keeps concurrent trials apart: each case
# lifecycle sets it in its own task and evaluators still see it.
_DATABASE_URL_OVERRIDE: ContextVar[str | None] = ContextVar(
    "arno_database_url_override", default=None
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
    """An asyncpg connection that returns dict rows."""

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


# loop -> (dsn, pool). TestClient runs the app lifespan on its own loop, so binding is per loop.
_POOLS: dict[asyncio.AbstractEventLoop, tuple[str, asyncpg.Pool]] = {}


async def open_pool(dsn: str) -> asyncpg.Pool:
    """Open a pool and bind it to the running event loop and `dsn`."""

    pool = asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=POOL_MAX_SIZE,
        connection_class=Connection,
        init=_init_connection,
    )
    await pool
    _POOLS[asyncio.get_running_loop()] = (dsn, pool)
    return pool


async def close_pool(pool: asyncpg.Pool, timeout: float = 5) -> None:
    """Unbind and close `pool`; terminate it if a detached task still holds a connection."""
    loop = asyncio.get_running_loop()
    if loop in _POOLS and _POOLS[loop][1] is pool:
        del _POOLS[loop]
    try:
        await asyncio.wait_for(pool.close(), timeout)
    except TimeoutError:
        pool.terminate()
    except asyncio.CancelledError:
        pool.terminate()
        raise


async def _init_connection(conn: Connection) -> None:
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


class _ConnectContext:
    """A connection factory usable with both `await` and `async with`."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        role: Role | None = None,
        connect_timeout: float = 10,
    ) -> None:
        self._dsn = dsn
        self._role = role
        self._connect_timeout = connect_timeout
        self._conn: Connection | None = None
        self._pool: asyncpg.Pool | None = None

    async def _acquire(self) -> Connection:
        pooled = _POOLS.get(asyncio.get_running_loop()) if self._dsn is None else None
        if self._role is None and pooled and pooled[0] == current_database_url():
            try:
                # The proxy forwards every Connection method; its type is the connection's.
                self._pool = pooled[1]
                return cast(Connection, await self._pool.acquire(timeout=self._connect_timeout))
            except TimeoutError as exc:
                raise ConnectionError("database connection timed out") from exc
        target = self._dsn or current_database_url()
        if self._role is not None:
            target = role_dsn(target, self._role)
        try:
            conn: Connection = await asyncpg.connect(
                target, timeout=self._connect_timeout, connection_class=Connection
            )
        except TimeoutError as exc:
            raise ConnectionError("database connection timed out") from exc
        await _init_connection(conn)
        return conn

    async def __aenter__(self) -> Connection:
        self._conn = await self._acquire()
        return self._conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            if self._pool is not None:
                await self._pool.release(conn)
            else:
                await conn.close()


def connect(
    dsn: str | None = None,
    *,
    role: Role | None = None,
    connect_timeout: float = 10,
) -> _ConnectContext:
    """Acquire a pooled connection or open a direct one; leaving `async with` gives it back."""
    return _ConnectContext(dsn, role=role, connect_timeout=connect_timeout)
