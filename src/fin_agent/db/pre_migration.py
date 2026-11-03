"""A dump before a migration, so an upgrade can always be rolled back.

The API and `fin-agent migrate` both call this. A fresh database has nothing to dump and skips it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from fin_agent.config import Settings
from fin_agent.db.migrate import pending
from fin_agent.ledger.export import export_pg_dump

logger = logging.getLogger("fin_agent")


def dump_before_migrating(conn: psycopg.Connection, settings: Settings) -> Path | None:
    """Write a plain `pg_dump` when migrations are pending on a non-empty database."""
    if not pending(conn):
        return None
    applied = conn.execute("SELECT count(*) AS n FROM schema_version").fetchone()["n"]
    if not applied:
        return None
    settings.pre_migration_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = settings.pre_migration_dir / f"pre-migration-{stamp}.sql"
    path.write_bytes(export_pg_dump(settings.database_url))
    logger.info("pre-migration dump written to %s", path)
    return path
