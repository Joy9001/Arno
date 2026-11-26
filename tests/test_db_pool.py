"""The per-loop asyncpg pool behind `connect()`, and the direct connections that bypass it.

Pool tests use a migrated scratch database and need no ledger code; the API tests below drive the
real app.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
from asyncpg.pool import PoolConnectionProxy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from arno.agent.builder import build_agent
from arno.agent.deps import AgentDeps
from arno.api.app import create_app
from arno.config import Settings
from arno.db.bootstrap import set_agent_ro_password
from arno.db.conn import (
    Connection,
    close_pool,
    connect,
    open_pool,
    reset_database_url,
    use_database_url,
)
from arno.db.migrate import run_migrations
from arno.ledger import audit as ledger_audit
from arno.ledger.accounts import create_account
from arno.ledger.audit import write_group
from arno.ledger.errors import CommitOutcomeUnknownError
from tests.conftest import _dsn, _mutable_reset, cluster_lock, fresh_database


@pytest.fixture
async def scratch() -> AsyncGenerator[str]:
    """A migrated scratch database that `connect()` targets for the test."""
    async with fresh_database("arno_pool") as dsn:
        async with cluster_lock(), connect(dsn) as owner:
            await run_migrations(owner)
            await set_agent_ro_password(owner, os.environ["AGENT_RO_PASSWORD"])
        token = use_database_url(dsn)
        try:
            yield dsn
        finally:
            reset_database_url(token)


@pytest.fixture
async def pool(scratch: str) -> AsyncGenerator[asyncpg.Pool]:
    opened = await open_pool(scratch)
    try:
        yield opened
    finally:
        await close_pool(opened)


async def _pid(conn: Connection) -> int:
    return int(str(await conn.fetchval("SELECT pg_backend_pid()")))


async def test_connect_reuses_pooled_connections(pool: asyncpg.Pool) -> None:
    async with connect() as first:
        pid = await _pid(first)
        assert isinstance(first, PoolConnectionProxy)

    async with connect() as second:
        assert await _pid(second) == pid
    assert pool.get_size() == 1


async def test_leaving_the_context_releases_the_connection(pool: asyncpg.Pool) -> None:
    async with connect() as conn:
        pid = await _pid(conn)
        assert pool.get_idle_size() == 0

    assert pool.get_idle_size() == 1
    async with connect() as again:
        assert await _pid(again) == pid


async def test_concurrent_acquisitions_use_distinct_connections(
    pool: asyncpg.Pool,
) -> None:
    async with connect() as one, connect() as two:
        assert await _pid(one) != await _pid(two)
    assert pool.get_size() == 2


async def test_release_resets_session_state_and_json_is_decoded(
    pool: asyncpg.Pool,
) -> None:
    async with connect() as first:
        pid = await _pid(first)
        await first.execute("SET statement_timeout = '1234ms'")
        await first.execute("BEGIN")

    async with connect() as second:
        assert await _pid(second) == pid
        assert await second.fetchval("SHOW statement_timeout") != "1234ms"
        assert not second.is_in_transaction()
        assert await second.fetchval("SELECT '{\"a\": [1, 2]}'::jsonb") == {"a": [1, 2]}
        assert await second.fetchval("SELECT $1::jsonb", {"b": 1}) == {"b": 1}


async def test_a_terminated_idle_backend_is_replaced_on_the_next_acquisition(
    scratch: str, pool: asyncpg.Pool
) -> None:
    async with connect() as conn:
        pid = await _pid(conn)
    async with connect(scratch) as admin:
        await admin.execute("SELECT pg_terminate_backend($1)", pid)

    async with connect() as after:
        assert await _pid(after) != pid


async def test_explicit_dsn_and_other_trial_databases_bypass_the_pool(
    scratch: str, pool: asyncpg.Pool
) -> None:
    async with connect() as pooled:
        pooled_pid = await _pid(pooled)

    async with connect(scratch) as explicit:
        assert not isinstance(explicit, PoolConnectionProxy)
        assert await _pid(explicit) != pooled_pid

    async with fresh_database("arno_pool_trial") as trial_dsn:
        token = use_database_url(trial_dsn)
        try:
            async with connect() as trial:
                assert not isinstance(trial, PoolConnectionProxy)
                name = await trial.fetchval("SELECT current_database()")
        finally:
            reset_database_url(token)
        assert name != await _database(scratch)
    assert pool.get_size() == 1


async def _database(dsn: str) -> str:
    async with connect(dsn) as conn:
        return str(await conn.fetchval("SELECT current_database()"))


async def test_agent_ro_connects_directly_never_from_the_pool(
    pool: asyncpg.Pool,
) -> None:
    size = pool.get_size()
    async with connect(role="agent_ro") as ro:
        assert not isinstance(ro, PoolConnectionProxy)
        assert await ro.fetchval("SELECT current_user") == "agent_ro"
    assert pool.get_size() == size


async def test_a_pool_serves_only_the_event_loop_that_opened_it(
    scratch: str, pool: asyncpg.Pool
) -> None:
    seen: dict[str, Any] = {}

    def other_loop() -> None:
        async def run() -> None:
            use_database_url(scratch)  # a new thread starts with an empty context
            async with connect() as conn:
                seen["pooled"] = isinstance(conn, PoolConnectionProxy)
                seen["rows"] = await conn.fetchval("SELECT 1")

        asyncio.run(run())

    thread = threading.Thread(target=other_loop)
    thread.start()
    await asyncio.to_thread(thread.join, 10)

    assert seen == {"pooled": False, "rows": 1}
    assert pool.get_size() == 1


@pytest.mark.usefixtures("scratch")
async def test_without_a_pool_connections_are_direct_and_close_for_real() -> None:
    async with connect() as conn:
        assert not isinstance(conn, PoolConnectionProxy)

    assert conn.is_closed()


async def test_shutdown_terminates_a_pool_that_a_detached_task_still_holds(scratch: str) -> None:
    pool = await open_pool(scratch)
    holder = connect()
    held = await holder.__aenter__()
    pid = await _pid(held)

    await asyncio.wait_for(close_pool(pool, timeout=0.2), timeout=5)

    assert pool.is_closing()
    async with connect(scratch) as admin:
        gone = await admin.fetchval("SELECT count(*) FROM pg_stat_activity WHERE pid = $1", pid)
    assert gone == 0
    async with connect() as after:  # the pool is unbound again, so this one is direct
        assert not isinstance(after, PoolConnectionProxy)


async def test_shutdown_closes_an_idle_pool_gracefully(scratch: str) -> None:
    pool = await open_pool(scratch)
    async with connect() as conn:
        await conn.fetchval("SELECT 1")

    await close_pool(pool)

    assert pool.is_closing()
    assert pool.get_size() == 0


async def test_connect_timeout_becomes_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang_connect(*args: Any, **kwargs: Any) -> Connection:
        raise TimeoutError("connect timed out")

    monkeypatch.setattr(asyncpg, "connect", hang_connect)
    with pytest.raises(ConnectionError) as exc_info:
        async with connect(role="agent_ro"):
            pass
    assert isinstance(exc_info.value, OSError)


# API behaviour over the pool ------------------------------------------------------------


@asynccontextmanager
async def _authed_app_client(
    settings: Settings,
    *,
    app: FastAPI | None = None,
    raise_app_exceptions: bool = True,
) -> AsyncGenerator[tuple[FastAPI, httpx.AsyncClient]]:
    target_app = app or create_app(settings)
    async with (
        target_app.router.lifespan_context(target_app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=target_app, raise_app_exceptions=raise_app_exceptions
            ),
            base_url="http://localhost",
            headers={"Tailscale-User-Login": settings.owner_email or ""},
        ) as client,
    ):
        yield target_app, client


class _TcpProxy:
    """Minimal loopback TCP proxy so tests can bring a Postgres listener down and back up."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target_host = target_host
        self.target_port = target_port
        self.port = 0
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self, *, port: int = 0) -> int:
        self._server = await asyncio.start_server(
            self._handle,
            "127.0.0.1",
            port,
            reuse_address=True,
        )
        sockets = self._server.sockets
        assert sockets
        self.port = int(sockets[0].getsockname()[1])
        return self.port

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            server.close_clients()
        for writer in list(self._writers):
            with contextlib.suppress(OSError):
                writer.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        self._tasks.clear()
        self._writers.clear()

    async def _handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(self.target_host, self.target_port)
        except OSError:
            client_writer.close()
            return

        self._writers.add(client_writer)
        self._writers.add(up_writer)

        async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            except OSError, asyncio.CancelledError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    writer.close()

        t1 = asyncio.create_task(pipe(client_reader, up_writer))
        t2 = asyncio.create_task(pipe(up_reader, client_writer))
        self._tasks.update((t1, t2))
        try:
            await asyncio.gather(t1, t2, return_exceptions=True)
        finally:
            self._tasks.discard(t1)
            self._tasks.discard(t2)
            self._writers.discard(client_writer)
            self._writers.discard(up_writer)


