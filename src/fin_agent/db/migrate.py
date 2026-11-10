"""Numbered SQL migrations with a `schema_version` table.

Each file runs in its own transaction. A file may pull in a vendored SQL file with a psql-style
include line on its own line:

    \\i ../vendor/pgledger/pgledger.sql

The include is expanded before checksumming, so the recorded checksum covers exactly what ran.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fin_agent.db.conn import Connection, connect

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
INCLUDE_RE = re.compile(r"^\s*\\i\s+(\S+)\s*$")
NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
ALLOWED_INCLUDE_ROOT = Path(__file__).resolve().parent

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """A migration is malformed, out of order, or changed after it ran."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def _expand(path: Path, seen: tuple[Path, ...] = ()) -> str:
    resolved = path.resolve()
    if resolved in seen:
        raise MigrationError(f"Include cycle at {resolved}")
    lines: list[str] = []
    for line in resolved.read_text(encoding="utf-8").splitlines():
        match = INCLUDE_RE.match(line)
        if match:
            included = (resolved.parent / match.group(1)).resolve()
            if ALLOWED_INCLUDE_ROOT not in included.parents:
                raise MigrationError(f"Include escapes the db directory: {included}")
            lines.append(_expand(included, (*seen, resolved)))
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = NAME_RE.match(path.name)
        if not match:
            raise MigrationError(f"Migration file name must be NNNN_name.sql: {path.name}")
        migrations.append(
            Migration(
                version=int(match.group(1)),
                name=match.group(2),
                path=path,
                sql=_expand(path),
            )
        )
    versions = [m.version for m in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"Migrations must be numbered 1..n with no gaps: {versions}")
    return migrations


async def current_version(conn: Connection) -> int:
    await conn.execute(SCHEMA_VERSION_DDL)
    # An aggregate with no GROUP BY always yields its one row.
    row = await conn.fetchrow_required(
        "SELECT coalesce(max(version), 0) AS version FROM schema_version"
    )
    return int(row["version"])


async def applied(conn: Connection) -> list[dict[str, Any]]:
    await conn.execute(SCHEMA_VERSION_DDL)
    return await conn.fetch(
        "SELECT version, name, checksum, applied_at FROM schema_version ORDER BY version"
    )


async def run_migrations(
    conn: Connection | None = None, directory: Path = MIGRATIONS_DIR
) -> list[int]:
    """Apply pending migrations. Returns the versions applied in this call."""

    async def _run(conn: Connection) -> list[int]:
        await conn.execute(SCHEMA_VERSION_DDL)

        migrations = discover(directory)
        by_version = {m.version: m for m in migrations}
        recorded = {int(row["version"]): row["checksum"] for row in await applied(conn)}

        for version, checksum in recorded.items():
            if version not in by_version:
                raise MigrationError(
                    f"Database has migration {version}, which is missing from disk"
                )
            if by_version[version].checksum != checksum:
                raise MigrationError(
                    f"Migration {version} ({by_version[version].name}) changed after it was applied"
                )

        newly_applied: list[int] = []
        for migration in migrations:
            if migration.version in recorded:
                continue
            async with conn.transaction():
                await conn.execute(migration.sql)
                await conn.execute(
                    "INSERT INTO schema_version (version, name, checksum) VALUES ($1, $2, $3)",
                    migration.version,
                    migration.name,
                    migration.checksum,
                )
            newly_applied.append(migration.version)
        return newly_applied

    if conn is None:
        async with connect() as owned:
            return await _run(owned)
    return await _run(conn)


async def pending(conn: Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Return the migrations this database has not applied yet, without applying anything."""
    await conn.execute(SCHEMA_VERSION_DDL)
    recorded = {int(row["version"]) for row in await applied(conn)}
    return [migration for migration in discover(directory) if migration.version not in recorded]
