"""Test setup: a scratch Postgres, and model requests blocked.

Tests refuse to run against a database whose name doesn't end in `_test`, so a stray
DATABASE_URL can't wipe a real ledger. Every outbound socket to a non-loopback address is
refused, and each model role's endpoint defaults to a closed loopback port.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shlex
import socket
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient


def _per_worker_env() -> None:
    """Point each xdist worker at its own database and task folders.

    Runs before `fin_agent` is imported, so `Settings.from_env()` sees the worker values. Without
    xdist nothing changes.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    dsn = os.environ.get("DATABASE_URL", "")
    if "://" in dsn:
        parts = urlsplit(dsn)
        name = parts.path.lstrip("/")
        if name.endswith("_test"):
            dsn = urlunsplit(parts._replace(path=f"/{name[:-5]}_{worker}_test"))
    elif dsn:
        tokens = shlex.split(dsn)
        tokens = [
            f"dbname={token[7:-5]}_{worker}_test"
            if token.startswith("dbname=") and token.endswith("_test")
            else token
            for token in tokens
        ]
        dsn = shlex.join(tokens)
    if dsn:
        os.environ["DATABASE_URL"] = dsn
    temp = Path(tempfile.gettempdir())
    os.environ["FIN_AGENT_TASKS_DIR"] = str(temp / f"fin-agent-tasks-{worker}")
    os.environ["FIN_AGENT_PRE_MIGRATION_DIR"] = str(temp / f"fin-agent-pre-migration-{worker}")


_per_worker_env()

# Set before importing fin_agent, so Settings.from_env sees them.
os.environ.setdefault("FIN_AGENT_RP_ID", "localhost")
os.environ.setdefault("FIN_AGENT_RP_ORIGIN", "http://localhost:8000")
os.environ.setdefault("FIN_AGENT_COOKIE_SECURE", "0")
for _role in ("CHAT", "VISION", "STT"):
    os.environ.setdefault(f"{_role}_BASE_URL", "http://127.0.0.1:9/v1")
    os.environ.setdefault(f"{_role}_MODEL", "blocked-in-tests")

# The job loop is tested directly; the API tests must not race it.
os.environ.setdefault("FIN_AGENT_JOBS_ENABLED", "0")

# Tests never export ambient telemetry. The standard SDK kill switch stops the process
# bootstrap from installing Phoenix; a smoke test uses its own isolated process.
for _otel_name in (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "PHOENIX_COLLECTOR_ENDPOINT",
    "PHOENIX_API_KEY",
):
    os.environ.pop(_otel_name, None)
os.environ["OTEL_SDK_DISABLED"] = "true"

# No test can spend money. Only evals turn this on.
from pydantic_ai import models  # noqa: E402

from fin_agent.api.app import create_app  # noqa: E402
from fin_agent.auth.setup import issue_setup_token  # noqa: E402
from fin_agent.config import Settings, get_settings  # noqa: E402
from fin_agent.db.bootstrap import set_agent_ro_password, set_backup_password  # noqa: E402
from fin_agent.db.conn import (  # noqa: E402
    Connection,
    connect,
    conninfo_to_dict,
    make_conninfo,
    quote_ident,
)
from fin_agent.db.migrate import run_migrations  # noqa: E402
from fin_agent.demo.persona import seed_demo  # noqa: E402
from fin_agent.ledger.accounts import ensure_system_accounts  # noqa: E402
from fin_agent.ledger.categories import ensure_default_categories  # noqa: E402
from tests.software_authenticator import SoftwareAuthenticator  # noqa: E402

# A module-level `bool` flag the library itself rebinds; its inferred type is `Literal[True]`.
models.ALLOW_MODEL_REQUESTS = False  # pyrefly: ignore[bad-assignment]

RESET_TABLES = (
    "schedule_payments",
    "schedule_occurrences",
    "users",
    "setup_tokens",
    "webauthn_challenges",
    "sessions",
    "pgledger_idempotency_keys",
    "pgledger_entries",
    "pgledger_transfers",
    "pgledger_accounts",
    "accounts",
    "payees",
    "categories",
    "movements",
    "transfer_labels",
    "captures",
    "imports",
    "statement_lines",
    "statement_layouts",
    "parser_versions",
    "attachments",
    "schedules",
    "goals",
    "scheduled_questions",
    "category_rules",
    "categorize_runs",
    "baselines",
    "conversations",
    "messages",
    "preferences",
    "audit_log",
    "notifications",
    "recap_views",
    "push_subscriptions",
    "jobs",
    "job_schedules",
    "backup_runs",
    "vapid_keys",
    "risk_state",
)

