"""The host CLI: migrations, the server, and passkey recovery.

Admin commands never go over HTTP. Anyone with a shell on the host already has the data.
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import date
from pathlib import Path

import typer
import uvicorn

from fin_agent.agent.builder import build_agent
from fin_agent.agent.history import (
    create_thread,
    ensure_history,
    get_thread,
    mark_run_started,
    record_user_message,
    refresh_snapshot,
    save_records,
    title_for,
)
from fin_agent.auth.enrollment import enrollment_message
from fin_agent.auth.sessions import revoke_all_sessions
from fin_agent.auth.setup import issue_setup_token, setup_required
from fin_agent.config import ConfigurationError, get_settings
from fin_agent.db.bootstrap import set_agent_ro_password, set_backup_password
from fin_agent.db.conn import connect
from fin_agent.db.migrate import run_migrations
from fin_agent.demo.persona import seed_demo
from fin_agent.demo.template import clone_database
from fin_agent.ledger import export as ledger_export

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Fin-Agent administration and development.",
)
passkeys_app = typer.Typer(no_args_is_help=True, help="Passkey recovery.")
demo_app = typer.Typer(no_args_is_help=True, help="The synthetic demo persona.")
app.add_typer(passkeys_app, name="passkeys")
app.add_typer(demo_app, name="demo")

logger = logging.getLogger("fin_agent")


def _settings():
    try:
        return get_settings(refresh=True)
    except ConfigurationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _echo_enrollment(text: str) -> None:
    """Print a QR code even on a console whose stdout is not UTF-8 yet."""
    try:
        typer.echo(text)
    except UnicodeEncodeError:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        typer.echo(text)


@app.command()
def migrate() -> None:
    """Apply any pending SQL migrations."""
    settings = _settings()
    conn = connect(settings.database_url)
    try:
        applied = run_migrations(conn)
        set_agent_ro_password(conn, settings.agent_ro_password)
        set_backup_password(conn, settings.backup_password)
    finally:
        conn.close()
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        "fin_agent.api.app:create_app",
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


@app.command("setup-token")
def setup_token() -> None:
    """Issue a fresh first-run enrollment link (rotates the previous one)."""
    settings = _settings()
    conn = connect(settings.database_url)
    try:
        if not setup_required(conn):
            typer.echo("Setup is complete; use Settings → Passkeys to add a device.")
            raise typer.Exit(code=0)
        issued = issue_setup_token(conn, ttl_hours=settings.setup_token_ttl_hours)
    finally:
        conn.close()
    _echo_enrollment(
        enrollment_message(
            settings.rp_origin,
            issued.token,
            title=f"First-run setup is open (link valid {settings.setup_token_ttl_hours}h).",
        )
    )


@passkeys_app.command("list")
def passkeys_list() -> None:
    """List registered passkeys."""
    settings = _settings()
    conn = connect(settings.database_url)
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.created_at, c.last_used_at, u.display_name
            FROM webauthn_credentials c
            JOIN users u ON u.id = c.user_id
            ORDER BY c.created_at
            """
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        typer.echo("No passkeys are registered.")
        return
    for row in rows:
        used = row["last_used_at"].isoformat() if row["last_used_at"] else "never"
        typer.echo(f"{row['id']}  {row['name']}  created {row['created_at']}  last used {used}")


@passkeys_app.command("reset")
def passkeys_reset() -> None:
    """Delete every passkey and session, then reopen setup with a fresh enrollment link."""
    settings = _settings()
    conn = connect(settings.database_url)
    try:
        with conn.transaction():
            deleted = conn.execute("DELETE FROM webauthn_credentials").rowcount
            conn.execute("DELETE FROM webauthn_challenges")
        revoked = revoke_all_sessions(conn)
        if setup_required(conn):
            issued = issue_setup_token(conn, ttl_hours=settings.setup_token_ttl_hours)
        else:
            issued = None
    finally:
        conn.close()

    typer.echo(f"Deleted {deleted} passkey(s) and revoked {revoked} session(s).")
    if issued:
        _echo_enrollment(
            enrollment_message(
                settings.rp_origin,
                issued.token,
                title=(
                    "First-run setup is open. Scan this QR code or open the link "
                    f"(valid {settings.setup_token_ttl_hours}h)."
                ),
            )
        )


@app.command("chat")
def chat(
    thread_id: str | None = typer.Option(None, "--thread", help="Reopen a thread by ID."),
    message: str | None = typer.Option(None, "--message", help="Send one message and exit."),
) -> None:
    """Chat with the agent in the terminal."""
    settings = _settings()
    agent = build_agent()
    conn = connect(settings.database_url)
    try:
        thread = get_thread(conn, thread_id) if thread_id else None
        if thread is None:
            thread = create_thread(conn, title=title_for(message or "Terminal chat"))
            conn.commit()
        thread_id = str(thread["id"])
        history, _outside = ensure_history(conn, thread)
        history, _swap = refresh_snapshot(conn, thread, history)
        conn.commit()

        if message is not None:
            _run_cli_turn(conn, agent, thread, history, message)
            return

        typer.echo(f"Fin-Agent chat ({thread_id}). Ctrl-D to leave.")
        while True:
            try:
                text = typer.prompt("you")
            except EOFError, KeyboardInterrupt:
                typer.echo()
                return
            if not text.strip():
                continue
            history = _run_cli_turn(conn, agent, thread, history, text)
    finally:
        conn.close()


