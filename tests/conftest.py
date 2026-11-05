"""Test setup: a scratch Postgres, and model requests blocked.

Tests refuse to run against a database whose name doesn't end in `_test`, so a stray
DATABASE_URL can't wipe a real ledger. Every outbound socket to a non-loopback address is
refused, and each model role's endpoint defaults to a closed loopback port.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import replace
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

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
from fin_agent.db.migrate import run_migrations  # noqa: E402
from fin_agent.ledger.accounts import ensure_system_accounts  # noqa: E402
from fin_agent.ledger.categories import ensure_default_categories  # noqa: E402
from tests.software_authenticator import SoftwareAuthenticator  # noqa: E402

models.ALLOW_MODEL_REQUESTS = False

RESET_TABLES = """
TRUNCATE
    users, setup_tokens, webauthn_challenges, sessions,
    pgledger_idempotency_keys, pgledger_entries, pgledger_transfers, pgledger_accounts,
    accounts, payees, categories, movements, transfer_labels, captures,
    imports, statement_lines, statement_layouts, attachments,
    schedules, goals, scheduled_questions,
    category_rules, baselines,
    conversations, messages, preferences, audit_log,
    notifications, push_subscriptions, jobs, job_schedules, backup_runs,
    vapid_keys, risk_state
RESTART IDENTITY CASCADE
"""


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise pytest.UsageError(
            "DATABASE_URL must point at a scratch database whose name ends in _test"
        )
    return dsn


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
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        applied = run_migrations(conn)
        set_agent_ro_password(conn, os.environ.get("AGENT_RO_PASSWORD"))
        set_backup_password(conn, os.environ.get("BACKUP_PASSWORD"))
    return applied


@pytest.fixture(autouse=True)
def clean_database(migrated: list[int]) -> None:
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        conn.execute(RESET_TABLES)
        conn.execute(
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
        conn.execute("INSERT INTO risk_state (id) VALUES (1) ON CONFLICT DO NOTHING")


@pytest.fixture
def settings() -> Settings:
    return get_settings(refresh=True)


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
def setup_token(client: TestClient) -> str:
    with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
        return issue_setup_token(conn, ttl_hours=1).token


@pytest.fixture
def db():
    with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def ledger(db):
    """A database with the Indian categories and the three system accounts."""
    ensure_default_categories(db)
    ensure_system_accounts(db)
    return db


@pytest.fixture
def demo_ledger(db):
    """The seeded synthetic persona, as a fixture (today fixed for deterministic tests)."""
    from datetime import date

    from fin_agent.demo.persona import seed_demo

    seed_demo(db, today=date(2026, 11, 1), reset=True)
    return db


@pytest.fixture
def authed_client(client, setup_token, settings):
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
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        conn.execute("UPDATE sessions SET reverified_at = now() WHERE revoked_at IS NULL")
    return client
