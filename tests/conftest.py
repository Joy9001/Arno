"""Test setup: a scratch Postgres, and model requests blocked.

Tests refuse to run against a database whose name doesn't end in `_test`, so a stray
DATABASE_URL can't wipe a real ledger. Every outbound socket to a non-loopback address is
refused, and each model role's endpoint defaults to a closed loopback port.

Database fixtures clone shared templates. Pure tests run without Postgres.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import secrets
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
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

_LOCAL_TEMP = tempfile.TemporaryDirectory(prefix="arno-tests-")

# One BLAS thread per worker. Without this, every numeric test multiplies OpenBLAS's 24 threads
# across the xdist workers and pins a laptop's cores.
for _thread_limit in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_thread_limit] = "1"


def _database_name(dsn: str) -> str:
    if "://" in dsn:
        return urlsplit(dsn).path.lstrip("/")
    return next((token[7:] for token in shlex.split(dsn) if token.startswith("dbname=")), "")


def _tag_database(tag: str) -> None:
    """Give this process its own copy of the scratch database.

    Two pytest runs against the same DATABASE_URL must not delete each other's tables.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    original = _database_name(dsn)
    if not dsn or not original.endswith("_test"):
        return
    prefix = original.removesuffix("_test")[: 57 - len(tag)]
    name = f"{prefix}_{tag}_test"
    if "://" in dsn:
        parts = urlsplit(dsn)
        dsn = urlunsplit(parts._replace(path=f"/{name}"))
    else:
        tokens = [
            f"dbname={name}" if token.startswith("dbname=") and token.endswith("_test") else token
            for token in shlex.split(dsn)
        ]
        dsn = shlex.join(tokens)
    os.environ["DATABASE_URL"] = dsn


def _per_worker_env() -> None:
    """Point each xdist worker at its own database, and give every process its own task folders.

    Runs before `arno` is imported, so `Settings.from_env()` sees these values. The controller is
    left alone: rewriting its DATABASE_URL would make every worker inherit that tag and rewrite
    it again. `pytest_configure` tags a serial run instead.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        _tag_database("_".join(part for part in (worker, os.environ.get("ARNO_TEST_RUN")) if part))
    temp = Path(_LOCAL_TEMP.name)
    os.environ["ARNO_TASKS_DIR"] = str(temp / "tasks")
    os.environ["ARNO_PRE_MIGRATION_DIR"] = str(temp / "pre-migration")


_per_worker_env()

# Set before importing arno, so Settings.from_env sees them.
os.environ.setdefault("ARNO_RP_ID", "localhost")
os.environ.setdefault("ARNO_RP_ORIGIN", "http://localhost:8000")
os.environ.setdefault("ARNO_COOKIE_SECURE", "0")
for _role in ("CHAT", "VISION", "STT"):
    os.environ.setdefault(f"{_role}_BASE_URL", "http://127.0.0.1:9/v1")
    os.environ.setdefault(f"{_role}_MODEL", "blocked-in-tests")

# The job loop is tested directly; the API tests must not race it.
os.environ.setdefault("ARNO_JOBS_ENABLED", "0")

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

from arno import config as arno_config  # noqa: E402
from arno.api.app import create_app  # noqa: E402
from arno.auth.sessions import COOKIE_NAME, create_session  # noqa: E402
from arno.auth.setup import issue_setup_token  # noqa: E402
from arno.config import Settings, get_settings  # noqa: E402
from arno.db.bootstrap import set_agent_ro_password, set_backup_password  # noqa: E402
from arno.db.conn import (  # noqa: E402
    Connection,
    connect,
    conninfo_to_dict,
    make_conninfo,
    quote_ident,
)
from arno.db.migrate import discover, run_migrations  # noqa: E402
from arno.demo.persona import seed_demo  # noqa: E402
from arno.demo.template import (  # noqa: E402
    clone_database,
    create_database,
    database_dsn,
    drop_database,
)
from arno.importers.passwords import PASSWORDS  # noqa: E402
from arno.importers.seed_rules import ensure_seed_rules  # noqa: E402
from arno.jobs.schedules import ensure_builtin_schedules  # noqa: E402
from arno.ledger.accounts import ensure_system_accounts  # noqa: E402
from arno.ledger.categories import ensure_default_categories  # noqa: E402
from arno.notifications import ensure_vapid_keys  # noqa: E402
from tests.software_authenticator import SoftwareAuthenticator  # noqa: E402

# A module-level `bool` flag the library itself rebinds; its inferred type is `Literal[True]`.
models.ALLOW_MODEL_REQUESTS = False  # pyrefly: ignore[bad-assignment]

# Ordinary cases reset in place. Demo cases keep native template cloning.
RESET_TABLES = (
    "model_credentials",
    "llm_calls",
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
    "movement_legs",
    "captures",
    "imports",
    "statement_lines",
    "statement_layouts",
    "parser_versions",
    "attachments",
    "schedules",
    "series_detections",
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
_API_CATALOG = ("categories", "category_rules", "job_schedules", "vapid_keys")
_TRUNCATE_API = (
    "TRUNCATE "
    + ", ".join(
        table for table in RESET_TABLES if table not in (*_API_CATALOG, "scheduled_questions")
    )
    + " RESTART IDENTITY CASCADE"
)

# Insert order: every FK target precedes its children.
_STATIC_TABLES = (
    "settings",
    "risk_state",
    "pgledger_accounts",
    "categories",
    "job_schedules",
    "vapid_keys",
    "category_rules",
    "accounts",
)
_STATIC_SNAPSHOT: dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]] | None = None


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise pytest.UsageError(
            "This test needs a database: set DATABASE_URL to a scratch database whose name "
            "ends in _test, e.g. postgresql://app:app@127.0.0.1:5433/arno_test"
        )
    return dsn


def scratch_name(suffix: str) -> str:
    """Give worker-local auxiliary databases names within PostgreSQL's identifier limit."""
    name = f"{suffix}_{conninfo_to_dict(_dsn())['dbname']}"
    if len(name) <= 63:
        return name
    fingerprint = hashlib.sha256(name.encode()).hexdigest()[:12]
    return f"{name[:45]}_{fingerprint}_test"