_TRUNCATE = "TRUNCATE " + ", ".join(RESET_TABLES) + " RESTART IDENTITY CASCADE"
SNAPSHOT_SCHEMA = "demo_seed"


async def _reset(conn: Connection) -> None:
    """Empty every table's rows and put the single-row defaults back."""
    await conn.execute(_TRUNCATE)
    await conn.execute(
        """
        UPDATE settings
        SET base_currency = 'INR',
            time_zone = 'Asia/Kolkata',
            floor_paise = NULL,
            default_horizon_days = 90,
            notification_time = '08:00',
            weekly_briefing_dow = 0,
            reminder_lead_days = 3,
            last_model_error_type = NULL,
            last_model_error_at = NULL,
            updated_at = now()
        WHERE id = 1
        """
    )
    await conn.execute("INSERT INTO risk_state (id) VALUES (1) ON CONFLICT DO NOTHING")


async def _snapshot_table(conn: Connection, table: str) -> list[str] | None:
    """Copy one table's rows into the snapshot schema; return the columns worth re-inserting.

    Generated and dropped columns are left out, so the insert back is a plain INSERT.
    """
    columns = [
        row["attname"]
        for row in await conn.fetch(
            """
            SELECT a.attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = $1
              AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
            ORDER BY a.attnum
            """,
            table,
        )
    ]
    source = f"public.{quote_ident(table)}"
    if not await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {source})"):
        return None
    names = ", ".join(quote_ident(column) for column in columns)
    await conn.execute(
        f"CREATE TABLE {SNAPSHOT_SCHEMA}.{quote_ident(table)} AS SELECT {names} FROM {source}"
    )
    return columns


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise pytest.UsageError(
            "DATABASE_URL must point at a scratch database whose name ends in _test"
        )
    return dsn


# One key, taken on the `postgres` database, so every worker serialises the cluster-wide DDL
# (CREATE/ALTER ROLE, CREATE DATABASE) even though each worker uses its own test database.
CLUSTER_LOCK_KEY = 0x66696E5F6C6F636B  # "fin_lock"


@asynccontextmanager
async def cluster_lock() -> AsyncGenerator[None]:
    """Hold a Postgres advisory lock that all workers can see, on the shared server."""
    info = conninfo_to_dict(_dsn())
    info["dbname"] = "postgres"
    async with connect(make_conninfo(**info)) as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", CLUSTER_LOCK_KEY)
        try:
            yield
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", CLUSTER_LOCK_KEY)


async def _ensure_database() -> None:
    """Create this run's database if it does not exist yet."""
    info = conninfo_to_dict(_dsn())
    name = info["dbname"]
    info["dbname"] = "postgres"
    async with connect(make_conninfo(**info)) as conn:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            await conn.execute(f"CREATE DATABASE {quote_ident(name)}")


def pytest_configure(config: pytest.Config) -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise pytest.UsageError(
            "DATABASE_URL must point at a scratch database whose name ends in _test, e.g. "
            "postgresql://app:app@127.0.0.1:5433/fin_agent_test"
        )
    dbname = conninfo_to_dict(dsn).get("dbname", "")
    if not dbname.endswith("_test") and not os.environ.get("FIN_AGENT_ALLOW_DATABASE_RESET"):
        raise pytest.UsageError(
            f"Refusing to reset database {dbname!r}: its name must end in _test. "
            "Set FIN_AGENT_ALLOW_DATABASE_RESET=1 to override."
        )


