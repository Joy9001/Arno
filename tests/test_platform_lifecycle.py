"""Platform lifecycle checks for CLI, jobs, migrations, and restores."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from arno.auth.setup import issue_setup_token
from arno.cli import app as cli_app
from arno.config import ConfigurationError, Settings
from arno.db.conn import connect
from arno.db.migrate import MigrationError, discover, run_migrations
from arno.demo.persona import seed_demo
from arno.jobs.runner import (
    JobRunner,
    _claim_one,
    recover_interrupted_jobs,
)
from arno.jobs.schedules import enqueue_due_schedules
from arno.ledger.accounts import list_accounts
from tests.conftest import fresh_database
from tests.fixtures.passkeys import complete_setup
from tests.software_authenticator import SoftwareAuthenticator


def test_telemetry_is_off_when_no_collector_endpoint_is_configured(monkeypatch, caplog):
    """`register()` with no endpoint targets gRPC localhost:4317 and blocks a host CLI."""
    import arno.observability as observability

    monkeypatch.setattr(observability, "_started", False)
    monkeypatch.setattr(observability, "_provider", None)
    for name in (
        "PHOENIX_COLLECTOR_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")

    with caplog.at_level(logging.INFO):
        provider = observability.bootstrap_observability()

    assert provider is None
    assert "PHOENIX_COLLECTOR_ENDPOINT" in caplog.text


async def test_export_reports_a_missing_pg_dump_instead_of_a_traceback(monkeypatch, demo_ledger):
    del demo_ledger
    monkeypatch.delenv("PG_DUMP", raising=False)
    monkeypatch.setattr("arno.ledger.export.shutil.which", lambda _name: None)

    result = await asyncio.to_thread(CliRunner().invoke, cli_app, ["export", "--format", "sql"])

    assert result.exit_code == 1, result.output
    assert "pg_dump is not installed" in result.output


PUBLIC_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/auth/state"),
    ("POST", "/api/auth/setup/begin"),
    ("POST", "/api/auth/setup/verify"),
    ("POST", "/api/auth/login/begin"),
    ("POST", "/api/auth/login/verify"),
    # Clearing a cookie needs no session: it always answers 204.
    ("POST", "/api/auth/logout"),
}


def test_every_non_public_api_route_requires_a_session(app):
    spec = app.openapi()
    routes = [
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
    ]
    assert len(routes) > 100

    with TestClient(app) as client:
        for method, path in routes:
            if (method, path) in PUBLIC_ROUTES:
                continue
            url = re.sub(r"\{[^}]+\}", "x", path)
            response = client.request(method, url, json={} if method != "GET" else None)
            assert response.status_code == 401, (method, path, response.status_code, response.text)


def test_a_cross_origin_request_gets_no_cors_permission(client):
    preflight = client.options(
        "/api/movements",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in preflight.headers
    assert (
        "access-control-allow-origin"
        not in client.get("/api/health", headers={"Origin": "https://evil.example"}).headers
    )


async def test_one_enrollment_link_registers_exactly_one_passkey(app, settings, db):
    """Two browsers can both open the link: one of them must lose the race."""
    del db
    async with connect(settings.database_url) as conn:
        token = (await issue_setup_token(conn, ttl_hours=1)).token

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_begin = await client.post("/api/auth/setup/begin", json={"token": token})
        second_begin = await client.post("/api/auth/setup/begin", json={"token": token})
        assert first_begin.status_code == 200, first_begin.text
        assert second_begin.status_code == 200, second_begin.text

        first = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
        second = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
        first_body = {
            "challenge_id": first_begin.json()["challenge_id"],
            "credential": first.registration_credential(first_begin.json()["options"]),
        }
        second_body = {
            "challenge_id": second_begin.json()["challenge_id"],
            "credential": second.registration_credential(second_begin.json()["options"]),
        }
        responses = await asyncio.gather(
            client.post("/api/auth/setup/verify", json=first_body),
            client.post("/api/auth/setup/verify", json=second_body),
        )

    assert sorted(response.status_code for response in responses) == [200, 400]
    async with connect(settings.database_url) as conn:
        assert await conn.fetchval("SELECT count(*) FROM users") == 1
        assert await conn.fetchval("SELECT count(*) FROM webauthn_credentials") == 1


async def test_an_expired_session_is_refused(client, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200
    assert client.get("/api/auth/session").status_code == 200

    await db.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")

    assert client.get("/api/auth/session").status_code == 401


async def test_a_session_cookie_alone_authenticates_and_satisfies_step_up(session_client):
    """The fast fixture is a real session: no passkey ceremony, but the same request boundary."""
    assert session_client.get("/api/auth/session").status_code == 200
    assert session_client.get("/api/accounts").status_code == 200
    assert session_client.get("/api/export/json").status_code == 200


async def test_an_expired_first_run_token_is_refused(client, settings, db):
    async with connect(settings.database_url) as conn:
        token = (await issue_setup_token(conn, ttl_hours=1)).token
    await db.execute("UPDATE setup_tokens SET expires_at = now() - interval '1 second'")

    response = client.post("/api/auth/setup/begin", json={"token": token})

    assert response.status_code == 401


async def test_two_workers_never_claim_the_same_due_job(db):
    await db.execute("INSERT INTO jobs (key, kind) VALUES ('claim:1', 'recompute_baselines')")

    async with connect() as first, connect() as second:
        claimed = await asyncio.gather(_claim_one(first), _claim_one(second))

    ids = [job["id"] for job in claimed if job is not None]
    assert len(ids) == 1
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE status = 'running'") == 1


async def test_a_job_interrupted_mid_run_is_retried_exactly_once(db):
    runs: list[str] = []

    async def handler(payload):
        runs.append(payload["period"])
        return {"ok": True}

    await db.execute(
        """
        INSERT INTO jobs (key, kind, status, started_at, payload)
        VALUES ('crash:1', 'crash', 'running', now(), '{"period": "2026-W44"}'::jsonb)
        """
    )
    assert await recover_interrupted_jobs(db) == 1

    runner = JobRunner(registry={"crash": handler}, timeouts={"crash": 5})
    await runner.run_once()

    assert runs == ["2026-W44"]
    row = await db.fetchrow("SELECT status, attempts, result FROM jobs WHERE key = 'crash:1'")
    assert row["status"] == "done" and row["result"] == {"ok": True}


async def test_a_due_schedule_creates_one_job_and_does_not_repeat_it(db):
    await db.execute(
        """
        INSERT INTO job_schedules (kind, payload, rule, next_run_at)
        VALUES ('reminders', '{}'::jsonb, 'FREQ=DAILY;BYHOUR=8;BYMINUTE=0',
                now() - interval '2 days')
        """
    )
    now = datetime.now(ZoneInfo("Asia/Kolkata"))

    assert await enqueue_due_schedules(db, now=now) == 1
    assert await enqueue_due_schedules(db, now=now) == 0
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE kind = 'reminders'") == 1


async def test_two_simultaneous_passkey_deletes_cannot_remove_the_last_one(authed_client, db):
    from arno.auth.sessions import COOKIE_NAME

    await db.execute(
        """
        INSERT INTO webauthn_credentials
            (user_id, credential_id, public_key, sign_count, transports, aaguid, name)
        SELECT user_id, decode('0102', 'hex'), public_key, sign_count, transports, aaguid, 'Second'
        FROM webauthn_credentials LIMIT 1
        """
    )
    ids = [row["id"] for row in await db.fetch("SELECT id FROM webauthn_credentials")]
    assert len(ids) == 2
    token = authed_client.cookies[COOKIE_NAME]

    transport = httpx.ASGITransport(app=authed_client.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        cookies={COOKIE_NAME: token},
    ) as client:
        responses = await asyncio.gather(
            client.delete(f"/api/auth/passkeys/{ids[0]}"),
            client.delete(f"/api/auth/passkeys/{ids[1]}"),
        )

    assert sorted(response.status_code for response in responses) == [204, 409]
    assert await db.fetchval("SELECT count(*) FROM webauthn_credentials") == 1


async def test_clean_shutdown_leaves_an_interrupted_row_for_boot_recovery(db):
    started = asyncio.Event()
    resume = asyncio.Event()

    async def slow(payload):
        del payload
        started.set()
        await resume.wait()
        return {}

    runner = JobRunner(registry={"slow": slow}, concurrency=1, max_idle_seconds=60)
    await db.execute("INSERT INTO jobs (key, kind) VALUES ('stop:1', 'slow')")
    runner.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
    finally:
        await runner.stop()

    assert await recover_interrupted_jobs(db) == 1
    row = await db.fetchrow("SELECT status FROM jobs WHERE key = 'stop:1'")
    assert row["status"] == "pending"


def test_a_migration_include_still_cannot_escape_the_db_directory(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (tmp_path / "outside.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (migrations / "0001_escape.sql").write_text("\\i ../outside.sql\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="escapes the db directory"):
        discover(migrations)


async def test_a_changed_migration_is_refused(tmp_path, clean_database: None):
    del clean_database  # the worker database must exist before the checksum check runs
    # Expanded copies are self-contained: the vendored includes stay where they belong.
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for migration in discover():
        path = migrations / f"{migration.version:04d}_{migration.name}.sql"
        path.write_text(migration.sql, encoding="utf-8")
    first = migrations / "0001_pgledger.sql"
    first.write_text(first.read_text(encoding="utf-8") + "\n-- edited after it ran\n")

    with pytest.raises(MigrationError, match="changed after it was applied"):
        async with connect() as conn:
            await run_migrations(conn, migrations)


@pytest.mark.xdist_group("cluster")
async def test_a_failed_migration_leaves_the_database_unchanged(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_good.sql").write_text("CREATE TABLE audit_good (id int);\n")
    (migrations / "0002_bad.sql").write_text(
        "CREATE TABLE audit_bad (id int);\nINSERT INTO audit_missing VALUES (1);\n"
    )

    async with fresh_database("arno_platform_bad_migration") as dsn:
        async with connect(dsn) as conn:
            with pytest.raises(asyncpg.PostgresError):
                await run_migrations(conn, migrations)

            assert await conn.fetchval("SELECT to_regclass('public.audit_good')") is not None
            assert await conn.fetchval("SELECT to_regclass('public.audit_bad')") is None
            assert await conn.fetchval("SELECT max(version) FROM schema_version") == 1


async def test_app_role_is_not_superuser_and_holds_expected_attributes(db):
    role = await db.fetchrow_required(
        """
        SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb,
               rolcanlogin, rolreplication, rolbypassrls
        FROM pg_roles
        WHERE rolname = 'app'
        """
    )
    assert dict(role) == {
        "rolsuper": False,
        "rolinherit": True,
        "rolcreaterole": True,
        "rolcreatedb": True,
        "rolcanlogin": True,
        "rolreplication": False,
        "rolbypassrls": False,
    }

    memberships = {
        row["role"]: (row["admin_option"], row["inherit_option"], row["set_option"])
        for row in await db.fetch(
            """
            SELECT r.rolname AS role, a.admin_option, a.inherit_option, a.set_option
            FROM pg_auth_members a
            JOIN pg_roles r ON r.oid = a.roleid
            JOIN pg_roles m ON m.oid = a.member
            WHERE m.rolname = 'app'
            """
        )
    }
    assert memberships == {
        "pg_signal_backend": (False, True, True),
        "agent_ro": (True, False, False),
        "backup": (True, False, False),
    }

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await db.execute("SELECT * FROM pg_authid LIMIT 1")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await db.execute("ALTER SYSTEM SET work_mem = '64MB'")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await db.execute("COPY (SELECT 1) TO PROGRAM 'cat'")


async def test_warn_if_superuser_logs_when_superuser_and_is_silent_for_normal_role(
    db, caplog, monkeypatch
):
    from arno.db.bootstrap import warn_if_superuser

    with caplog.at_level("WARNING"):
        assert await warn_if_superuser(db) is False
    assert caplog.records == []

    async def _fake_fetchrow(
        query: str, *args: object, timeout: float | None = None
    ) -> dict[str, object]:
        return {"role": "app", "rolsuper": True}

    monkeypatch.setattr(db, "fetchrow", _fake_fetchrow)
    with caplog.at_level("WARNING"):
        assert await warn_if_superuser(db) is True
    assert any("fresh database volume" in rec.getMessage() for rec in caplog.records)


async def test_restore_verification_catches_a_broken_ledger(demo_ledger):
    from arno.backup_restore import verify_database

    checks = await verify_database(demo_ledger)
    assert checks and all(checks.values())

    await demo_ledger.execute(
        """
        UPDATE pgledger_entries SET amount = amount + 1
        WHERE id = (SELECT id FROM pgledger_entries ORDER BY id LIMIT 1)
        """
    )
    checks = await verify_database(demo_ledger)

    assert checks["ledger_transfers_balance"] is False
    assert checks["account_balances_match_entries"] is False


def test_a_bad_number_in_the_environment_names_the_variable(monkeypatch):
    monkeypatch.setenv("ARNO_SESSION_TTL_DAYS", "thirty")

    with pytest.raises(ConfigurationError, match="ARNO_SESSION_TTL_DAYS"):
        Settings.from_env()


def test_a_typo_in_a_boolean_is_an_error_not_silently_false(monkeypatch):
    monkeypatch.setenv("ARNO_COOKIE_SECURE", "ture")

    with pytest.raises(ConfigurationError, match="ARNO_COOKIE_SECURE"):
        Settings.from_env()


def test_a_zero_lifetime_is_refused(monkeypatch):
    monkeypatch.setenv("ARNO_SETUP_TOKEN_TTL_HOURS", "0")

    with pytest.raises(ConfigurationError, match="ARNO_SETUP_TOKEN_TTL_HOURS"):
        Settings.from_env()


def test_an_origin_with_a_path_is_refused(monkeypatch):
    """WebAuthn compares the origin exactly; a path would fail every ceremony later."""
    monkeypatch.setenv("ARNO_RP_ORIGIN", "https://box.tailnet.ts.net/app")

    with pytest.raises(ConfigurationError, match="ARNO_RP_ORIGIN"):
        Settings.from_env()


def test_a_blank_rp_id_names_its_own_variable(monkeypatch):
    monkeypatch.setenv("ARNO_RP_ID", "")
    monkeypatch.delenv("ARNO_RP_ORIGIN", raising=False)

    with pytest.raises(ConfigurationError, match="ARNO_RP_ID"):
        Settings.from_env()


DEMO_ANCHORS = [
    date(2026, 11, 9),
    date(2026, 11, 1),
    date(2026, 10, 31),
    date(2026, 2, 28),
    date(2024, 2, 29),
    date(2025, 12, 31),
]


async def test_a_future_demo_anchor_is_a_clear_error(db):
    from arno.demo.persona import DemoSeedError

    with pytest.raises(DemoSeedError, match="in the future"):
        await seed_demo(db, today=date(2999, 1, 1), reset=True)


@pytest.mark.parametrize("anchor", DEMO_ANCHORS)
async def test_demo_persona_keeps_every_ledger_invariant_at_any_anchor(db, anchor):
    summary = await seed_demo(db, today=anchor, reset=True)

    unbalanced = await db.fetchval(
        """
        SELECT count(*) FROM (
            SELECT transfer_id FROM pgledger_entries
            GROUP BY transfer_id HAVING sum(amount) <> 0
        ) AS bad
        """
    )
    assert unbalanced == 0

    drifted = await db.fetchval(
        """
        SELECT count(*) FROM (
            SELECT a.id
            FROM pgledger_accounts a
            LEFT JOIN (
                SELECT account_id, sum(amount) AS total FROM pgledger_entries GROUP BY account_id
            ) e ON e.account_id = a.id
            WHERE coalesce(e.total, 0) <> a.balance
        ) AS bad
        """
    )
    assert drifted == 0

    assert await db.fetchval("SELECT count(*) FROM movements WHERE date > $1", anchor) == 0

    owed = {
        row["display_name"]: row["balance_paise"] for row in await db.fetch("SELECT * FROM v_owed")
    }
    assert owed["Ravi"] == 200_000
    assert owed["Amit"] == 20_000

    card = {account["name"]: account for account in await list_accounts(db)}["Slice card"]
    assert card["card_outstanding_paise"] is None

    payments = await db.fetchval("SELECT count(*) FROM movements WHERE kind = 'loan'")
    loan = await db.fetchrow(
        """
        SELECT pa.balance FROM pgledger_accounts pa
        JOIN accounts a ON a.ledger_account_id = pa.id
        WHERE a.name = 'Home loan'
        """
    )
    interest = await db.fetchval(
        """
        SELECT coalesce(sum(s.amount_paise), 0) FROM v_spending s
        WHERE s.category_name = 'Loan Interest'
        """
    )
    # The persona splits each EMI with the ledger's own rule, so the loan falls by its principal
    # (the payment minus the interest booked), not by a fixed 2,800,000 a month.
    assert interest > payments * 350_000
    assert loan["balance"] == -350_000_000 + payments * 3_150_000 - interest

    assert summary["statement_coverage"]["months"] == [
        f"{month:04d}-{number:02d}" for month, number in _complete_months(anchor)
    ]


def _complete_months(anchor: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = anchor.year, anchor.month
    for _ in range(3):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        months.append((year, month))
    return list(reversed(months))
