"""The host CLI: migrations, the server, and passkey recovery.

Admin commands never go over HTTP. Anyone with a shell on the host already has the data.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import signal
import sys
from datetime import date
from pathlib import Path
from typing import Any

import typer
import uvicorn

from arno.auth.enrollment import enrollment_message
from arno.auth.sessions import revoke_all_sessions
from arno.auth.setup import issue_setup_token, setup_required
from arno.config import ConfigurationError, Settings, get_settings
from arno.db.bootstrap import set_agent_ro_password, set_backup_password
from arno.db.conn import connect
from arno.db.migrate import run_migrations
from arno.db.pre_migration import dump_before_migrating
from arno.demo.persona import seed_demo
from arno.demo.template import clone_database
from arno.ledger import export as ledger_export
from arno.logging_utils import configure_logging

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Arno administration and development.",
)
passkeys_app = typer.Typer(no_args_is_help=True, help="Passkey recovery.")
demo_app = typer.Typer(no_args_is_help=True, help="The synthetic demo persona.")
app.add_typer(passkeys_app, name="passkeys")
app.add_typer(demo_app, name="demo")

logger = logging.getLogger("arno")


def _settings() -> Settings:
    try:
        return get_settings(refresh=True)
    except ConfigurationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _configure_console() -> None:
    """Write UTF-8 with replacement, so `₹` or a QR block can't crash a legacy console.

    A Windows console still reports a code page such as cp1252, where `₹` is unencodable:
    `typer.echo` would raise after the turn was already saved, and the user never saw the answer.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except OSError, ValueError:  # pragma: no cover - a closed or exotic stream
            pass


@app.callback()
def _entry() -> None:
    """Every command runs on a console that can carry the rupee sign and QR blocks."""
    _configure_console()


async def _migrate(settings: Settings) -> list[int]:
    async with connect(settings.database_url) as conn:
        await dump_before_migrating(conn, settings)
        applied = await run_migrations(conn)
        await set_agent_ro_password(conn, settings.agent_ro_password)
        await set_backup_password(conn, settings.backup_password)
    return applied