def _is_local(host: str) -> bool:
    if host in {"", "localhost", "testserver"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No model endpoint, and nothing else, leaves the test process."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guard(address: Any) -> None:
        if isinstance(address, tuple) and not _is_local(str(address[0])):
            raise RuntimeError(f"Outbound network is blocked in tests: {address!r}")

    def connect(self: socket.socket, address: Any) -> Any:
        guard(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        guard(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else str(host or "")
        if not _is_local(name):
            raise RuntimeError(f"DNS lookup is blocked in tests: {name!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(scope="session")
def migrated() -> list[int]:
    """Start from an empty public schema and apply every migration once."""
    dsn = _dsn()

    async def _run() -> list[int]:
        async with cluster_lock():
            await _ensure_database()
            async with connect(dsn) as conn:
                await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
                await conn.execute("CREATE SCHEMA public")
                applied = await run_migrations(conn)
                await set_agent_ro_password(conn, os.environ.get("AGENT_RO_PASSWORD"))
                await set_backup_password(conn, os.environ.get("BACKUP_PASSWORD"))
                return applied

    return asyncio.run(_run())


@pytest.fixture(autouse=True)
async def clean_database(migrated: list[int], demo_snapshot: str) -> None:
    # The snapshot is built before any test's own fixtures run, because building it empties
    # the database; built lazily, it would wipe rows an earlier fixture had just written.
    async with connect(_dsn()) as conn:
        await _reset(conn)


@pytest.fixture(scope="session")
def demo_snapshot(migrated: list[int]) -> Iterator[str]:
    """Seed the demo persona once, snapshot it into a scratch schema, and return its restore SQL.

    Built once per session, first. The restore runs as one simple-protocol round trip with
    foreign-key triggers off, because the snapshot is known good.
    """
    dsn = _dsn()

    async def _build() -> str:
        async with connect(dsn) as conn:
            await _reset(conn)
            await seed_demo(conn, today=date(2026, 11, 1), reset=True)
            await conn.execute(f"DROP SCHEMA IF EXISTS {SNAPSHOT_SCHEMA} CASCADE")
            await conn.execute(f"CREATE SCHEMA {SNAPSHOT_SCHEMA}")
            statements = ["BEGIN;", "SET LOCAL session_replication_role = replica;"]
            for table in (*RESET_TABLES, "settings"):
                columns = await _snapshot_table(conn, table)
                if columns is None:
                    continue
                names = ", ".join(quote_ident(column) for column in columns)
                target = f"public.{quote_ident(table)}"
                statements.append(f"DELETE FROM {target};")
                statements.append(
                    f"INSERT INTO {target} ({names}) "
                    f"SELECT {names} FROM {SNAPSHOT_SCHEMA}.{quote_ident(table)};"
                )
            # A restore reads like a fresh seed: the audit trail's clock starts now, so a grader
            # looking for writes newer than a trial's start still sees the seed's rows.
            # shortcut: only audit_log's clock is refreshed; widen it if another table's matters.
            statements.append("UPDATE public.audit_log SET created_at = now();")
            statements.append("COMMIT;")
            return "\n".join(statements)

    async def _drop() -> None:
        async with connect(dsn) as conn:
            await conn.execute(f"DROP SCHEMA IF EXISTS {SNAPSHOT_SCHEMA} CASCADE")

    snapshot = asyncio.run(_build())
    yield snapshot
    asyncio.run(_drop())


@pytest.fixture
def settings() -> Settings:
    # App startup must not ALTER ROLE: the roles are cluster-wide and `migrated` already set
    # both passwords. Role connections read them from the global settings.
    return replace(get_settings(refresh=True), agent_ro_password=None, backup_password=None)


@pytest.fixture
def secure_settings(settings: Settings) -> Settings:
    return replace(settings, cookie_secure=True)


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def setup_token(client: TestClient) -> str:
    async with connect(_dsn()) as conn:
        return (await issue_setup_token(conn, ttl_hours=1)).token


@pytest.fixture
async def db() -> AsyncIterator[Connection]:
    async with connect(_dsn()) as conn:
        yield conn


@pytest.fixture
async def ledger(db: Connection) -> Connection:
    """A database with the Indian categories and the three system accounts."""
    await ensure_default_categories(db)
    await ensure_system_accounts(db)
    return db


@pytest.fixture
async def demo_ledger(db: Connection, demo_snapshot: str) -> Connection:
    """The seeded synthetic persona, restored from the session snapshot (today fixed)."""
    await db.execute(demo_snapshot)
    return db


@pytest.fixture
async def authed_client(client, setup_token, settings):
    """A TestClient with a registered passkey, a session, and a fresh passkey check.

    The L8 step-up wall is tested separately in `test_l8_ship.py`; most tests are about ledger
    behavior, so this fixture marks the session as freshly reverified. It asks for
    `demo_snapshot` first because building the snapshot empties every table, which would delete
    the setup token and session created below.
    """
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    begin = client.post("/api/auth/setup/begin", json={"token": setup_token})
    assert begin.status_code == 200, begin.text
    credential = authenticator.registration_credential(begin.json()["options"])
    verified = client.post(
        "/api/auth/setup/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )
    assert verified.status_code == 200, verified.text
    async with connect(_dsn()) as conn:
        await conn.execute("UPDATE sessions SET reverified_at = now() WHERE revoked_at IS NULL")
    return client
