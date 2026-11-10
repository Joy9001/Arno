"""The host CLI: migrations, the server, and passkey recovery.

Admin commands never go over HTTP. Anyone with a shell on the host already has the data.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import typer
import uvicorn
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests

from arno.agent.builder import build_agent
from arno.agent.deps import AgentDeps
from arno.agent.history import (
    create_thread,
    ensure_history,
    get_thread,
    mark_run_started,
    record_user_message,
    refresh_snapshot,
    save_records,
    title_for,
)
from arno.auth.enrollment import enrollment_message
from arno.auth.sessions import revoke_all_sessions
from arno.auth.setup import issue_setup_token, setup_required
from arno.config import REPO_ROOT, ConfigurationError, Settings, get_settings
from arno.db.bootstrap import set_agent_ro_password, set_backup_password
from arno.db.conn import Connection, connect
from arno.db.migrate import run_migrations
from arno.db.pre_migration import dump_before_migrating
from arno.demo.persona import seed_demo
from arno.demo.template import clone_database
from arno.ledger import export as ledger_export
from arno.logging_utils import configure_logging
from arno.observability import bootstrap_observability

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


@app.command("chat")
def chat(
    thread_id: str | None = typer.Option(None, "--thread", help="Reopen a thread by ID."),
    message: str | None = typer.Option(None, "--message", help="Send one message and exit."),
) -> None:
    """Chat with the agent in the terminal."""
    settings = _settings()
    configure_logging()
    bootstrap_observability()
    asyncio.run(_run_chat_command(settings, thread_id, message))


async def _run_chat_command(settings: Settings, thread_id: str | None, message: str | None) -> None:
    from arno.agent.spend import SharedSpendStore

    spend_store = SharedSpendStore()
    try:
        agent = build_agent(spend_store=spend_store)
        async with connect(settings.database_url) as conn:
            thread = await get_thread(conn, thread_id) if thread_id else None
            if thread_id is not None and thread is None:
                typer.echo(f"error: no thread {thread_id} on this instance", err=True)
                raise typer.Exit(code=2)
            if thread is None:
                thread = await create_thread(conn, title=title_for(message or "Terminal chat"))
            thread_id = str(thread["id"])
            history, _outside = await ensure_history(conn, thread)
            history, _swap = await refresh_snapshot(conn, thread, history)

            if message is not None:
                await _run_cli_turn(conn, agent, thread, history, message)
                return

            typer.echo(f"Arno chat ({thread_id}). Ctrl-D to leave.")
            while True:
                try:
                    text = typer.prompt("you")
                except EOFError, KeyboardInterrupt:
                    typer.echo()
                    return
                if not text.strip():
                    continue
                history = await _run_cli_turn(conn, agent, thread, history, text)
    finally:
        spend_store.close()


async def _run_cli_turn(
    conn: Connection,
    agent: Agent[AgentDeps, DeferredToolRequests | str],
    thread: dict[str, Any],
    history: list[Any],
    text: str,
) -> list[Any]:
    from arno.agent.deps import AgentDeps
    from arno.agent.number_check import check_messages
    from arno.agent.turns import _this_turn

    message_id = f"cli_{uuid.uuid4().hex}"
    run_id = f"run_{uuid.uuid4().hex}"
    user_row = await record_user_message(
        conn, thread_id=str(thread["id"]), client_id=message_id, text=text
    )
    await mark_run_started(conn, str(thread["id"]), run_id)
    deps = AgentDeps(
        conversation_id=str(thread["id"]),
        message_id=str(user_row["id"]),
        run_id=run_id,
        flagged=thread.get("outside_text_at") is not None,
        user_texts=(text,),
    )
    result = await agent.run(text, message_history=history, deps=deps, run_id=run_id)
    # Compaction may shorten the run's history, so the turn is found by run id, not by length.
    turn_messages = _this_turn(result.all_messages(), history, run_id, message_id)
    reply = str(result.output)
    flags = check_messages(reply, turn_messages, kinds=("money", "percent"))
    async with conn.transaction():
        await save_records(
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
    template: str = typer.Option("arno_eval_template", help="Template database name."),
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

    configure_logging()
    bootstrap_observability()
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


async def _run_backtest(settings: Settings, *, source: str, runs: int) -> Any:
    from arno.engine.backtest import backtest as run_backtest

    async with connect(settings.database_url) as conn:
        return await run_backtest(conn, source=source, runs=runs)


@app.command("backtest")
def backtest_command(
    source: str = typer.Option("auto", help="auto, db, or synthetic."),
    runs: int = typer.Option(1_000, help="Seeded walks for each month."),
    publish: bool = typer.Option(
        True, "--publish/--no-publish", help="Replace the README backtest section."
    ),
) -> None:
    """Run project() as of each month's first day and compare with the balance 30 days later."""
    from arno.engine.backtest import format_report, publish_report

    settings = _settings()
    try:
        result = asyncio.run(_run_backtest(settings, source=source, runs=runs))
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(format_report(result))
    if publish:
        try:
            publish_report(result, REPO_ROOT / "README.md")
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        typer.echo("README.md updated.")