@app.command()
def migrate() -> None:
    """Apply any pending SQL migrations."""
    settings = _settings()
    applied = asyncio.run(_migrate(settings))
    if applied:
        typer.echo(f"applied migrations {', '.join(str(v) for v in applied)}")
    else:
        typer.echo("already up to date")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address. Compose uses 0.0.0.0."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, help="Reload on changes (development only)."),
) -> None:
    """Run the API and the static web build."""
    _settings()
    configure_logging()
    # A native crash (PDFium aborts with SIGTRAP) otherwise leaves no trace in the log; dump
    # every thread's Python stack first, then die as before.
    faulthandler.enable(all_threads=True)
    if hasattr(signal, "SIGTRAP"):
        faulthandler.register(signal.SIGTRAP, all_threads=True, chain=True)
    uvicorn.run(
        "arno.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@app.command("setup")
def setup() -> None:
    """Issue a first-run setup token (alias of setup-token)."""
    setup_token()


async def _setup_token(settings: Settings) -> Any:
    async with connect(settings.database_url) as conn:
        if not await setup_required(conn):
            return None
        return await issue_setup_token(conn, ttl_hours=settings.setup_token_ttl_hours)


@app.command("setup-token")
def setup_token() -> None:
    """Issue a fresh first-run enrollment link (rotates the previous one)."""
    settings = _settings()
    issued = asyncio.run(_setup_token(settings))
    if issued is None:
        typer.echo("Setup is complete; use Settings → Passkeys to add a device.")
        raise typer.Exit(code=0)
    typer.echo(
        enrollment_message(
            settings.rp_origin,
            issued.token,
            title=f"First-run setup is open (link valid {settings.setup_token_ttl_hours}h).",
        )
    )


async def _passkeys_list(settings: Settings) -> list[dict[str, Any]]:
    async with connect(settings.database_url) as conn:
        return await conn.fetch(
            """
            SELECT c.id, c.name, c.created_at, c.last_used_at, u.display_name
            FROM webauthn_credentials c
            JOIN users u ON u.id = c.user_id
            ORDER BY c.created_at
            """
        )


@passkeys_app.command("list")
def passkeys_list() -> None:
    """List registered passkeys."""
    settings = _settings()
    rows = asyncio.run(_passkeys_list(settings))
    if not rows:
        typer.echo("No passkeys are registered.")
        return
    for row in rows:
        used = row["last_used_at"].isoformat() if row["last_used_at"] else "never"
        typer.echo(f"{row['id']}  {row['name']}  created {row['created_at']}  last used {used}")


async def _passkeys_reset(settings: Settings) -> tuple[int, int, Any]:
    async with connect(settings.database_url) as conn:
        async with conn.transaction():
            status_str = await conn.execute("DELETE FROM webauthn_credentials")
            deleted = int(status_str.split()[-1])
            await conn.execute("DELETE FROM webauthn_challenges")
        revoked = await revoke_all_sessions(conn)
        if await setup_required(conn):
            issued = await issue_setup_token(conn, ttl_hours=settings.setup_token_ttl_hours)
        else:
            issued = None
    return deleted, revoked, issued


@passkeys_app.command("reset")
def passkeys_reset() -> None:
    """Delete every passkey and session, then reopen setup with a fresh enrollment link."""
    settings = _settings()
    deleted, revoked, issued = asyncio.run(_passkeys_reset(settings))

    typer.echo(f"Deleted {deleted} passkey(s) and revoked {revoked} session(s).")
    if issued:
        typer.echo(
            enrollment_message(
                settings.rp_origin,
                issued.token,
                title=(
                    "First-run setup is open. Scan this QR code or open the link "
                    f"(valid {settings.setup_token_ttl_hours}h)."
                ),
            )
        )


async def _export(settings: Settings, *, format: str) -> Any:
    async with connect(settings.database_url) as conn:
        if format == "json":
            payload = await ledger_export.export_json(conn)
            return __import__("json").dumps(payload, indent=2, ensure_ascii=False, default=str)
        if format == "csv":
            return await ledger_export.export_csv_zip(conn)
        if format == "sql":
            return ledger_export.export_pg_dump(settings.database_url)
        raise typer.BadParameter("format is json, csv, or sql")


@app.command("export")
def export_command(
    format: str = typer.Option("json", help="json, csv, or sql."),
    out: Path | None = typer.Option(  # noqa: B008
        None, "--out", help="Write here instead of stdout."
    ),
) -> None:
    """Export the whole ledger as JSON, a CSV zip, or a pg_dump."""
    from arno.ledger.errors import UnavailableLedgerError

    settings = _settings()
    try:
        data = asyncio.run(_export(settings, format=format))
    except UnavailableLedgerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if out is None:
        if isinstance(data, bytes):
            sys.stdout.buffer.write(data)
        else:
            typer.echo(data)
    else:
        out.write_bytes(data) if isinstance(data, bytes) else out.write_text(data, encoding="utf-8")
        typer.echo(f"wrote {out}")


@app.command("restore")
def restore_command(
    target: str | None = typer.Option(
        None,
        "--target",
        envvar="RESTORE_DATABASE_URL",
        help="Fresh database URL to restore into. Defaults to DATABASE_URL.",
    ),
    repository: str | None = typer.Option(
        None, "--repository", envvar="RESTIC_REPOSITORY", help="The restic repository."
    ),
    snapshot: str = typer.Option("latest", help="Snapshot ID or `latest`."),
    force: bool = typer.Option(
        False, "--force", help="Replace the target database if it already has tables."
    ),
) -> None:
    """Restore a restic snapshot with `restic dump | pg_restore`, then verify the ledger.

    The restic password always comes from RESTIC_PASSWORD.
    """
    from arno.backup_restore import RestoreError, restore_from_restic

    if not repository:
        typer.echo("error: pass --repository or set RESTIC_REPOSITORY", err=True)
        raise typer.Exit(code=2)
    try:
        checks = asyncio.run(
            restore_from_restic(
                repository=repository,
                password=os.environ.get("RESTIC_PASSWORD", ""),
                target_dsn=target or _settings().database_url,
                snapshot=snapshot,
                force=force,
            )
        )
    except RestoreError as exc:
        typer.echo(f"restore failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    for name, passed in checks.items():
        typer.echo(f"  {'ok' if passed else 'FAIL'}  {name}")
    typer.echo(f"restored {snapshot}; restore verified")


async def _demo_seed(
    settings: Settings,
    *,
    anchor: date | None,
    reset: bool,
    statement_coverage: bool,
    coverage_gap: bool,
) -> dict[str, Any]:
    async with connect(settings.database_url) as conn:
        await run_migrations(conn)
        await set_agent_ro_password(conn, settings.agent_ro_password)
        await set_backup_password(conn, settings.backup_password)
        return await seed_demo(
            conn,
            today=anchor,
            reset=reset,
            statement_coverage=statement_coverage,
            coverage_gap=coverage_gap,
        )


@demo_app.command("seed")
def demo_seed(
    today: str | None = typer.Option(None, help="Anchor date (YYYY-MM-DD). Defaults to today."),
    reset: bool = typer.Option(
        False, "--reset/--no-reset", help="Clear the L1 tables before seeding."
    ),
    force: bool = typer.Option(False, "--force", help="Skip the reset confirmation."),
    statement_coverage: bool = typer.Option(
        True,
        "--statement-coverage/--no-statement-coverage",
        help="Seed synthetic committed statement imports for the last three complete months.",
    ),
    coverage_gap: bool = typer.Option(
        False,
        "--coverage-gap",
        help="Leave the oldest of those months uncovered for honesty cases.",
    ),
) -> None:
    """Seed the synthetic Indian persona: the demo instance and the test fixture."""
    anchor = date.fromisoformat(today) if today else None
    settings = _settings()
    if (
        reset
        and not force
        and not typer.confirm("This clears the L1 tables in the current database. Continue?")
    ):
        raise typer.Exit(code=1)
    summary = asyncio.run(
        _demo_seed(
            settings,
            anchor=anchor,
            reset=reset,
            statement_coverage=statement_coverage,
            coverage_gap=coverage_gap,
        )
    )
    typer.echo(
        "Seeded demo persona: "
        f"{summary['accounts']} accounts, {summary['categories']} categories, "
        f"{summary['schedules']} schedules, {summary['goals']} goals, "
        f"{summary['movements']} movements, {summary['imports']} imports, "
        f"{summary['people']} people (today={summary['today']})."
    )


async def _demo_template(
    settings: Settings, *, target: str, anchor: date | None, coverage_gap: bool
) -> tuple[str, dict[str, Any]]:
    async with connect(settings.database_url) as conn:
        await run_migrations(conn)
        summary = await seed_demo(conn, today=anchor, reset=True, coverage_gap=coverage_gap)
        row = await conn.fetchrow("SELECT current_database() AS name")
        source = str(row["name"]) if row else ""
    await clone_database(settings.database_url, source=source, target=target)
    return source, summary


@demo_app.command("template")
def demo_template(
    target: str = typer.Option("arno_demo_template", help="Template database name to create."),
    today: str | None = typer.Option(None, help="Anchor date (YYYY-MM-DD). Defaults to today."),
    force: bool = typer.Option(False, "--force", help="Skip the reset confirmation."),
    coverage_gap: bool = typer.Option(
        False,
        "--coverage-gap",
        help="Leave the oldest covered month as a gap for honesty cases.",
    ),
) -> None:
    """Seed the current database, then clone it as a Postgres template for eval trials."""
    anchor = date.fromisoformat(today) if today else None
    settings = _settings()
    if not force and not typer.confirm("This reseeds and clones the current database. Continue?"):
        raise typer.Exit(code=1)
    source, summary = asyncio.run(
        _demo_template(settings, target=target, anchor=anchor, coverage_gap=coverage_gap)
    )
    typer.echo(
        f"Seeded {source} ({summary['movements']} movements, {summary['imports']} imports) "
        f"and created template {target}. Clone a trial with "
        f"CREATE DATABASE <trial> TEMPLATE {target}."
    )


if __name__ == "__main__":
    app()