@asynccontextmanager
async def fresh_database(suffix: str) -> AsyncGenerator[str]:
    """Create an empty scratch database named after this worker, yield its DSN, and drop it."""
    base = _dsn()
    name = scratch_name(suffix)
    await create_database(base, name)
    try:
        yield database_dsn(base, name)
    finally:
        await drop_database(base, name)


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


def _template_name(kind: str) -> str:
    return f"arno_{os.environ['ARNO_TEST_RUN']}_{kind}_test"


def _template_flag(kind: str) -> Path:
    return Path(tempfile.gettempdir()) / _template_name(kind)


async def _clone(source: str, target: str) -> None:
    base = _dsn()
    await clone_database(base, source=source, target=target, terminate=False)
    # CREATE DATABASE copies each table's grants but starts with default database ACLs.
    async with connect(database_dsn(base, target)) as conn:
        name = quote_ident(target)
        await conn.execute(
            f"GRANT CONNECT ON DATABASE {name} TO agent_ro, backup; "
            f"REVOKE TEMP ON DATABASE {name} FROM PUBLIC"
        )


async def _ensure_template(kind: str) -> str:
    base = _dsn()
    name = _template_name(kind)
    flag = _template_flag(kind)
    if flag.exists():
        return name
    async with cluster_lock():
        if flag.exists():
            return name
        try:
            if kind == "clean":
                await create_database(base, name)
            else:
                await _clone(_template_name("clean"), name)
            async with connect(database_dsn(base, name)) as conn:
                if kind == "clean":
                    await run_migrations(conn)
                    await set_agent_ro_password(conn, os.environ.get("AGENT_RO_PASSWORD"))
                    await set_backup_password(conn, os.environ.get("BACKUP_PASSWORD"))
                elif kind == "demo":
                    await seed_demo(conn, today=date(2026, 11, 1))
                else:
                    await ensure_default_categories(conn)
                    await ensure_system_accounts(conn)
                    await ensure_seed_rules(conn)
                    await ensure_vapid_keys(conn)
                    await ensure_builtin_schedules(conn)
        except BaseException:
            await drop_database(base, name)
            raise
        flag.touch()
    return name