async def _run_repair_openings(settings: Settings, *, dry_run: bool) -> list[dict[str, Any]]:
    from arno.importers.repair import repair_openings

    async with connect(settings.database_url) as conn:
        return await repair_openings(conn, dry_run=dry_run)


@app.command("repair-openings")
def repair_openings_command(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what would change, in aggregate, and write nothing."
    ),
    yes: bool = typer.Option(False, "--yes", help="Apply without asking."),
) -> None:
    """Place each committed import's opening at its statement's first day.

    For a ledger written before the commit path booked openings: every date a statement covered
    read short by its printed opening. Run it once after upgrading; it is idempotent.
    """
    from arno.importers.repair import format_changes

    settings = _settings()
    preview = asyncio.run(_run_repair_openings(settings, dry_run=True))
    if not preview:
        typer.echo(
            "Nothing to repair: every import with an evidenced opening already has it at its "
            "statement's first day."
        )
        return
    typer.echo(format_changes(preview))
    if dry_run:
        typer.echo("Dry run: nothing written.")
        return
    if not yes and not typer.confirm(f"Apply these {len(preview)} repair(s)?"):
        typer.echo("Nothing written.")
        raise typer.Exit(code=1)
    applied = asyncio.run(_run_repair_openings(settings, dry_run=False))
    typer.echo(format_changes(applied))
    typer.echo("Repaired. Undo an import from the Imports screen to reverse its repair too.")


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
    from arno.engine.hackerrank import format_report, publish_report, score

    result = score(dataset, runs=runs)
    typer.echo(format_report(result))
    if publish:
        publish_report(result, REPO_ROOT / "README.md")
        typer.echo("README.md updated.")
    if not result.get("available"):
        raise typer.Exit(code=3)


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


async def _restore(
    *,
    from_dir: Path | None,
    repository: str | None,
    target_dsn: str,
    snapshot: str,
    force: bool,
    require_manifest: bool,
) -> Any:
    from arno.backup_restore import RestoreError, restore_from_directory, restore_from_restic

    if from_dir is not None:
        return await restore_from_directory(
            from_dir,
            target_dsn,
            force=force,
            require_manifest=require_manifest,
        )
    if repository is None:
        raise RestoreError("a restic restore needs --repository or RESTIC_REPOSITORY")
    return await restore_from_restic(
        repository=repository,
        password=os.environ.get("RESTIC_PASSWORD", ""),
        target_dsn=target_dsn,
        snapshot=snapshot,
        force=force,
        require_manifest=require_manifest,
    )


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
    from arno.backup_restore import RestoreError

    settings = _settings()
    target_dsn = target or settings.database_url
    if from_dir is None and not repository:
        typer.echo(
            "error: pass --repository, set RESTIC_REPOSITORY, or use --from-dir",
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        report = asyncio.run(
            _restore(
                from_dir=from_dir,
                repository=repository,
                target_dsn=target_dsn,
                snapshot=snapshot,
                force=force,
                require_manifest=not allow_no_manifest,
            )
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
        True, "--write/--no-write", help="Write docs/arno/import-spike-results.md."
    ),
) -> None:
    """Milestone 21: a fresh parse run per bank, then rerun the parser on the rest.

    It prints counts and status words only. The synthetic fixtures always run; real files run
    only when they are in a git-ignored folder.
    """
    from arno.importers.spike import SpikeReport, run_real, run_synthetic

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
        target = Path("docs/arno/import-spike-results.md")
        target.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {target}")


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