@pytest.mark.usefixtures("app")
async def test_api_reset_repairs_edited_seed_rows_and_keeps_unchanged_ones():
    async with connect(_dsn()) as conn:
        category = await conn.fetchrow_required(
            "SELECT id, name FROM categories ORDER BY id LIMIT 1"
        )
        await conn.execute(
            "UPDATE categories SET name = 'Edited category' WHERE id = $1", category["id"]
        )

    await _mutable_reset(api=True)

    async with connect(_dsn()) as conn:
        assert (
            await conn.fetchval("SELECT name FROM categories WHERE id = $1", category["id"])
            == category["name"]
        )


async def test_idle_backend_termination_recovers_on_next_request(
    session_client: TestClient, db: Connection
) -> None:
    warm = session_client.get("/api/accounts")
    assert warm.status_code == 200

    rows = await db.fetch(
        """
        SELECT pid FROM pg_stat_activity
        WHERE datname = current_database()
          AND usename = 'app'
          AND state = 'idle'
          AND pid <> pg_backend_pid()
        """
    )
    assert rows
    for row in rows:
        await db.execute("SELECT pg_terminate_backend($1)", row["pid"])

    # The first request may land on a connection the pool has not yet seen die (503, which
    # discards it); the next one is served by a fresh connection.
    first = session_client.get("/api/accounts")
    assert first.status_code in {200, 503}
    assert session_client.get("/api/accounts").status_code == 200


