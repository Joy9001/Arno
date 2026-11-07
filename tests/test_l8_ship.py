from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from fin_agent.api.capture import MAX_CAPTURE_BYTES
from fin_agent.api.imports import MAX_UPLOAD_BYTES
from fin_agent.api.middleware import CONTENT_SECURITY_POLICY
from fin_agent.backup_restore import build_manifest, restore_from_restic
from fin_agent.db.conn import Connection, connect, conninfo_to_dict, make_conninfo
from fin_agent.db.pre_migration import dump_before_migrating
from tests.software_authenticator import SoftwareAuthenticator
from tests.test_auth import _complete_setup

REQUIRED_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)


@asynccontextmanager
async def _admin_conn() -> AsyncIterator[Connection]:
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["dbname"] = "postgres"
    async with connect(make_conninfo(**info), autocommit=True) as conn:
        yield conn


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
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    blocked = client.get("/api/export/json")
    assert blocked.status_code == 403
    assert blocked.headers["x-fin-agent-step-up"] == "passkey"

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


async def test_backup_manifest_changes_when_a_table_changes(demo_ledger):
    await demo_ledger.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('Loves UPI', 'user')"
    )
    before = await build_manifest(demo_ledger)
    assert before["format"] == "fin-agent-backup-manifest"
    assert before["tables"]["preferences"]["rows"] >= 1
    assert before["tables"]["accounts"]["rows"] > 0

    await demo_ledger.execute("UPDATE preferences SET body = 'Loves cards'")
    after = await build_manifest(demo_ledger)
    assert after["tables"]["preferences"]["md5"] != before["tables"]["preferences"]["md5"]


async def test_pre_migration_dump_runs_only_when_a_migration_is_pending(db, settings, tmp_path):
    from dataclasses import replace

    assert await dump_before_migrating(db, replace(settings, pre_migration_dir=tmp_path)) is None

    import fin_agent.db.pre_migration as pre_migration

    original_pending = pre_migration.pending
    original_dump = pre_migration.export_pg_dump

    async def fake_pending(conn):
        return [object()]

    pre_migration.pending = fake_pending
    pre_migration.export_pg_dump = lambda url: b"-- pre-migration dump\n"
    try:
        path = await dump_before_migrating(db, replace(settings, pre_migration_dir=tmp_path))
    finally:
        pre_migration.pending = original_pending
        pre_migration.export_pg_dump = original_dump

    assert path is not None
    assert path.read_bytes() == b"-- pre-migration dump\n"


def _binary(env_name: str, name: str) -> str | None:
    # Same lookup as fin_agent.backup_restore: CI pins the PostgreSQL 18 client by env var.
    return os.environ.get(env_name) or shutil.which(name)


@pytest.mark.skipif(
    not (
        _binary("RESTIC", "restic")
        and _binary("PG_DUMP", "pg_dump")
        and _binary("PG_RESTORE", "pg_restore")
    ),
    reason="restic or the PostgreSQL 18 client is missing",
)
async def test_restore_drill_brings_back_every_area(demo_ledger, tmp_path: Path):
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

    manifest = await build_manifest(db)
    source = os.environ["DATABASE_URL"]
    dump = tmp_path / "fin_agent.dump"
    env = os.environ.copy()
    info = conninfo_to_dict(source)
    if "password" in info:
        env["PGPASSWORD"] = str(info["password"])
    dumped = subprocess.run(
        [
            _binary("PG_DUMP", "pg_dump"),
            "--no-owner",
            "--no-privileges",
            "--format=custom",
            "-f",
            str(dump),
            source,
        ],
        capture_output=True,
        env=env,
        check=False,
    )
    assert dumped.returncode == 0, dumped.stderr.decode("utf-8", "replace")

    import hashlib

    manifest["dump_sha256"] = hashlib.sha256(dump.read_bytes()).hexdigest()
    manifest_path = tmp_path / "backup_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    repo = tmp_path / "restic-repo"
    restic_env = os.environ | {
        "RESTIC_REPOSITORY": str(repo),
        "RESTIC_PASSWORD": "local-drill-password",
    }
    assert subprocess.run(["restic", "init"], env=restic_env, check=False).returncode == 0
    backup = subprocess.run(
        ["restic", "backup", "--tag", "fin-agent", str(dump), str(manifest_path)],
        env=restic_env,
        capture_output=True,
        check=False,
    )
    assert backup.returncode == 0, backup.stderr.decode("utf-8", "replace")

    target_info = dict(info)
    target_info["dbname"] = "fin_agent_l8_restore_test"
    target = make_conninfo(**target_info)
    try:
        report = await restore_from_restic(
            repository=str(repo),
            password="local-drill-password",
            target_dsn=target,
            force=True,
        )
        assert report.ok, report.as_dict()
        async with connect(target) as restored:
            assert (await restored.fetchrow("SELECT body FROM preferences"))["body"] == (
                "Prefers UPI"
            )
            assert (await restored.fetchrow("SELECT body_text FROM messages"))["body_text"] == (
                "Dinner at Swiggy"
            )
            assert (await restored.fetchrow("SELECT sha256 FROM attachments"))["sha256"] == (
                "abc123"
            )
            assert (await restored.fetchrow("SELECT count(*) AS n FROM audit_log"))["n"] >= 1
            assert (await restored.fetchrow("SELECT count(*) AS n FROM schedules"))["n"] > 0
            assert (await restored.fetchrow("SELECT count(*) AS n FROM goals"))["n"] > 0
    finally:
        async with _admin_conn() as admin:
            await admin.execute('DROP DATABASE IF EXISTS "fin_agent_l8_restore_test" WITH (FORCE)')


BACKUP_SCRIPT = Path(__file__).resolve().parents[1] / "backup" / "backup.sh"


