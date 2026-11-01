"""The host CLI: migrations, the server, and passkey recovery.

Admin commands never go over HTTP. Anyone with a shell on the host already has the data.
"""

from __future__ import annotations

import logging

import typer
import uvicorn

from fin_agent.auth.sessions import revoke_all_sessions
from fin_agent.auth.setup import issue_setup_token, setup_required
from fin_agent.config import ConfigurationError, get_settings
from fin_agent.db.bootstrap import set_agent_ro_password
from fin_agent.db.conn import connect
from fin_agent.db.migrate import run_migrations

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Fin-Agent administration and development.",
)
passkeys_app = typer.Typer(no_args_is_help=True, help="Passkey recovery.")
app.add_typer(passkeys_app, name="passkeys")

logger = logging.getLogger("fin_agent")


def _settings():
    try:
        return get_settings(refresh=True)
    except ConfigurationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def migrate() -> None:
    """Apply any pending SQL migrations."""
    settings = _settings()
    conn = connect(settings.database_url)
    try:
        applied = run_migrations(conn)
        set_agent_ro_password(conn, settings.agent_ro_password)
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
    """Issue a new first-run setup token, for when the log line is gone."""
    settings = _settings()
    conn = connect(settings.database_url)
    try:
        if not setup_required(conn):
            typer.echo("Setup is complete; there is nothing to do.")
            raise typer.Exit(code=0)
        token = issue_setup_token(conn, ttl_hours=settings.setup_token_ttl_hours)
    finally:
        conn.close()
    typer.echo(f"First-run setup token (valid {settings.setup_token_ttl_hours}h): {token}")


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
    """Delete every passkey and session, then reopen setup with a fresh token."""
    settings = _settings()
    conn = connect(settings.database_url)
    try:
        with conn.transaction():
            deleted = conn.execute("DELETE FROM webauthn_credentials").rowcount
            conn.execute("DELETE FROM webauthn_challenges")
        revoked = revoke_all_sessions(conn)
        if setup_required(conn):
            token = issue_setup_token(conn, ttl_hours=settings.setup_token_ttl_hours)
        else:
            token = None
    finally:
        conn.close()

    typer.echo(f"Deleted {deleted} passkey(s) and revoked {revoked} session(s).")
    if token:
        typer.echo(
            f"First-run setup is open. Enter this setup token (valid "
            f"{settings.setup_token_ttl_hours}h): {token}"
        )


if __name__ == "__main__":
    app()