# Mid-read termination ------------------------------------------------------------------------


async def test_mid_read_termination_returns_503_and_next_request_succeeds(
    settings: Settings, db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arno.ledger import accounts as ledger_accounts

    started = asyncio.Event()
    backend_pid: dict[str, int] = {}
    real_list_accounts = ledger_accounts.list_accounts
    inject = {"slow": True}

    async def slow_once(conn: Connection, **kwargs: Any) -> list[dict[str, Any]]:
        if inject["slow"]:
            inject["slow"] = False
            pid = await conn.fetchval("SELECT pg_backend_pid()")
            assert pid is not None
            backend_pid["pid"] = int(pid)
            started.set()
            await conn.execute("SELECT pg_sleep(10)")
        return await real_list_accounts(conn, **kwargs)

    monkeypatch.setattr(ledger_accounts, "list_accounts", slow_once)

    async with _authed_app_client(settings) as (_, client):
        req_task = asyncio.create_task(client.get("/api/accounts"))
        await asyncio.wait_for(started.wait(), timeout=5)
        await db.execute("SELECT pg_terminate_backend($1)", backend_pid["pid"])

        failed = await req_task
        assert failed.status_code == 503
        assert failed.json()["detail"] == "The database is unreachable."

        recovered = await client.get("/api/accounts")
        assert recovered.status_code == 200


# Mid-write termination before commit ---------------------------------------------------------


async def test_mid_write_before_commit_raises_ordinary_error_with_no_retry(
    db: Connection,
) -> None:
    dsn = _dsn()
    pool = await open_pool(dsn)
    attempts = 0
    try:
        with pytest.raises(
            (asyncpg.ConnectionDoesNotExistError, asyncpg.InternalClientError)
        ) as exc_info:
            async with connect() as conn:
                async with write_group(conn, actor="user") as group:
                    attempts += 1
                    await conn.execute(
                        "INSERT INTO categories (id, name) VALUES ('cat_lost', 'Should Not Commit')"
                    )
                    group.record_created(
                        "categories", {"id": "cat_lost", "name": "Should Not Commit"}
                    )
                    pid = await conn.fetchval("SELECT pg_backend_pid()")
                    await db.execute("SELECT pg_terminate_backend($1)", pid)
                    await conn.execute("SELECT 1")

        assert not isinstance(exc_info.value, CommitOutcomeUnknownError)
        assert attempts == 1
        assert await db.fetchval("SELECT count(*) FROM categories WHERE id = 'cat_lost'") == 0
        assert await db.fetchval("SELECT count(*) FROM undo_log") == 0
    finally:
        await close_pool(pool)


async def test_mid_write_closed_before_commit_block_raises_connection_does_not_exist(
    db: Connection,
) -> None:
    dsn = _dsn()
    pool = await open_pool(dsn)
    try:
        with pytest.raises(asyncpg.ConnectionDoesNotExistError) as exc_info:
            async with connect() as conn:
                async with write_group(conn, actor="user") as group:
                    await conn.execute(
                        "INSERT INTO categories (id, name) "
                        "VALUES ('cat_closed', 'Closed Before Commit')"
                    )
                    group.record_created(
                        "categories", {"id": "cat_closed", "name": "Closed Before Commit"}
                    )
                    pid = await conn.fetchval("SELECT pg_backend_pid()")
                    await db.execute("SELECT pg_terminate_backend($1)", pid)

                    async def _until_closed() -> None:
                        # The pool detaches a lost connection from its proxy.
                        with contextlib.suppress(asyncpg.InterfaceError):
                            while not conn.is_closed():
                                await asyncio.sleep(0.001)

                    # Wait for the transport to observe EOF, not a fixed nap.
                    await asyncio.wait_for(_until_closed(), timeout=5)

        assert not isinstance(exc_info.value, CommitOutcomeUnknownError)
        assert await db.fetchval("SELECT count(*) FROM categories WHERE id = 'cat_closed'") == 0
    finally:
        await close_pool(pool)


# Mid-write loss during commit ----------------------------------------------------------------


async def test_commit_loss_raises_commit_outcome_unknown_error(
    db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_commit(tx: Any) -> None:
        raise asyncpg.ConnectionDoesNotExistError("lost during COMMIT")

    monkeypatch.setattr(ledger_audit, "_commit_transaction", fail_commit)

    with pytest.raises(CommitOutcomeUnknownError):
        async with write_group(db, actor="user") as group:
            await db.execute(
                "INSERT INTO categories (id, name) VALUES ('cat_uncertain', 'Uncertain Category')"
            )
            group.record_created(
                "categories", {"id": "cat_uncertain", "name": "Uncertain Category"}
            )


async def test_commit_loss_api_returns_distinct_503_body(
    session_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_commit(tx: Any) -> None:
        raise asyncpg.ConnectionDoesNotExistError("lost during COMMIT")

    monkeypatch.setattr(ledger_audit, "_commit_transaction", fail_commit)

    response = session_client.post(
        "/api/accounts",
        json={"kind": "bank", "name": "Uncertain API Account"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The connection was lost while saving. Check whether the change was recorded "
        "before trying again."
    )


async def test_commit_loss_agent_write_tool_reports_unknown_without_retry(
    ledger: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ledger.execute("INSERT INTO conversations (id, title) VALUES ('cnv_uncertain', 'test')")
    await ledger.execute(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES ('cnv_uncertain', 'msg_uncertain', 'user', '{}'::jsonb, 'test')
        """
    )
    msg_row = await ledger.fetchrow_required(
        "SELECT id FROM messages WHERE client_id = 'msg_uncertain'"
    )
    await create_account(ledger, kind="bank", name="SBI Bank")
    call_count = {"n": 0}
    observed_tool_returns: list[Any] = []

    async def fail_commit(tx: Any) -> None:
        call_count["n"] += 1
        raise OSError("broken pipe during COMMIT")

    monkeypatch.setattr(ledger_audit, "_commit_transaction", fail_commit)

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        for msg in messages:
            for part in getattr(msg, "parts", ()):
                if getattr(part, "part_kind", None) == "tool-return":
                    observed_tool_returns.append(part.content)
        if not observed_tool_returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "record_movements",
                        {
                            "movements": [
                                {
                                    "kind": "expense",
                                    "account": "SBI Bank",
                                    "amount_rupees": 100,
                                    "description": "Tea",
                                }
                            ]
                        },
                        tool_call_id="tc_1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Please check whether it was recorded.")])

    agent = build_agent(model=FunctionModel(script))
    deps = AgentDeps(
        conversation_id="cnv_uncertain",
        message_id=str(msg_row["id"]),
    )
    result = await agent.run("Log 100 for tea", deps=deps)
    assert "check" in str(result.output).lower()
    assert call_count["n"] == 1
    assert len(observed_tool_returns) == 1
    payload = observed_tool_returns[0]
    assert payload["status"] == "outcome_unknown"
    assert payload["committed"] == "unknown"
    assert "unknown" in payload["message"].lower()
    assert "do not call this write tool again" in payload["message"].lower()


# Database down and recovery ------------------------------------------------------------------


async def test_open_pool_fails_fast_on_a_closed_port_and_works_when_the_listener_returns(
    settings: Settings, clean_database: None
) -> None:
    del clean_database
    parts = urlsplit(settings.database_url)
    assert parts.hostname and parts.port
    proxy = _TcpProxy(parts.hostname, parts.port)
    proxy_port = await proxy.start()
    await proxy.stop()

    auth = (
        f"{parts.username}:{parts.password}@"
        if parts.password is not None
        else f"{parts.username}@"
    )
    proxy_dsn = urlunsplit(parts._replace(netloc=f"{auth}127.0.0.1:{proxy_port}"))

    with pytest.raises(OSError):
        await open_pool(proxy_dsn)

    await proxy.start(port=proxy_port)
    pool = await open_pool(proxy_dsn)
    token = use_database_url(proxy_dsn)
    try:
        async with connect() as conn:
            assert await conn.fetchval("SELECT 1") == 1
    finally:
        reset_database_url(token)
        await close_pool(pool)
        await proxy.stop()


async def test_app_survives_full_database_outage_and_recovers_without_restart(
    settings: Settings, clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    from arno.config import get_settings

    parts = urlsplit(settings.database_url)
    assert parts.hostname and parts.port
    proxy = _TcpProxy(parts.hostname, parts.port)
    proxy_port = await proxy.start()
    auth = (
        f"{parts.username}:{parts.password}@"
        if parts.password is not None
        else f"{parts.username}@"
    )
    proxy_dsn = urlunsplit(parts._replace(netloc=f"{auth}127.0.0.1:{proxy_port}"))
    monkeypatch.setenv("DATABASE_URL", proxy_dsn)
    get_settings(refresh=True)
    proxy_settings = replace(settings, database_url=proxy_dsn)

    try:
        async with _authed_app_client(proxy_settings) as (_, client):
            assert (await client.get("/api/accounts")).status_code == 200

            # Full database outage: listener down and existing sockets terminated.
            await proxy.stop()
            down_api = await client.get("/api/accounts")
            assert down_api.status_code == 503
            assert down_api.json()["detail"] == "The database is unreachable."

            down_health = await client.get("/api/health")
            assert down_health.status_code == 503
            assert down_health.json()["detail"] == "The database is unreachable."

            # Postgres comes back on the same address; no app/cache restart.
            await proxy.start(port=proxy_port)
            up_api = await client.get("/api/accounts")
            assert up_api.status_code == 200
            up_health = await client.get("/api/health")
            assert up_health.status_code == 200
    finally:
        await proxy.stop()
        monkeypatch.undo()
        get_settings(refresh=True)


def test_two_sequential_testclient_lifespans_both_work(settings: Settings) -> None:
    with TestClient(create_app(settings)) as first:
        assert first.get("/api/health").status_code == 200
    with TestClient(create_app(settings)) as second:
        assert second.get("/api/health").status_code == 200


# The 503 handler itself ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.PostgresConnectionError("down"),
        asyncpg.AdminShutdownError("restarting"),
        asyncpg.CrashShutdownError("crashed"),
        asyncpg.InternalClientError("cannot switch to state"),
        ConnectionError("database connection timed out"),
    ],
)
async def test_api_503_handler_maps_connection_and_shutdown_errors(
    session_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    from arno.ledger import accounts as ledger_accounts

    async def boom(conn: Connection, **kwargs: Any) -> list[dict[str, Any]]:
        del conn, kwargs
        raise error

    monkeypatch.setattr(ledger_accounts, "list_accounts", boom)
    with caplog.at_level(logging.WARNING, logger="arno"):
        response = session_client.get("/api/accounts")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["detail"] == "The database is unreachable."
    if isinstance(error, asyncpg.InternalClientError):
        assert any(
            rec.levelno == logging.ERROR and rec.exc_info is not None for rec in caplog.records
        )


async def test_api_does_not_turn_bare_timeout_error_into_503(
    settings: Settings, clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database  # enrollment needs fresh setup state, not a user left by a prior app test
    from arno.ledger import accounts as ledger_accounts

    async def model_timeout(conn: Connection, **kwargs: Any) -> list[dict[str, Any]]:
        del conn, kwargs
        raise TimeoutError("model timed out")

    async with _authed_app_client(settings, raise_app_exceptions=False) as (_, client):
        monkeypatch.setattr(ledger_accounts, "list_accounts", model_timeout)
        response = await client.get("/api/accounts")
        assert response.status_code == 500