async def _load_static_snapshot() -> None:
    """Capture the seeded static baseline once, before any test can edit it."""
    global _STATIC_SNAPSHOT
    base = _dsn()
    source = database_dsn(base, await _ensure_template("api"))
    snapshot: dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]] = {}
    async with connect(source) as conn:
        for table in _STATIC_TABLES:
            rows = await conn.fetch(f"SELECT * FROM public.{quote_ident(table)} ORDER BY id")
            columns = tuple(rows[0].keys()) if rows else ()
            snapshot[table] = (columns, [tuple(row[c] for c in columns) for row in rows])
    _STATIC_SNAPSHOT = snapshot


def _static_snapshot() -> dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]]:
    assert _STATIC_SNAPSHOT is not None, "static baseline must be captured in `migrated`"
    return _STATIC_SNAPSHOT


async def _restore_table(conn: Connection, table: str) -> None:
    columns, rows = _static_snapshot()[table]
    if not rows:
        return
    names = ", ".join(quote_ident(column) for column in columns)
    placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
    await conn.executemany(
        f"INSERT INTO public.{quote_ident(table)} ({names}) VALUES ({placeholders})", rows
    )


async def _catalog_changed(conn: Connection, table: str) -> bool:
    columns, expected = _static_snapshot()[table]
    records = await conn.fetch(f"SELECT * FROM public.{quote_ident(table)} ORDER BY id")
    return [tuple(row[column] for column in columns) for row in records] != expected


async def _mutable_reset(*, api: bool) -> None:
    """Empty dynamic tables in place and reapply the deterministic baseline."""
    async with connect(_dsn()) as conn, conn.transaction():
        await conn.execute(_TRUNCATE_API if api else _TRUNCATE)
        if await conn.fetchval("SELECT to_regclass('public.spend_counters')"):
            await conn.execute("TRUNCATE spend_counters, spend_dedup")
        # `settings` is not in RESET_TABLES; clear it before restoring its baseline row.
        await conn.execute("DELETE FROM settings")
        # Both rows come from the migrations, so every test kind needs them.
        await _restore_table(conn, "settings")
        await _restore_table(conn, "risk_state")
        if api:
            # DELETE follows the FK only for user-created schedules and keeps the builtins.
            await conn.execute("DELETE FROM scheduled_questions")
            categories_dirty = await _catalog_changed(conn, "categories")
            if categories_dirty:
                await conn.execute("DELETE FROM category_rules; DELETE FROM categories")
                await _restore_table(conn, "categories")
                await _restore_table(conn, "category_rules")
            for table in ("category_rules", "job_schedules", "vapid_keys"):
                # Category repair already restored the rules; keep the standalone dirty-rules pass.
                if table == "category_rules" and categories_dirty:
                    continue
                if await _catalog_changed(conn, table):
                    await conn.execute(f"DELETE FROM public.{quote_ident(table)}")
                    await _restore_table(conn, table)
            # System entries share tables with mutable accounts and their balances.
            await _restore_table(conn, "pgledger_accounts")
            await _restore_table(conn, "accounts")


@pytest.fixture(scope="session")
def migrated() -> Iterator[list[int]]:
    """Provide the worker's database and migrate the session's clean template once."""
    base = _dsn()
    name = str(conninfo_to_dict(base)["dbname"])

    async def start() -> None:
        await _clone(await _ensure_template("clean"), name)
        await _load_static_snapshot()

    asyncio.run(start())
    try:
        yield [migration.version for migration in discover()]
    finally:
        asyncio.run(drop_database(base, name))


def pytest_configure(config: pytest.Config) -> None:
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        os.environ["ARNO_TEST_RUN"] = uuid4().hex[:16]
        if not config.getoption("numprocesses", None):
            _tag_database(f"local{os.getpid()}_{os.environ['ARNO_TEST_RUN']}")
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        if not _database_name(dsn).endswith("_test"):
            raise pytest.UsageError("DATABASE_URL must name a scratch database ending in _test")
        host = conninfo_to_dict(dsn).get("host") or ""
        if not (_is_local(host) or host.startswith("/")):
            raise pytest.UsageError("The scratch database must be on loopback or a local socket")