def _backup_script_skip_reason() -> str | None:
    missing = [name for name in ("sh", "jq", "restic") if shutil.which(name) is None]
    for env_name, name in (("PSQL", "psql"), ("PG_DUMP", "pg_dump")):
        if _binary(env_name, name) is None:
            missing.append(name)
    if missing:
        return f"backup script needs {', '.join(missing)}"
    dump = _binary("PG_DUMP", "pg_dump")
    assert dump is not None
    version = subprocess.run([dump, "--version"], capture_output=True, text=True, check=False)
    if " (PostgreSQL) 18." not in version.stdout:
        return f"backup script needs pg_dump 18: {version.stdout.strip() or version.stderr.strip()}"
    return None


def _client_path() -> str:
    """Prepend the client directories the env vars name, so the script finds v18 first."""
    dirs = [
        str(Path(executable).parent)
        for env_name, name in (
            ("PSQL", "psql"),
            ("PG_DUMP", "pg_dump"),
            ("PG_RESTORE", "pg_restore"),
            ("RESTIC", "restic"),
        )
        if (executable := _binary(env_name, name))
    ]
    return os.pathsep.join([*dict.fromkeys(dirs), os.environ.get("PATH", "")])


BACKUP_SCRIPT_SKIP = _backup_script_skip_reason()


@pytest.mark.skipif(BACKUP_SCRIPT_SKIP is not None, reason=BACKUP_SCRIPT_SKIP or "")
async def test_backup_script_takes_the_dump_and_manifest_from_one_snapshot(
    demo_ledger, tmp_path: Path
):
    """A write that commits mid-backup must not land in the manifest but miss the dump."""
    source = os.environ["DATABASE_URL"]
    info = conninfo_to_dict(source)
    dump = tmp_path / "fin_agent.dump"
    manifest = tmp_path / "backup_manifest.json"
    repo = tmp_path / "restic-repo"

    real_dump = _binary("PG_DUMP", "pg_dump")
    assert real_dump is not None
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "pg_dump"
    shim.write_text(
        "#!/bin/sh\n"
        '"$REAL_PG_DUMP" "$@"\n'
        "status=$?\n"
        'psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -c '
        "\"INSERT INTO preferences (body, created_by) VALUES ('raced write', 'user')\"\n"
        'exit "$status"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)

    env = os.environ | {
        "BACKUP_ONCE": "1",
        "BACKUP_DUMP_PATH": str(dump),
        "BACKUP_MANIFEST_PATH": str(manifest),
        "RESTIC_REPOSITORY": str(repo),
        "RESTIC_PASSWORD": "race-drill-password",
        "REAL_PG_DUMP": real_dump,
        "PATH": os.pathsep.join([str(shim_dir), _client_path()]),
    }
    if "password" in info:
        env["PGPASSWORD"] = str(info["password"])
    sh = shutil.which("sh")
    assert sh is not None
    ran = subprocess.run(
        [sh, str(BACKUP_SCRIPT)], capture_output=True, env=env, timeout=300, check=False
    )
    assert ran.returncode == 0, ran.stderr.decode("utf-8", "replace")[-3000:]

    target_info = dict(info)
    target_info["dbname"] = "fin_agent_l8_snapshot_test"
    target = make_conninfo(**target_info)
    try:
        report = await restore_from_restic(
            repository=str(repo),
            password="race-drill-password",
            target_dsn=target,
            force=True,
        )
        assert report.checks.get("manifest_matches") is True, report.as_dict()
        assert report.ok, report.as_dict()
        async with connect(target) as restored:
            raced = (
                await restored.fetchrow(
                    "SELECT count(*) AS n FROM preferences WHERE body = 'raced write'"
                )
            )["n"]
        assert raced == 0
    finally:
        async with _admin_conn() as admin:
            await admin.execute('DROP DATABASE IF EXISTS "fin_agent_l8_snapshot_test" WITH (FORCE)')


@pytest.mark.skipif(
    BACKUP_SCRIPT_SKIP is not None or sys.platform == "win32",
    reason=BACKUP_SCRIPT_SKIP or "POSIX signals are needed to stop the idle sleep",
)
def test_backup_script_stops_promptly_while_idle(tmp_path: Path):
    """`docker compose stop` must not wait out the interval: TERM interrupts the idle sleep."""
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    env = os.environ | {
        "BACKUP_INTERVAL_SECONDS": "3600",
        "BACKUP_MAX_AGE_SECONDS": "99999999999",
        "BACKUP_DUMP_PATH": str(tmp_path / "fin_agent.dump"),
        "BACKUP_MANIFEST_PATH": str(tmp_path / "backup_manifest.json"),
        "RESTIC_REPOSITORY": str(tmp_path / "restic-repo"),
        "RESTIC_PASSWORD": "idle-drill-password",
        "PATH": _client_path(),
    }
    if "password" in info:
        env["PGPASSWORD"] = str(info["password"])
    sh = shutil.which("sh")
    assert sh is not None
    proc = subprocess.Popen(
        [sh, str(BACKUP_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
    )
    output: list[str] = []
    try:
        assert proc.stdout is not None
        deadline = time.monotonic() + 60
        while not any("next check in" in line for line in output):
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"script never went idle: {output}"
            ready, _, _ = select.select([proc.stdout], [], [], remaining)
            assert ready, f"script never went idle: {output}"
            line = proc.stdout.readline()
            assert line, f"script exited before going idle: {output}"
            output.append(line)

        started = time.monotonic()
        proc.terminate()
        proc.wait(timeout=5)
        elapsed = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()

    assert elapsed < 5, f"stop took {elapsed:.1f}s; output: {''.join(output)}"
    assert proc.returncode == 0, f"output: {''.join(output)}"
