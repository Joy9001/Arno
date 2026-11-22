"""PostgreSQL client helpers shared by the restore drills.

The drills need the real `pg_dump`/`pg_restore`/`psql` binaries, which CI pins by environment
variable (a version other than 18 would restore a dump the app cannot use).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from arno.db.conn import conninfo_to_dict


def binary(env_name: str, name: str) -> str | None:
    return os.environ.get(env_name) or shutil.which(name)


def missing() -> str | None:
    """The clients this machine does not have, or None when all three are present."""
    absent = [
        name
        for env_name, name in (
            ("PG_DUMP", "pg_dump"),
            ("PG_RESTORE", "pg_restore"),
            ("PSQL", "psql"),
        )
        if binary(env_name, name) is None
    ]
    return f"missing {', '.join(absent)}" if absent else None


def client_env(dsn: str) -> dict[str, str]:
    """The client environment for one DSN, including its sslmode."""
    env = os.environ.copy()
    info = conninfo_to_dict(dsn)
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
    return env


def dump_database(path: Path, dsn: str, *, privileges: bool) -> None:
    """Dump the database the way the sidecar does, or the way older images did."""
    pg_dump = binary("PG_DUMP", "pg_dump")
    assert pg_dump is not None  # every caller skips without it
    command = [pg_dump, "--no-owner"]
    if not privileges:
        command.append("--no-privileges")
    command += ["--format=custom", "-f", str(path), dsn]
    dumped = subprocess.run(command, capture_output=True, env=client_env(dsn), check=False)
    assert dumped.returncode == 0, dumped.stderr.decode("utf-8", "replace")
