from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from arno.ledger import export
from arno.ledger.errors import UnavailableLedgerError

# The public contract: these tables are credential or key material and never appear
# in a data export. Kept literal so dropping one from the source makes this test fail.
EXCLUDED_TABLES = [
    "model_credentials",
]

DATABASE_URL = "postgresql://app:secret@db.example:5433/arno"


def test_export_scope_reports_the_public_exclusion_list(session_client):
    response = session_client.get("/api/export/scope")

    assert response.status_code == 200
    assert response.json() == {"excluded_tables": EXCLUDED_TABLES}


def test_export_scope_requires_a_session(client):
    assert client.get("/api/export/scope").status_code == 401


def test_pg_dump_is_unavailable_without_the_binary(monkeypatch):
    monkeypatch.delenv("PG_DUMP", raising=False)
    monkeypatch.setattr(export.shutil, "which", lambda _name: None)

    with pytest.raises(UnavailableLedgerError, match="not installed"):
        export.export_pg_dump(DATABASE_URL)


def test_pg_dump_receives_the_database_url_as_environment(monkeypatch):
    monkeypatch.setenv("PG_DUMP", "/opt/postgres/bin/pg_dump")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv, *, env, **kwargs):
        calls.append((argv, env))
        return SimpleNamespace(returncode=0, stdout=b"-- dump", stderr=b"")

    monkeypatch.setattr(export.subprocess, "run", fake_run)

    assert export.export_pg_dump(DATABASE_URL) == b"-- dump"

    argv, env = calls[0]
    assert argv == ["/opt/postgres/bin/pg_dump", "--no-owner", "--format=plain"]
    assert env["PGHOST"] == "db.example"
    assert env["PGPORT"] == "5433"
    assert env["PGUSER"] == "app"
    assert env["PGPASSWORD"] == "secret"
    assert env["PGDATABASE"] == "arno"


def test_pg_dump_failure_keeps_the_server_message(monkeypatch):
    monkeypatch.setenv("PG_DUMP", "pg_dump")
    monkeypatch.setattr(
        export.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout=b"", stderr=b"connection refused\n"
        ),
    )

    with pytest.raises(UnavailableLedgerError, match="pg_dump failed: connection refused"):
        export.export_pg_dump(DATABASE_URL)


def test_pg_dump_timeout_is_a_ledger_error(monkeypatch):
    monkeypatch.setenv("PG_DUMP", "pg_dump")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("pg_dump", 120)

    monkeypatch.setattr(export.subprocess, "run", timeout)

    with pytest.raises(UnavailableLedgerError, match="pg_dump failed"):
        export.export_pg_dump(DATABASE_URL)


def test_the_dump_route_reports_an_unavailable_pg_dump(session_client, monkeypatch):
    def unavailable(_url: str) -> bytes:
        raise UnavailableLedgerError("pg_dump is not installed in this environment.")

    monkeypatch.setattr(export, "export_pg_dump", unavailable)

    response = session_client.get("/api/export/pg_dump")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["detail"] == "pg_dump is not installed in this environment."