def pytest_sessionfinish(session: pytest.Session) -> None:
    _LOCAL_TEMP.cleanup()
    if os.environ.get("PYTEST_XDIST_WORKER") or not os.environ.get("DATABASE_URL"):
        return

    async def discard_templates() -> None:
        for kind in ("demo", "api", "clean"):
            flag = _template_flag(kind)
            if flag.exists():
                await drop_database(_dsn(), _template_name(kind))
                flag.unlink()

    asyncio.run(discard_templates())


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


@pytest.fixture(autouse=True)
def clean_process_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep cached settings and statement passwords local to each test."""
    monkeypatch.setattr(arno_config, "_cached", None)
    PASSWORDS.clear()
    yield
    PASSWORDS.clear()


@pytest.fixture
async def clean_database(migrated: list[int], request: pytest.FixtureRequest) -> None:
    """Reset the worker database before opening any per-test connections.

    Clean and API cases reset in place (portable: no per-test CREATE DATABASE); demo cases
    keep native template cloning because the persona's rows are too wide to reset cheaply.
    """
    del migrated
    fixtures = request.fixturenames
    kind = "demo" if "demo_ledger" in fixtures else "api" if "app" in fixtures else "clean"
    if kind == "demo":
        base = _dsn()
        await _clone(await _ensure_template("demo"), str(conninfo_to_dict(base)["dbname"]))
        async with connect(base) as conn:
            await conn.execute("UPDATE public.audit_log SET created_at = now()")
        return
    await _mutable_reset(api=kind == "api")


@pytest.fixture
def settings() -> Settings:
    # App startup must not ALTER ROLE: the roles are cluster-wide and `migrated` already set
    # both passwords. Role connections read them from the global settings.
    return replace(get_settings(refresh=True), agent_ro_password=None, backup_password=None)


@pytest.fixture
def secure_settings(settings: Settings) -> Settings:
    return replace(settings, cookie_secure=True)


@pytest.fixture
def app(settings: Settings, clean_database: None):
    del clean_database
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def setup_token(client: TestClient) -> str:
    async with connect(_dsn()) as conn:
        return (await issue_setup_token(conn, ttl_hours=1)).token


@pytest.fixture
async def db(clean_database: None) -> AsyncIterator[Connection]:
    del clean_database
    async with connect(_dsn()) as conn:
        yield conn


@pytest.fixture
async def ledger(db: Connection) -> Connection:
    """A database with the Indian categories and the three system accounts."""
    await ensure_default_categories(db)
    await ensure_system_accounts(db)
    return db


@pytest.fixture
def demo_ledger(db: Connection) -> Connection:
    """The synthetic persona, cloned by clean_database before the connection opened."""
    return db


@pytest.fixture
async def session_client(client: TestClient, settings: Settings) -> TestClient:
    """A TestClient holding a real session cookie, without a WebAuthn ceremony.

    Use `authed_client` when the test is about passkeys, step-up, or the credential list; this one
    is for the many tests that only need an authenticated request.
    """
    async with connect(_dsn()) as conn:
        user = await conn.fetchrow_required(
            "INSERT INTO users (webauthn_user_handle) VALUES ($1) RETURNING id",
            secrets.token_bytes(16),
        )
        token, _ = await create_session(conn, user["id"], ttl_days=settings.session_ttl_days)
        await conn.execute("UPDATE sessions SET reverified_at = now() WHERE revoked_at IS NULL")
    client.cookies.set(COOKIE_NAME, token)
    return client


@pytest.fixture
async def authed_client(
    client: TestClient, setup_token: str, settings: Settings
) -> AsyncIterator[TestClient]:
    """A TestClient with a registered passkey, a session, and a fresh passkey check.

    The L8 step-up wall is tested separately in `test_l8_ship.py`; most tests are about ledger
    behavior, so this fixture marks the session as freshly reverified.
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
    yield client
