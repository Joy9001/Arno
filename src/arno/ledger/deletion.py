"""What a deletion request removes, what it keeps, and where the data still lives.

`preview` is the read-only half every delete button shows before its confirm step: the rows a
list action touches, the rows that survive (audit, undo, provenance), the backup window, and the
retention terms of each configured model provider. `erase_everything` is the Settings-only bulk
delete. Both return plain dicts; the API and the web app render them as they are.

The erase is one transaction: every table that holds user or financial rows is truncated, then
the rows a fresh install has are rebuilt (system accounts, default categories, the
weekly briefing question) so the instance keeps working without a restart. Nothing here imports
PydanticAI or FastAPI; the router in `api/deletion.py` wraps it.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any
from urllib.parse import urlsplit

from arno.db.conn import Connection, quote_ident
from arno.india.categories import DEFAULT_CATEGORIES
from arno.ledger.accounts import ensure_system_accounts
from arno.ledger.categories import ensure_default_categories
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError, NotFoundLedgerError

SCOPES = ("memory", "statement", "everything")

# The erase tries this lock exclusively and every background task holds it shared, so no task
# can run between the busy check and the truncate.
ERASE_LOCK_KEY = 0x41524E4F  # "ARNO"

# The phrase the Settings panel asks for before the bulk delete.
CONFIRM_PHRASE = "delete everything"

# Bootstrap settings and the migration ledger. An erase that also removed these would break the
# instance, so they are never touched.
RETAINED = frozenset(
    {
        "schema_version",
        "settings",
    }
)


def _rebuilt_counts() -> dict[str, int]:
    """Rows the erase rebuilds exactly as a fresh install has them.

    They are subtracted from every count: after an erase each of these reads zero, so the
    erase's own seed rows never look like user data the preview promised to remove.
    """
    return {
        "accounts": 3,
        "categories": len(DEFAULT_CATEGORIES),
        "scheduled_questions": 1,
    }


BACKUP_POLICY = (
    "restic keeps 7 daily and 4 weekly snapshots: "
    "`restic forget --keep-daily 7 --keep-weekly 4 --prune` (jobs/backup.py)."
)
BACKUP_NOTE = (
    "A failed or delayed backup or prune means there is no guaranteed purge date. Deleted data "
    "typically ages out after about 4 weeks once backups and pruning run; until then it remains "
    "in off-site snapshots."
)

# (env prefix, role the UI shows, what that role was sent)
_PROVIDER_ROLES = (
    ("CHAT", "chat", "text"),
    ("VISION", "vision", "images and statement pages"),
    ("STT", "stt", "voice audio"),
)

_PROVIDER_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")

_EVERYTHING_RETAINS: tuple[dict[str, str], ...] = (
    {
        "what": "Your sign-in: it comes from Tailscale and the environment, not the database",
        "why": "so you can still open the instance afterwards",
    },
    {
        "what": "The instance settings row",
        "why": "the ledger needs a currency, time zone, and notification defaults; the personal "
        "spending floor and last model error are cleared",
    },
    {
        "what": "The schema version",
        "why": "needed to run and upgrade the instance",
    },
    {
        "what": "Default categories, merchant rules, system accounts, and the weekly briefing",
        "why": "rebuilt as on a fresh install; they name no person and hold no money",
    },
    {
        "what": "Database dumps taken before a schema upgrade",
        "why": "plaintext SQL kept for rolling the upgrade back, like a backup; delete the files "
        "under ARNO_PRE_MIGRATION_DIR to remove them",
    },
    {
        "what": "Model traces in the observability store, when one is configured",
        "why": "Phoenix keeps every run for debugging in its own `phoenix-data` volume; erase "
        "everything does not reach it, so stop it and run `docker volume rm arno_phoenix-data`",
    },
)


def _hostname(base_url: str) -> str | None:
    try:
        parts = urlsplit(base_url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    return parts.hostname


def _is_local(host: str) -> bool:
    if host in {"", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _providers() -> list[dict[str, str]]:
    """Each configured model role by host only: a base URL can carry a key in its userinfo."""
    found: list[dict[str, str]] = []
    for prefix, role, sent in _PROVIDER_ROLES:
        base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip()
        model = os.environ.get(f"{prefix}_MODEL", "").strip()
        # Both are needed for a usable role, the same test the settings screen uses.
        host = _hostname(base_url) if base_url and model else None
        if host is None:
            continue
        declared = os.environ.get(f"{prefix}_PROVIDER", "").strip().lower()
        provider = declared if _PROVIDER_NAME.fullmatch(declared) else "openai-compatible"
        if _is_local(host):
            note = "stays on this machine; nothing this role saw leaves the host"
        else:
            note = (
                f"{sent} already sent cannot be recalled by Arno; the provider keeps it under "
                "its own retention terms"
            )
        found.append({"role": role, "provider": provider, "host": host, "note": note})
    return found


def retention_facts() -> dict[str, Any]:
    """The three answers that need no database: what an erase keeps, backups, and providers."""
    return {
        "retains": [dict(item) for item in _EVERYTHING_RETAINS],
        "backups": {"policy": BACKUP_POLICY, "note": BACKUP_NOTE},
        "providers": _providers(),
    }


async def _data_tables(conn: Connection) -> list[str]:
    """Every public base table that is not auth or bootstrap state, in a stable order."""
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    return [str(row["table_name"]) for row in rows if str(row["table_name"]) not in RETAINED]


async def _removable_counts(conn: Connection) -> list[dict[str, Any]]:
    """Rows the erase deletes and does not rebuild, one entry per table with a non-zero count."""
    rebuilt = _rebuilt_counts()
    removes: list[dict[str, Any]] = []
    for table in await _data_tables(conn):
        total = await conn.fetchval(f"SELECT count(*) FROM {quote_ident(table)}")
        remaining = int(total or 0) - rebuilt.get(table, 0)
        if remaining > 0:
            removes.append({"table": table, "count": remaining})
    return removes


async def _memory_scope(conn: Connection, target_id: str | None) -> tuple[list[Any], list[Any]]:
    if not target_id:
        raise InvalidLedgerError("A memory preview needs the memory's id")
    row = await conn.fetchrow("SELECT id FROM preferences WHERE id = $1", target_id)
    if row is None:
        raise NotFoundLedgerError("No such memory")
    audits = await conn.fetchval(
        """
        SELECT count(*) FROM undo_log
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements(snapshot) e
            WHERE e ->> 'table' = 'preferences' AND e ->> 'key' = $1
        )
        """,
        target_id,
    )
    removes: list[dict[str, Any]] = [{"table": "preferences", "count": 1}]
    retains = [
        {
            "what": f"{int(audits or 0)} undo-history entries naming this memory",
            "why": "they stay as the record of the delete, with the body blanked in every copy",
        },
        {
            "what": "The conversations that mentioned it",
            "why": "one memory delete leaves chat history alone; erase everything to remove it",
        },
    ]
    return removes, retains


async def _statement_scope(conn: Connection, target_id: str | None) -> tuple[list[Any], list[Any]]:
    if not target_id:
        raise InvalidLedgerError("A statement preview needs the import's id")
    row = await conn.fetchrow("SELECT id, attachment_id FROM imports WHERE id = $1", target_id)
    if row is None:
        raise NotFoundLedgerError("No such import")
    stored = 0
    if row["attachment_id"]:
        stored = int(
            await conn.fetchval(
                "SELECT count(*) FROM attachments WHERE id = $1 AND data IS NOT NULL",
                row["attachment_id"],
            )
            or 0
        )
    removes = [{"table": "attachments", "count": stored}]
    retains = [
        {
            "what": "The attachment row, its SHA-256, and the statement lines",
            "why": "only the stored file bytes are cleared; the row and hash stay so the import "
            "remains auditable and a re-upload is recognized",
        },
        {
            "what": "Undo history for this import",
            "why": "the ledger writes it made keep their history; purging the file moves no money",
        },
        {
            "what": "The movements booked from this statement",
            "why": "use Undo import to reverse them; deleting the file does not",
        },
    ]
    return removes, retains


async def preview(conn: Connection, scope: str, target_id: str | None = None) -> dict[str, Any]:
    """Rows a deletion touches, rows it keeps, the backup window, and provider retention."""
    if scope not in SCOPES:
        raise InvalidLedgerError("A deletion scope is memory, statement, or everything")
    if scope == "everything":
        removes, retains = (
            await _removable_counts(conn),
            [dict(item) for item in _EVERYTHING_RETAINS],
        )
    elif scope == "memory":
        removes, retains = await _memory_scope(conn, target_id)
    else:
        removes, retains = await _statement_scope(conn, target_id)
    return {
        "scope": scope,
        "removes": removes,
        "retains": retains,
        "backups": {"policy": BACKUP_POLICY, "note": BACKUP_NOTE},
        "providers": _providers(),
    }


async def erase_everything(conn: Connection) -> dict[str, Any]:
    """Delete every user and financial row in one transaction, then rebuild the seed rows."""
    from arno.scheduled_questions import ensure_default_question

    async with conn.transaction():
        # A background task holds this lock shared while it runs, so getting it exclusively
        # means none is running, and none can start until this transaction ends.
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", ERASE_LOCK_KEY):
            raise ConflictLedgerError(
                "A background task is running. Wait for it to finish, then delete everything."
            )
        removes = await _removable_counts(conn)
        tables = ", ".join(quote_ident(table) for table in await _data_tables(conn))
        # No CASCADE: a future foreign key from a retained table makes this fail instead of
        # silently truncating the row someone needs to sign in with.
        await conn.execute(f"TRUNCATE {tables}")
        await ensure_system_accounts(conn)
        await ensure_default_categories(conn)
        await ensure_default_question(conn)
        # Rebuilding the seed rows writes undo entries; a fresh start keeps no history.
        await conn.execute("TRUNCATE undo_log")
        await conn.execute(
            """
            UPDATE settings
            SET floor_paise = NULL,
                last_model_error_type = NULL,
                last_model_error_at = NULL,
                updated_at = now()
            WHERE id = 1
            """
        )
    return {
        "scope": "everything",
        "removes": removes,
        "total": sum(int(item["count"]) for item in removes),
    }


__all__ = [
    "CONFIRM_PHRASE",
    "ERASE_LOCK_KEY",
    "RETAINED",
    "SCOPES",
    "erase_everything",
    "preview",
    "retention_facts",
]
