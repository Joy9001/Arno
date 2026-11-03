from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from fin_agent.api.capture import MAX_CAPTURE_BYTES
from fin_agent.api.imports import MAX_UPLOAD_BYTES
from fin_agent.api.middleware import CONTENT_SECURITY_POLICY
from fin_agent.backup_restore import build_manifest, restore_from_restic
from fin_agent.db.pre_migration import dump_before_migrating
from fin_agent.logging_utils import ContentlessLogFilter, sanitize
from tests.software_authenticator import SoftwareAuthenticator
from tests.test_auth import _complete_setup

REQUIRED_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)


def _admin_conn():
    info = conninfo_to_dict(os.environ["DATABASE_URL"])
    info["dbname"] = "postgres"
    return psycopg.connect(make_conninfo(**info), autocommit=True, row_factory=dict_row)


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


def test_export_and_passkey_changes_need_a_fresh_passkey(client, setup_token, settings, db):
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

    passkey_id = db.execute("SELECT id FROM webauthn_credentials").fetchone()["id"]
    renamed = client.patch(f"/api/auth/passkeys/{passkey_id}", json={"name": "Laptop"})
    assert renamed.status_code == 200, renamed.text

    db.execute("UPDATE sessions SET reverified_at = now() - interval '6 minutes'")
    db.commit()
    assert client.get("/api/export/json").status_code == 403


def test_log_sanitizer_removes_amounts_and_database_detail():
    record = logging.LogRecord(
        "fin_agent",
        logging.ERROR,
        __file__,
        1,
        "paid ₹1,234.50 to Swiggy",
        (),
        None,
    )
    ContentlessLogFilter().filter(record)
    assert "1,234" not in record.getMessage()
    assert "[amount]" in record.getMessage()

    detail = (
        'duplicate key value violates unique constraint "payees_name_key" '
        "DETAIL: Key (name)=(Swiggy) already exists."
    )
    cleaned = sanitize(detail)
    assert "Swiggy" not in cleaned
    assert "DETAIL:" in cleaned

    try:
        raise RuntimeError(detail)
    except RuntimeError:
        exception_record = logging.LogRecord(
            "fin_agent",
            logging.ERROR,
            __file__,
            1,
            "job failed",
            (),
            sys.exc_info(),
        )
    ContentlessLogFilter().filter(exception_record)
    assert exception_record.exc_info is None
    assert "Swiggy" not in (exception_record.exc_text or "")


def test_backup_manifest_changes_when_a_table_changes(demo_ledger):
    demo_ledger.execute("INSERT INTO preferences (body, created_by) VALUES ('Loves UPI', 'user')")
    demo_ledger.commit()
    before = build_manifest(demo_ledger)
    assert before["format"] == "fin-agent-backup-manifest"
    assert before["tables"]["preferences"]["rows"] >= 1
    assert before["tables"]["accounts"]["rows"] > 0

    demo_ledger.execute("UPDATE preferences SET body = 'Loves cards'")
    demo_ledger.commit()
    after = build_manifest(demo_ledger)
    assert after["tables"]["preferences"]["md5"] != before["tables"]["preferences"]["md5"]


def test_pre_migration_dump_runs_only_when_a_migration_is_pending(db, settings, tmp_path):
    from dataclasses import replace

    assert dump_before_migrating(db, replace(settings, pre_migration_dir=tmp_path)) is None

    import fin_agent.db.pre_migration as pre_migration

    original_pending = pre_migration.pending
    original_dump = pre_migration.export_pg_dump
    pre_migration.pending = lambda conn: [object()]
    pre_migration.export_pg_dump = lambda url: b"-- pre-migration dump\n"
    try:
        path = dump_before_migrating(db, replace(settings, pre_migration_dir=tmp_path))
    finally:
        pre_migration.pending = original_pending
        pre_migration.export_pg_dump = original_dump

    assert path is not None
    assert path.read_bytes() == b"-- pre-migration dump\n"


@pytest.mark.skipif(
    not all(shutil.which(name) for name in ("restic", "pg_dump", "pg_restore")),
    reason="restic or the PostgreSQL 18 client is missing",
)
def test_restore_drill_brings_back_every_area(demo_ledger, tmp_path: Path):
    db = demo_ledger
    preference = db.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('Prefers UPI', 'agent') RETURNING id"
    ).fetchone()["id"]
    conversation = db.execute(
        "INSERT INTO conversations (title) VALUES ('L8 drill') RETURNING id"
    ).fetchone()["id"]
    message = db.execute(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES (%s, 'l8-drill-1', 'user', '[]'::jsonb, 'Dinner at Swiggy')
        RETURNING id
        """,
        (conversation,),
    ).fetchone()["id"]
    db.execute(
        """
        INSERT INTO attachments (kind, filename, content_type, size_bytes, sha256, data)
        VALUES ('photo', 'receipt.jpg', 'image/jpeg', 3, 'abc123', %s)
        """,
        (b"\x01\x02\x03",),
    )
    db.execute(
        """
        INSERT INTO audit_log (actor, message_id, group_id, table_name, row_id, after)
        VALUES ('agent', %s, 'l8-group', 'preferences', %s, '{"body":"Prefers UPI"}'::jsonb)
        """,
        (message, preference),
    )
    db.commit()

    manifest = build_manifest(db)
    source = os.environ["DATABASE_URL"]
    dump = tmp_path / "fin_agent.dump"
    env = os.environ.copy()
    info = conninfo_to_dict(source)
    if "password" in info:
        env["PGPASSWORD"] = str(info["password"])
    dumped = subprocess.run(
        ["pg_dump", "--no-owner", "--no-privileges", "--format=custom", "-f", str(dump), source],
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
        report = restore_from_restic(
            repository=str(repo),
            password="local-drill-password",
            target_dsn=target,
            force=True,
        )
        assert report.ok, report.as_dict()
        with psycopg.connect(target, row_factory=dict_row) as restored:
            assert restored.execute("SELECT body FROM preferences").fetchone()["body"] == (
                "Prefers UPI"
            )
            assert (
                restored.execute("SELECT body_text FROM messages").fetchone()["body_text"]
                == "Dinner at Swiggy"
            )
            assert restored.execute("SELECT sha256 FROM attachments").fetchone()["sha256"] == (
                "abc123"
            )
            assert restored.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"] >= 1
            assert restored.execute("SELECT count(*) AS n FROM schedules").fetchone()["n"] > 0
            assert restored.execute("SELECT count(*) AS n FROM goals").fetchone()["n"] > 0
    finally:
        with _admin_conn() as admin:
            admin.execute('DROP DATABASE IF EXISTS "fin_agent_l8_restore_test" WITH (FORCE)')