def _run_cli_turn(conn, agent, thread: dict, history: list, text: str) -> list:
    from fin_agent.agent.deps import AgentDeps
    from fin_agent.agent.number_check import check_messages

    message_id = f"cli_{uuid.uuid4().hex}"
    run_id = f"run_{uuid.uuid4().hex}"
    user_row = record_user_message(
        conn, thread_id=str(thread["id"]), client_id=message_id, text=text
    )
    mark_run_started(conn, str(thread["id"]), run_id)
    conn.commit()
    deps = AgentDeps(
        conversation_id=str(thread["id"]),
        message_id=str(user_row["id"]),
        run_id=run_id,
        flagged=thread.get("outside_text_at") is not None,
        user_texts=(text,),
    )
    result = agent.run_sync(text, message_history=history, deps=deps)
    turn_messages = result.all_messages()[len(history) :]
    reply = str(result.output)
    flags = check_messages(reply, turn_messages)
    with conn.transaction():
        save_records(
            conn,
            thread_id=str(thread["id"]),
            run_id=run_id,
            ui_message_id=message_id,
            history=history,
            turn_messages=turn_messages,
            ending="complete",
            number_flags=flags,
            title=thread.get("title"),
            all_messages=result.all_messages(),
        )
    typer.echo(result.output)
    if flags:
        typer.echo(f"couldn't trace: {', '.join(flags)}", err=True)
    return result.all_messages()


@app.command("export")
def export_command(
    format: str = typer.Option("json", help="json, csv, or sql."),
    out: Path | None = typer.Option(  # noqa: B008
        None, "--out", help="Write here instead of stdout."
    ),
) -> None:
    """Export the whole ledger as JSON, a CSV zip, or a pg_dump."""
    settings = _settings()
    conn = connect(settings.database_url)
    try:
        if format == "json":
            payload = ledger_export.export_json(conn)
            data = __import__("json").dumps(payload, indent=2, ensure_ascii=False, default=str)
        elif format == "csv":
            data = ledger_export.export_csv_zip(conn)
        elif format == "sql":
            data = ledger_export.export_pg_dump(settings.database_url)
        else:
            raise typer.BadParameter("format is json, csv, or sql")
    finally:
        conn.close()
    if out is None:
        if isinstance(data, bytes):
            sys.stdout.buffer.write(data)
        else:
            typer.echo(data)
    else:
        out.write_bytes(data) if isinstance(data, bytes) else out.write_text(data, encoding="utf-8")
        typer.echo(f"wrote {out}")


@demo_app.command("seed")
def demo_seed(
    today: str | None = typer.Option(None, help="Anchor date (YYYY-MM-DD). Defaults to today."),
    reset: bool = typer.Option(
        False, "--reset/--no-reset", help="Clear the L1 tables before seeding."
    ),
    force: bool = typer.Option(False, "--force", help="Skip the reset confirmation."),
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
    conn = connect(settings.database_url)
    try:
        run_migrations(conn)
        set_agent_ro_password(conn, settings.agent_ro_password)
        set_backup_password(conn, settings.backup_password)
        summary = seed_demo(conn, today=anchor, reset=reset)
    finally:
        conn.close()
    typer.echo(
        "Seeded demo persona: "
        f"{summary['accounts']} accounts, {summary['categories']} categories, "
        f"{summary['schedules']} schedules, {summary['goals']} goals, "
        f"{summary['movements']} movements, {summary['people']} people "
        f"(today={summary['today']})."
    )


@demo_app.command("template")
def demo_template(
    target: str = typer.Option("fin_agent_demo_template", help="Template database name to create."),
    today: str | None = typer.Option(None, help="Anchor date (YYYY-MM-DD). Defaults to today."),
    force: bool = typer.Option(False, "--force", help="Skip the reset confirmation."),
) -> None:
    """Seed the current database, then clone it as a Postgres template for eval trials."""
    anchor = date.fromisoformat(today) if today else None
    settings = _settings()
    if not force and not typer.confirm("This reseeds and clones the current database. Continue?"):
        raise typer.Exit(code=1)
    conn = connect(settings.database_url)
    try:
        run_migrations(conn)
        summary = seed_demo(conn, today=anchor, reset=True)
        source = conn.info.dbname
    finally:
        conn.close()
    clone_database(settings.database_url, source=source, target=target)
    typer.echo(
        f"Seeded {source} ({summary['movements']} movements) and created template {target}. "
        f"Clone a trial with CREATE DATABASE <trial> TEMPLATE {target}."
    )


if __name__ == "__main__":
    app()
