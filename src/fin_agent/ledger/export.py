"""Full export: JSON, one CSV per table in a ZIP, and `pg_dump`."""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from fin_agent.ledger.audit import jsonable
from fin_agent.ledger.errors import UnavailableLedgerError


def table_names(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    ).fetchall()
    return [str(row["table_name"]) for row in rows]


def _rows(conn: psycopg.Connection, table: str) -> tuple[list[str], list[dict[str, Any]]]:
    cursor = conn.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
    columns = [column.name for column in (cursor.description or ())]
    rows = [dict(row) for row in cursor.fetchall()]
    return columns, rows


def export_json(conn: psycopg.Connection) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in table_names(conn):
        _, rows = _rows(conn, table)
        tables[table] = [jsonable(row) for row in rows]
    return {
        "format": "fin-agent-export",
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "tables": tables,
    }


def export_csv_zip(conn: psycopg.Connection) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table in table_names(conn):
            columns, rows = _rows(conn, table)
            text = io.StringIO()
            writer = csv.DictWriter(
                text, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({column: _csv_value(jsonable(row[column])) for column in columns})
            archive.writestr(f"{table}.csv", text.getvalue())
        archive.writestr(
            "README.txt",
            "Fin-Agent full export. One CSV per table. Money is integer paise.\n",
        )
    return buffer.getvalue()


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def export_pg_dump(database_url: str, *, timeout: int = 120) -> bytes:
    executable = os.environ.get("PG_DUMP") or shutil.which("pg_dump")
    if not executable:
        raise UnavailableLedgerError(
            "pg_dump is not installed in this environment. Use the Docker image or install "
            "the PostgreSQL 18 client."
        )
    try:
        info = conninfo_to_dict(database_url)
    except Exception as exc:  # noqa: BLE001 - normalize driver errors
        raise UnavailableLedgerError(f"Could not read the database URL: {exc}") from exc

    env = os.environ.copy()
    for source, target in (
        ("host", "PGHOST"),
        ("port", "PGPORT"),
        ("user", "PGUSER"),
        ("password", "PGPASSWORD"),
        ("dbname", "PGDATABASE"),
        ("sslmode", "PGSSLMODE"),
    ):
        if source in info:
            env[target] = str(info[source])

    try:
        result = subprocess.run(
            [executable, "--no-owner", "--no-privileges", "--format=plain"],
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UnavailableLedgerError(f"pg_dump failed: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()[:500]
        raise UnavailableLedgerError(f"pg_dump failed: {message}")
    return result.stdout
