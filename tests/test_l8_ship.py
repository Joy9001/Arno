from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from arno.api.capture import MAX_CAPTURE_BYTES
from arno.api.imports import MAX_UPLOAD_BYTES
from arno.api.middleware import CONTENT_SECURITY_POLICY
from arno.backup_restore import restore_from_restic
from arno.config import get_settings
from arno.db.conn import Connection, connect
from arno.db.migrate import Migration
from arno.db.pre_migration import dump_before_migrating
from arno.demo.template import database_dsn, drop_database
from arno.jobs.backup import run_backup
from tests.conftest import cluster_lock, scratch_name
from tests.fixtures.passkeys import complete_setup
from tests.software_authenticator import SoftwareAuthenticator

REQUIRED_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)


def test_security_headers_are_the_exact_required_policy(client):
    response = client.get("/api/health")

    assert CONTENT_SECURITY_POLICY == REQUIRED_POLICY
    assert response.headers["content-security-policy"] == REQUIRED_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_a_tailnet_host_is_accepted(client):
    response = client.get("/api/health", headers={"host": "mini-pc.tailnet.ts.net"})

    assert response.status_code == 200


def test_both_upload_paths_share_the_25_mb_cap():
    assert MAX_UPLOAD_BYTES == 25 * 1024 * 1024
    assert MAX_CAPTURE_BYTES == MAX_UPLOAD_BYTES


async def test_export_and_passkey_changes_need_a_fresh_passkey(client, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200

    blocked = client.get("/api/export/json")
    assert blocked.status_code == 403
    assert blocked.headers["x-arno-step-up"] == "passkey"

    begin = client.post("/api/auth/reverify/begin")
    assert begin.status_code == 200, begin.text
    credential = authenticator.authentication_credential(begin.json()["options"])
    verified = client.post(
        "/api/auth/reverify/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )
    assert verified.status_code == 200, verified.text

    assert client.get("/api/export/json").status_code == 200
    assert client.post("/api/auth/enrollments").status_code == 200

    passkey_id = (await db.fetchrow("SELECT id FROM webauthn_credentials"))["id"]
    renamed = client.patch(f"/api/auth/passkeys/{passkey_id}", json={"name": "Laptop"})
    assert renamed.status_code == 200, renamed.text

    await db.execute("UPDATE sessions SET reverified_at = now() - interval '6 minutes'")
    assert client.get("/api/export/json").status_code == 403


async def test_pre_migration_dump_runs_only_when_a_migration_is_pending(db, settings, tmp_path):
    from dataclasses import replace

    assert await dump_before_migrating(db, replace(settings, pre_migration_dir=tmp_path)) is None

    import arno.db.pre_migration as pre_migration

    original_pending = pre_migration.pending
    original_dump = pre_migration.export_pg_dump

    async def fake_pending(conn: Connection, directory: Path | None = None):
        del conn, directory
        return [Migration(version=1, name="0001_x.sql", path=Path("0001_x.sql"), sql="SELECT 1;")]

    def fake_dump(database_url: str, *, timeout: int = 120) -> bytes:
        del database_url, timeout
        return b"-- pre-migration dump\n"

    pre_migration.pending = fake_pending
    pre_migration.export_pg_dump = fake_dump
    try:
        path = await dump_before_migrating(db, replace(settings, pre_migration_dir=tmp_path))
    finally:
        pre_migration.pending = original_pending
        pre_migration.export_pg_dump = original_dump

    assert path is not None
    assert path.read_bytes() == b"-- pre-migration dump\n"


def _binary(env_name: str, name: str) -> str | None:
    # Same lookup as arno.backup_restore: CI pins the PostgreSQL 18 client by env var.
    return os.environ.get(env_name) or shutil.which(name)


@pytest.mark.xdist_group("cluster")
@pytest.mark.skipif(
    not (
        _binary("RESTIC", "restic")
        and _binary("PG_DUMP", "pg_dump")
        and _binary("PG_RESTORE", "pg_restore")
    ),
    reason="restic or the PostgreSQL 18 client is missing",
)
async def test_restore_drill_brings_back_every_area(demo_ledger, tmp_path: Path, monkeypatch):
    db = demo_ledger
    preference = (
        await db.fetchrow(
            "INSERT INTO preferences (body, created_by) "
            "VALUES ('Prefers UPI', 'agent') RETURNING id"
        )
    )["id"]
    conversation = (
        await db.fetchrow("INSERT INTO conversations (title) VALUES ('L8 drill') RETURNING id")
    )["id"]
    message = (
        await db.fetchrow(
            """
            INSERT INTO messages (conversation_id, client_id, role, content, body_text)
            VALUES ($1, 'l8-drill-1', 'user', '[]'::jsonb, 'Dinner at Swiggy')
            RETURNING id
            """,
            conversation,
        )
    )["id"]
    await db.execute(
        """
        INSERT INTO attachments (kind, filename, content_type, size_bytes, sha256, data)
        VALUES ('photo', 'receipt.jpg', 'image/jpeg', 3, 'abc123', $1)
        """,
        b"\x01\x02\x03",
    )
    await db.execute(
        """
        INSERT INTO audit_log (actor, message_id, group_id, table_name, row_id, after)
        VALUES ('agent', $1, 'l8-group', 'preferences', $2, '{"body":"Prefers UPI"}'::jsonb)
        """,
        message,
        preference,
    )

    get_settings(refresh=True)
    repo = str(tmp_path / "restic-repo")
    monkeypatch.setenv("RESTIC_REPOSITORY", repo)
    monkeypatch.setenv("RESTIC_PASSWORD", "local-drill-password")
    assert await run_backup(db) is True

    target = database_dsn(os.environ["DATABASE_URL"], scratch_name("arno_l8_restore"))
    try:
        async with cluster_lock():
            checks = await restore_from_restic(
                repository=repo,
                password="local-drill-password",
                target_dsn=target,
                force=True,
            )
        assert all(checks.values()), checks
        async with connect(target) as restored:
            assert (await restored.fetchrow_required("SELECT body FROM preferences"))["body"] == (
                "Prefers UPI"
            )
            assert (await restored.fetchrow_required("SELECT body_text FROM messages"))[
                "body_text"
            ] == ("Dinner at Swiggy")
            assert (await restored.fetchrow_required("SELECT sha256 FROM attachments"))[
                "sha256"
            ] == ("abc123")
            assert (await restored.fetchrow_required("SELECT count(*) AS n FROM audit_log"))[
                "n"
            ] >= 1
            assert (await restored.fetchrow_required("SELECT count(*) AS n FROM schedules"))[
                "n"
            ] > 0
            assert (await restored.fetchrow_required("SELECT count(*) AS n FROM goals"))["n"] > 0
            # The dump carries the migrations' table grants, so a restored instance can answer and
            # back up again. `agent_ro` still cannot read the auth tables.
            assert await restored.fetchval(
                "SELECT has_table_privilege('agent_ro', 'v_accounts', 'SELECT')"
            )
            assert not await restored.fetchval(
                "SELECT has_table_privilege('agent_ro', 'accounts', 'SELECT')"
            )
            assert not await restored.fetchval(
                "SELECT has_table_privilege('agent_ro', 'users', 'SELECT')"
            )
            assert await restored.fetchval(
                "SELECT has_table_privilege('backup', 'accounts', 'SELECT')"
            )
    finally:
        await drop_database(os.environ["DATABASE_URL"], scratch_name("arno_l8_restore"))
