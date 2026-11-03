"""The host CLI: migrations, the server, and passkey recovery.

Admin commands never go over HTTP. Anyone with a shell on the host already has the data.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

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
from fin_agent.config import REPO_ROOT, ConfigurationError, get_settings
from fin_agent.db.bootstrap import set_agent_ro_password, set_backup_password
from fin_agent.db.conn import connect
from fin_agent.db.migrate import run_migrations
from fin_agent.db.pre_migration import dump_before_migrating
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
        dump_before_migrating(conn, settings)
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
    from fin_agent.logging_utils import install as install_log_sanitizer

    install_log_sanitizer()
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
    flags = check_messages(reply, turn_messages, kinds=("money", "percent"))
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


@app.command("eval")
def eval_command(
    model: list[str] = typer.Option(  # noqa: B008
        [], "--model", help="Model ID on the configured CHAT endpoint. Repeat for several."
    ),
    repeat: int = typer.Option(3, help="Trials per case; pass^k uses this."),
    category: list[str] = typer.Option(  # noqa: B008
        [],
        "--category",
        help="answers, writes, honesty, injection, advice, never_moves, long_threads",
    ),
    tag: list[str] = typer.Option([], "--tag", help="Only cases with any of these tags."),  # noqa: B008
    case_id: list[str] = typer.Option(  # noqa: B008
        [], "--case", help="Only these case IDs. Repeat for several."
    ),
    limit: int | None = typer.Option(None, help="Run only the first N cases after filtering."),
    per_category: int | None = typer.Option(
        None, "--per-category", help="Keep at most N cases from each category."
    ),
    note: str = typer.Option("", "--note", help="A note shown in the scorecard."),
    judge_model: str | None = typer.Option(
        None,
        "--judge-model",
        envvar="EVAL_JUDGE_MODEL",
        help="Pinned judge model for the advice rubric. Defaults to the model under test.",
    ),
    template: str = typer.Option("fin_agent_eval_template", help="Template database name."),
    concurrency: int = typer.Option(2, help="Trials that may run at once."),
    budget_usd: float = typer.Option(2.0, help="Spend ceiling for each trial, in USD."),
    publish: bool = typer.Option(
        True, "--publish/--no-publish", help="Replace the README scorecard table."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only list the selected cases."),
) -> None:
    """Run the agent eval suite against a configured cloud or local model.

    This is the only command that turns model requests on. Every trial clones a fresh database
    from the demo-persona template, so no trial can see another one's writes.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from evals.models import CATEGORY_LABELS
    from evals.runner import dry_run as list_cases
    from evals.runner import run_eval

    categories = set(category) or None
    if categories:
        unknown = categories - set(CATEGORY_LABELS)
        if unknown:
            raise typer.BadParameter(
                f"unknown categories: {', '.join(sorted(unknown))}; "
                f"choose from {', '.join(CATEGORY_LABELS)}"
            )
    tags = set(tag) or None
    case_ids = set(case_id) or None
    if dry_run:
        counts = list_cases(
            categories=categories,
            tags=tags,
            case_ids=case_ids,
            limit=limit,
            per_category=per_category,
        )
        total = sum(counts.values())
        typer.echo(f"{total} cases")
        for name, count in sorted(counts.items()):
            typer.echo(f"  {name}: {count}")
        return
    if not model:
        raise typer.BadParameter("pass at least one --model")
    if repeat < 1:
        raise typer.BadParameter("--repeat must be at least 1")
    if budget_usd <= 0:
        raise typer.BadParameter("--budget-usd must be positive")

    from decimal import Decimal

    cards = run_eval(
        model_names=list(model),
        repeat=repeat,
        template=template,
        judge_model=judge_model,
        categories=categories,
        tags=tags,
        case_ids=case_ids,
        limit=limit,
        per_category=per_category,
        max_concurrency=concurrency,
        run_budget_usd=Decimal(str(budget_usd)),
        publish=publish,
        note=note,
    )
    typer.echo(f"scorecards: {', '.join(card.model for card in cards)}")
    gate_failures = [
        card.model for card in cards if card.injection_trials > 0 and not card.gate_passed
    ]
    if gate_failures:
        typer.echo(
            "injection gate FAILED: "
            + ", ".join(gate_failures)
            + " (a lasting write landed without its confirm card, or a trial was unverified)",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command("backtest")
def backtest_command(
    source: str = typer.Option("auto", help="auto, db, or synthetic."),
    runs: int = typer.Option(1_000, help="Seeded walks for each month."),
    publish: bool = typer.Option(
        True, "--publish/--no-publish", help="Replace the README backtest section."
    ),
) -> None:
    """Run project() as of each month's first day and compare with the balance 30 days later."""
    from fin_agent.engine.backtest import backtest as run_backtest
    from fin_agent.engine.backtest import format_report, publish_report

    settings = _settings()
    conn = connect(settings.database_url)
    try:
        result = run_backtest(conn, source=source, runs=runs)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    finally:
        conn.close()
    typer.echo(format_report(result))
    if publish:
        publish_report(result, REPO_ROOT / "README.md")
        typer.echo("README.md updated.")


@app.command("hackerrank")
def hackerrank_command(
    dataset: Path | None = typer.Option(  # noqa: B008
        None, "--dataset", help="The local dataset folder. It is never committed."
    ),
    runs: int = typer.Option(1_000, help="Seeded walks for each sample."),
    publish: bool = typer.Option(
        True, "--publish/--no-publish", help="Replace the README HackerRank section."
    ),
) -> None:
    """Score the engine on the 25 labeled HackerRank 'Buy or Wait' samples, locally."""
    from fin_agent.engine.hackerrank import format_report, publish_report, score

    result = score(dataset, runs=runs)
    typer.echo(format_report(result))
    if publish:
        publish_report(result, REPO_ROOT / "README.md")
        typer.echo("README.md updated.")
    if not result.get("available"):
        raise typer.Exit(code=3)


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


@app.command("restore")
def restore_command(
    target: str | None = typer.Option(
        None,
        "--target",
        envvar="RESTORE_DATABASE_URL",
        help="Fresh database URL to restore into. Defaults to DATABASE_URL.",
    ),
    from_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--from-dir",
        help="A directory already extracted from a restic snapshot.",
    ),
    repository: str | None = typer.Option(
        None, "--repository", envvar="RESTIC_REPOSITORY", help="The restic repository."
    ),
    snapshot: str = typer.Option("latest", help="Snapshot ID or `latest`."),
    force: bool = typer.Option(
        False, "--force", help="Replace the target database if it already has tables."
    ),
    allow_no_manifest: bool = typer.Option(
        False,
        "--allow-no-manifest",
        help="Accept a dump without backup_manifest.json (manual pg_dump restores only).",
    ),
) -> None:
    """Restore a backup and verify every table, then the ledger's balances.

    With `--repository`, restic is used directly. With `--from-dir`, point at files extracted by
    `restic restore`. The restic password always comes from RESTIC_PASSWORD.
    """
    from fin_agent.backup_restore import RestoreError, restore_from_directory, restore_from_restic

    settings = _settings()
    target_dsn = target or settings.database_url
    try:
        if from_dir is not None:
            report = restore_from_directory(
                from_dir,
                target_dsn,
                force=force,
                require_manifest=not allow_no_manifest,
            )
        else:
            if not repository:
                typer.echo(
                    "error: pass --repository, set RESTIC_REPOSITORY, or use --from-dir",
                    err=True,
                )
                raise typer.Exit(code=2)
            report = restore_from_restic(
                repository=repository,
                password=os.environ.get("RESTIC_PASSWORD", ""),
                target_dsn=target_dsn,
                snapshot=snapshot,
                force=force,
                require_manifest=not allow_no_manifest,
            )
    except RestoreError as exc:
        typer.echo(f"restore failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = report.as_dict()
    typer.echo(
        f"restored {payload['dump']} from {payload['snapshot']} into {payload['restored_database']}"
    )
    for name, passed in report.checks.items():
        typer.echo(f"  {'ok' if passed else 'FAIL'}  {name}")
    for mismatch in report.mismatches:
        typer.echo(f"  mismatch: {mismatch}", err=True)
    typer.echo("restore verified" if report.ok else "restore verification FAILED")
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("import-spike")
def import_spike(
    folder: Path = typer.Argument(  # noqa: B008
        Path("private/statements"), help="Git-ignored folder with bank subfolders."
    ),
    bank: str | None = typer.Option(None, help="sbi, union, or slice. Omit for all three."),
    password: str | None = typer.Option(
        None, envvar="STATEMENT_PASSWORD", help="Statement password; never written anywhere."
    ),
    ledger_opening: int | None = typer.Option(
        None, help="Opening balance in paise when the bank statement does not print one."
    ),
    write: bool = typer.Option(
        True, "--write/--no-write", help="Write docs/fin-agent/import-spike-results.md."
    ),
) -> None:
    """Milestone 21: a fresh parse run per bank, then rerun the parser on the rest.

    It prints counts and status words only. The synthetic fixtures always run; real files run
    only when they are in a git-ignored folder.
    """
    from fin_agent.importers.spike import SpikeReport, run_real, run_synthetic

    synthetic = run_synthetic()
    if bank and bank not in {"sbi", "union", "slice"}:
        raise typer.BadParameter("bank is sbi, union, or slice")
    real: list[Any] = []
    notes: list[str] = []
    if folder.is_dir():
        if password is None and any(folder.iterdir()):
            password = typer.prompt(
                "Statement password (blank if none)", hide_input=True, default=""
            )
        real, notes = run_real(
            folder,
            password=password or None,
            bank=bank,
            ledger_opening_paise=ledger_opening,
        )
    else:
        notes = [f"No real statement folder at {folder}; the real spike is not run."]
    report = SpikeReport(
        synthetic=synthetic,
        real=real,
        real_root=str(folder) if real else None,
        notes=notes,
    )
    text = report.markdown()
    typer.echo(text)
    if write:
        target = Path("docs/fin-agent/import-spike-results.md")
        target.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {target}")


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
    conn = connect(settings.database_url)
    try:
        run_migrations(conn)
        set_agent_ro_password(conn, settings.agent_ro_password)
        set_backup_password(conn, settings.backup_password)
        summary = seed_demo(
            conn,
            today=anchor,
            reset=reset,
            statement_coverage=statement_coverage,
            coverage_gap=coverage_gap,
        )
    finally:
        conn.close()
    typer.echo(
        "Seeded demo persona: "
        f"{summary['accounts']} accounts, {summary['categories']} categories, "
        f"{summary['schedules']} schedules, {summary['goals']} goals, "
        f"{summary['movements']} movements, {summary['imports']} imports, "
        f"{summary['people']} people (today={summary['today']})."
    )


@demo_app.command("template")
def demo_template(
    target: str = typer.Option("fin_agent_demo_template", help="Template database name to create."),
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
    conn = connect(settings.database_url)
    try:
        run_migrations(conn)
        summary = seed_demo(conn, today=anchor, reset=True, coverage_gap=coverage_gap)
        source = conn.info.dbname
    finally:
        conn.close()
    clone_database(settings.database_url, source=source, target=target)
    typer.echo(
        f"Seeded {source} ({summary['movements']} movements, {summary['imports']} imports) "
        f"and created template {target}. Clone a trial with "
        f"CREATE DATABASE <trial> TEMPLATE {target}."
    )


if __name__ == "__main__":
    app()
