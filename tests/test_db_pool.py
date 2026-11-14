"""Tests for the API asyncpg connection cache, outage handling, and uncertain commits."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from arno.agent.builder import build_agent
from arno.agent.deps import AgentDeps
from arno.api.app import create_app
from arno.auth.setup import issue_setup_token
from arno.config import Settings
from arno.db import conn as db_conn
from arno.db.conn import (
    API_POOL_MAX_IDLE,
    Connection,
    bind_connection_cache,
    close_connection_cache,
    connect,
    use_database_url,
)
from arno.jobs.runner import JobRunner
from arno.ledger import audit as ledger_audit
from arno.ledger.accounts import create_account
from arno.ledger.audit import write_group
from arno.ledger.errors import CommitOutcomeUnknownError
from tests.conftest import _dsn
from tests.software_authenticator import SoftwareAuthenticator


async def _enroll_async_client(client: httpx.AsyncClient, settings: Settings) -> None:
    async with connect(_dsn()) as conn:
        token = (await issue_setup_token(conn, ttl_hours=1)).token
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    begin = await client.post("/api/auth/setup/begin", json={"token": token})
    assert begin.status_code == 200, begin.text
    credential = authenticator.registration_credential(begin.json()["options"])
    verified = await client.post(
        "/api/auth/setup/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )
    assert verified.status_code == 200, verified.text
    async with connect(_dsn()) as conn:
        await conn.execute("UPDATE sessions SET reverified_at = now() WHERE revoked_at IS NULL")


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
        ) as client,
    ):
        await _enroll_async_client(client, settings)
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


# 1. Idle backend termination --------------------------------------------------------------------


async def test_idle_cached_backend_termination_recovers_on_next_request(
    authed_client: TestClient, db: Connection
) -> None:
    warm = authed_client.get("/api/accounts")
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

    after = authed_client.get("/api/accounts")
    assert after.status_code == 200


async def test_idle_cached_connection_with_zero_delay_after_terminate(db: Connection) -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
    try:
        async with connect() as conn:
            pid1 = await conn.fetchval("SELECT pg_backend_pid()")

        await db.execute("SELECT pg_terminate_backend($1)", pid1)

        # Immediately acquire without yielding for extra timers: must still succeed.
        async with connect() as conn2:
            assert isinstance(conn2, Connection)
            assert await conn2.fetchval("SELECT 1") == 1
            pid2 = await conn2.fetchval("SELECT pg_backend_pid()")
            assert pid2 != pid1
    finally:
        await close_connection_cache(cache)


# 2. Mid-read termination ------------------------------------------------------------------------


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


# 3. Mid-write termination before commit ---------------------------------------------------------


async def test_mid_write_before_commit_raises_ordinary_error_with_no_retry(
    db: Connection,
) -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
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
        assert await db.fetchval("SELECT count(*) FROM audit_log") == 0
    finally:
        await close_connection_cache(cache)


async def test_mid_write_closed_before_commit_block_raises_connection_does_not_exist(
    db: Connection,
) -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
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
                    # Let the transport receive EOF so conn.is_closed() is True before COMMIT.
                    await asyncio.sleep(0.05)

        assert not isinstance(exc_info.value, CommitOutcomeUnknownError)
        assert await db.fetchval("SELECT count(*) FROM categories WHERE id = 'cat_closed'") == 0
    finally:
        await close_connection_cache(cache)


# 4. Mid-write loss during commit ----------------------------------------------------------------


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
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_commit(tx: Any) -> None:
        raise asyncpg.ConnectionDoesNotExistError("lost during COMMIT")

    monkeypatch.setattr(ledger_audit, "_commit_transaction", fail_commit)

    response = authed_client.post(
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
                        "record_movement",
                        {
                            "movement": {
                                "kind": "expense",
                                "account": "SBI Bank",
                                "amount_rupees": 100,
                                "description": "Tea",
                            }
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


async def test_commit_loss_job_runner_does_not_retry(db: Connection) -> None:
    attempts = {"n": 0}

    async def uncertain_handler(payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        attempts["n"] += 1
        raise CommitOutcomeUnknownError()

    runner = JobRunner(registry={"uncertain": uncertain_handler}, timeouts={"uncertain": 5})
    await db.execute("INSERT INTO jobs (key, kind) VALUES ('uncertain:1', 'uncertain')")

    await runner.run_once()
    row = await db.fetchrow(
        "SELECT status, attempts, last_error FROM jobs WHERE key = 'uncertain:1'"
    )
    assert row is not None
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert "recorded" in str(row["last_error"]).lower()
    assert attempts["n"] == 1
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'job_failed'") == 1

    # Even if run_once is invoked again, a failed job is never retried.
    await runner.run_once()
    assert attempts["n"] == 1


# 5. Database down and recovery ------------------------------------------------------------------


async def test_cache_created_against_closed_port_recovers_when_listener_returns(
    settings: Settings,
) -> None:
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

    cache = bind_connection_cache(proxy_dsn)
    token = use_database_url(proxy_dsn)
    try:
        with pytest.raises(OSError):
            async with connect() as conn:
                await conn.fetchval("SELECT 1")

        await proxy.start(port=proxy_port)
        async with connect() as conn:
            assert await conn.fetchval("SELECT 1") == 1
    finally:
        db_conn.reset_database_url(token)
        await close_connection_cache(cache)
        await proxy.stop()


async def test_app_survives_full_database_outage_and_recovers_without_restart(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    proxy_settings = replace(
        get_settings(refresh=True), agent_ro_password=None, backup_password=None
    )

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


# 6. Overflow, nested acquisition, idle cap, double close, cancelled release, identity ----------


async def test_overflow_and_nested_acquisition_under_full_cache_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn, max_idle=API_POOL_MAX_IDLE)
    real_open = db_conn._open_connection
    direct_opens = 0

    async def spy_open(*args: Any, **kwargs: Any) -> Connection:
        nonlocal direct_opens
        direct_opens += 1
        return await real_open(*args, **kwargs)

    monkeypatch.setattr(db_conn, "_open_connection", spy_open)

    held: list[Connection] = []
    try:
        for _ in range(API_POOL_MAX_IDLE):
            held.append(await connect())

        assert len(cache.idle) == 0
        opens_before_overflow = direct_opens

        # 9th concurrent connection opens immediately without waiting on capacity.
        async with connect() as overflow:
            assert isinstance(overflow, Connection)
            assert await overflow.fetchval("SELECT 1") == 1
            # Nested acquisition under full load also succeeds promptly without deadlock.
            async with connect() as nested:
                assert isinstance(nested, Connection)
                assert await nested.fetchval("SELECT 2") == 2
        assert direct_opens == opens_before_overflow + 2
        assert len(cache.idle) == 2
    finally:
        for conn in held:
            await conn.close()
        assert len(cache.idle) == API_POOL_MAX_IDLE
        await close_connection_cache(cache)


async def test_idle_deque_never_exceeds_max_idle() -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn, max_idle=API_POOL_MAX_IDLE)
    held: list[Connection] = []
    try:
        for _ in range(API_POOL_MAX_IDLE + 3):
            held.append(await connect())

        for conn in held[:API_POOL_MAX_IDLE]:
            await conn.close()
        assert len(cache.idle) == API_POOL_MAX_IDLE

        for extra in held[API_POOL_MAX_IDLE:]:
            await extra.close()
            assert extra.is_closed()
        assert len(cache.idle) == API_POOL_MAX_IDLE
    finally:
        await close_connection_cache(cache)


async def test_reused_connection_same_pid_and_overflow_different_pid() -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
    try:
        async with connect() as first:
            assert type(first) is Connection
            pid1 = await first.fetchval("SELECT pg_backend_pid()")

        async with connect() as reused:
            assert isinstance(reused, Connection)
            assert type(reused) is Connection
            assert await reused.fetchval("SELECT pg_backend_pid()") == pid1
            async with connect() as overflow:
                assert type(overflow) is Connection
                pid_overflow = await overflow.fetchval("SELECT pg_backend_pid()")
                assert pid_overflow != pid1
    finally:
        await close_connection_cache(cache)


async def test_double_close_enqueues_once() -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
    try:
        conn = await connect()
        pid1 = await conn.fetchval("SELECT pg_backend_pid()")
        await conn.close()
        assert len(cache.idle) == 1
        await conn.close()
        assert len(cache.idle) == 1
        assert not conn.is_closed()

        async with connect() as next_conn:
            assert await next_conn.fetchval("SELECT pg_backend_pid()") == pid1
    finally:
        await close_connection_cache(cache)


async def test_cancelled_release_terminates_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
    try:
        conn = await connect()

        async def cancel_reset(*args: Any, **kwargs: Any) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(conn, "reset", cancel_reset)
        with pytest.raises(asyncio.CancelledError):
            await conn.close()
        assert conn.is_closed()
        assert len(cache.idle) == 0
    finally:
        await close_connection_cache(cache)


# 7. Binding rules, release semantics, sequential lifespans, timeout conversion ------------------


async def test_binding_bypasses_and_awaitable_release_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
    real_open = db_conn._open_connection
    direct_calls: list[dict[str, Any]] = []

    async def spy_open(
        arg_dsn: str | None = None, *, role: Any = None, **kwargs: Any
    ) -> Connection:
        direct_calls.append({"dsn": arg_dsn, "role": role})
        return await real_open(arg_dsn, role=role, **kwargs)

    monkeypatch.setattr(db_conn, "_open_connection", spy_open)
    try:
        # Warm the cache via context manager, then verify awaitable connect() reuses and returns it.
        async with connect() as c1:
            pid1 = await c1.fetchval("SELECT pg_backend_pid()")
        assert len(cache.idle) == 1

        c2 = await connect()
        assert isinstance(c2, Connection)
        assert await c2.fetchval("SELECT pg_backend_pid()") == pid1
        await c2.close()
        assert len(cache.idle) == 1

        c3 = await connect()
        async with c3:
            assert isinstance(c3, Connection)
            assert await c3.fetchval("SELECT pg_backend_pid()") == pid1
        assert len(cache.idle) == 1

        direct_calls.clear()

        # Explicit dsn bypasses the cache.
        async with connect(dsn) as explicit:
            assert await explicit.fetchval("SELECT 1") == 1
        assert len(direct_calls) == 1

        # role= bypasses the cache.
        async with connect(role="agent_ro") as ro:
            assert await ro.fetchval("SELECT 1") == 1
        assert len(direct_calls) == 2

        # use_database_url override bypasses the cache (even when pointing at a different DSN).
        other_dsn = dsn + (
            "&application_name=eval_trial" if "?" in dsn else "?application_name=eval_trial"
        )
        token = use_database_url(other_dsn)
        try:
            async with connect() as trial_conn:
                assert await trial_conn.fetchval("SELECT 1") == 1
        finally:
            db_conn.reset_database_url(token)
        assert len(direct_calls) == 3

        # A different event loop bypasses the cache.
        def run_in_other_loop() -> None:
            async def _inner() -> None:
                async with connect() as other_loop_conn:
                    assert await other_loop_conn.fetchval("SELECT 1") == 1

            asyncio.run(_inner())

        t = threading.Thread(target=run_in_other_loop)
        t.start()
        t.join(timeout=10)
        assert not t.is_alive()
        assert len(direct_calls) == 4
    finally:
        await close_connection_cache(cache)


def test_two_sequential_testclient_lifespans_both_work(settings: Settings) -> None:
    with TestClient(create_app(settings)) as first:
        assert first.get("/api/health").status_code == 200
    with TestClient(create_app(settings)) as second:
        assert second.get("/api/health").status_code == 200


async def test_connect_timeout_becomes_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hang_connect(*args: Any, **kwargs: Any) -> Connection:
        raise TimeoutError("connect timed out")

    monkeypatch.setattr(asyncpg, "connect", hang_connect)
    with pytest.raises(ConnectionError) as exc_info:
        async with connect():
            pass
    assert isinstance(exc_info.value, OSError)

    # Also verify a timed-out pre-ping discards the idle connection and falls back to open,
    # converting the connect timeout to ConnectionError.
    monkeypatch.undo()
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
    try:
        async with connect() as warm:
            assert await warm.fetchval("SELECT 1") == 1
        cached = cache.idle[-1]

        async def hang_ping(*args: Any, **kwargs: Any) -> str:
            raise TimeoutError("ping timed out")

        monkeypatch.setattr(cached, "execute", hang_ping)
        monkeypatch.setattr(asyncpg, "connect", hang_connect)
        with pytest.raises(ConnectionError):
            async with connect():
                pass
        assert cached.is_closed()
        assert len(cache.idle) == 0
    finally:
        await close_connection_cache(cache)


# 8. Session hygiene and jsonb decoding ----------------------------------------------------------


async def test_cached_connection_resets_session_state_and_decodes_jsonb(
    db: Connection,
) -> None:
    dsn = _dsn()
    cache = bind_connection_cache(dsn)
    try:
        async with connect() as c1:
            pid1 = await c1.fetchval("SELECT pg_backend_pid()")
            await c1.execute("SET statement_timeout = '1234ms'")
            await c1.execute("BEGIN")
            await c1.execute("INSERT INTO categories (id, name) VALUES ('cat_leak', 'Leak')")

        async with connect() as c2:
            assert isinstance(c2, Connection)
            assert await c2.fetchval("SELECT pg_backend_pid()") == pid1
            assert await c2.fetchval("SHOW statement_timeout") != "1234ms"
            assert not c2.is_in_transaction()
            decoded = await c2.fetchval("SELECT '{\"hello\": [1, 2]}'::jsonb")
            assert decoded == {"hello": [1, 2]}

        assert await db.fetchval("SELECT count(*) FROM categories WHERE id = 'cat_leak'") == 0
    finally:
        await close_connection_cache(cache)


# 9. The 503 handler itself ----------------------------------------------------------------------


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
    authed_client: TestClient,
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
        response = authed_client.get("/api/accounts")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["detail"] == "The database is unreachable."
    if isinstance(error, asyncpg.InternalClientError):
        assert any(
            rec.levelno == logging.ERROR and rec.exc_info is not None for rec in caplog.records
        )


async def test_api_does_not_turn_bare_timeout_error_into_503(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arno.ledger import accounts as ledger_accounts

    async def model_timeout(conn: Connection, **kwargs: Any) -> list[dict[str, Any]]:
        del conn, kwargs
        raise TimeoutError("model timed out")

    async with _authed_app_client(settings, raise_app_exceptions=False) as (_, client):
        monkeypatch.setattr(ledger_accounts, "list_accounts", model_timeout)
        response = await client.get("/api/accounts")
        assert response.status_code == 500
