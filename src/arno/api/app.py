"""The FastAPI application: one process, one origin, API plus the static web build."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_ai_harness.step_persistence import PostgresStepStore
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from arno import __version__
from arno.agent.builder import build_agent
from arno.agent.history import recover_crashed_runs
from arno.agent.parser import build_parser_agent
from arno.agent.spend import make_spend_store
from arno.agent.turns import TurnManager
from arno.agent.wake import waiting_threads
from arno.api import (
    auth,
    capture,
    chat,
    health,
    home,
    imports,
    ledger,
    recaps,
    static,
    tasks,
    watch,
)
from arno.api.middleware import SecurityHeadersMiddleware
from arno.api.problems import problem
from arno.api.tasks import TaskOwner
from arno.api.wake import wake
from arno.auth.enrollment import enrollment_message
from arno.auth.setup import ensure_setup_token, setup_required
from arno.config import Settings, get_settings
from arno.db.bootstrap import set_agent_ro_password, set_backup_password, warn_if_superuser
from arno.db.conn import connect
from arno.db.migrate import run_migrations
from arno.db.pre_migration import dump_before_migrating
from arno.importers.seed_rules import ensure_seed_rules
from arno.importers.task_status import reconcile as reconcile_imports
from arno.importers.task_status import settle_imports
from arno.jobs.runner import JobRunner, recover_interrupted_jobs
from arno.jobs.schedules import ensure_builtin_schedules, sync_scheduled_questions
from arno.ledger.accounts import ensure_system_accounts
from arno.ledger.categories import ensure_default_categories
from arno.ledger.errors import LedgerError
from arno.logging_utils import configure_logging
from arno.notifications import ensure_vapid_keys
from arno.observability import bootstrap_observability, shutdown_observability

logger = logging.getLogger("arno")


async def _bootstrap(settings: Settings) -> list[int]:
    applied: list[int] = []
    async with connect(settings.database_url) as conn:
        await warn_if_superuser(conn)
        if settings.auto_migrate:
            await dump_before_migrating(conn, settings)
            applied = await run_migrations(conn)
            if applied:
                logger.info("applied migrations %s", applied)
        await set_agent_ro_password(conn, settings.agent_ro_password)
        await set_backup_password(conn, settings.backup_password)
        await ensure_default_categories(conn)
        await ensure_seed_rules(conn)
        await ensure_system_accounts(conn)
        await ensure_vapid_keys(conn, subject=settings.vapid_subject)
        await ensure_builtin_schedules(conn)
        await sync_scheduled_questions(conn)
        if await setup_required(conn):
            token = await ensure_setup_token(conn, ttl_hours=settings.setup_token_ttl_hours)
            if token:
                logger.warning(
                    "%s",
                    enrollment_message(
                        settings.rp_origin,
                        token,
                        title="First-run setup is open.",
                    ),
                    # The link embeds a one-time enrollment secret. It belongs on the operator's
                    # local console; standard logs stay on the host.
                    extra={"console_only": True},
                )
            else:
                logger.warning(
                    "First-run setup is open. Run `arno setup` for a fresh enrollment link."
                )
    return applied


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    pool = None
    tasks_pool = None
    await _bootstrap(app.state.settings)
    async with connect(app.state.settings.database_url) as conn:
        recovered = await recover_crashed_runs(conn)
        if recovered:
            logger.warning("recovered %d interrupted chat turn(s)", len(recovered))
        interrupted = await recover_interrupted_jobs(conn)
        if interrupted:
            logger.warning("requeued %d interrupted job(s)", interrupted)
    spend_store, pool = await make_spend_store(app.state.settings.database_url)
    app.state.spend_store = spend_store
    app.state.spend_pool = pool
    try:
        tasks_pool = await asyncpg.create_pool(
            app.state.settings.database_url, min_size=1, max_size=4, command_timeout=30
        )
    except Exception:  # noqa: BLE001 - the app still serves read-only panels
        logger.exception("subagent store pool did not start")
    step_store = PostgresStepStore(tasks_pool) if tasks_pool is not None else None

    async def observe(event: Any) -> None:
        await _settle_parser(event)
        task = event.task
        turns = getattr(app.state, "turns", None)
        # A report that settles while no one is chatting starts the turn that tells the user.
        if event.event is None and task.status == "finished" and turns is not None:
            turns.schedule_wake(task.conversation_id)

    tasks = TaskOwner(
        directory=app.state.settings.tasks_dir, step_store=step_store, observer=observe
    )
    parser = build_parser_agent(plan_pool=tasks_pool, spend_store=spend_store)
    app.state.tasks = tasks
    async with tasks.opened():
        try:
            async with connect() as conn:
                if settled := await reconcile_imports(conn, tasks.records):
                    logger.warning("settled %d import(s) left parsing", settled)
        except Exception:  # noqa: BLE001 - a stale status must not stop the app
            logger.exception("import status reconcile failed")
        app.state.agent = build_agent(spend_store=spend_store, parser_agent=parser)
        app.state.turns = TurnManager(app.state.agent, tasks=tasks)

        async def wake_thread(thread_id: str) -> None:
            await wake(app, thread_id)

        app.state.turns.waker = wake_thread
        for thread_id in waiting_threads(tasks, since=time.time() - 86_400):
            app.state.turns.schedule_wake(thread_id)
        app.state.jobs = JobRunner()
        if app.state.settings.jobs_enabled:
            app.state.jobs.start()

        try:
            yield
        finally:
            try:
                await app.state.jobs.stop()
                await app.state.turns.drain()
            finally:
                if pool is not None:
                    await pool.close()
                if tasks_pool is not None:
                    await tasks_pool.close()
                shutdown_observability()


async def _settle_parser(event: Any) -> None:
    """When a parser task ends, give its still-parsing imports their terminal state."""
    task = event.task
    if event.event is not None or task.agent_name != "parser" or task.status != "finished":
        return
    try:
        async with connect() as conn:
            await settle_imports(conn, task_id=task.id, outcome=task.outcome, output=task.output)
    except Exception:  # noqa: BLE001 - an observer that raises would abort the parent run
        logger.exception("could not settle imports for task %s", task.id)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    bootstrap_observability()

    app = FastAPI(
        title="Arno",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
    # Added last, so it wraps the host check and every response carries the policy.
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(ledger.router)
    app.include_router(home.router)
    app.include_router(imports.router)
    app.include_router(tasks.router)
    app.include_router(recaps.router)
    app.include_router(capture.router)
    app.include_router(chat.router)
    app.include_router(watch.router)
    app.include_router(static.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem(
            exc.status_code,
            detail=detail,
            headers=dict(exc.headers) if exc.headers is not None else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(422, detail="The request body or query is not valid.")

    @app.exception_handler(asyncpg.PostgresConnectionError)
    @app.exception_handler(asyncpg.CannotConnectNowError)
    @app.exception_handler(asyncpg.ConnectionDoesNotExistError)
    @app.exception_handler(OSError)
    async def database_unreachable(request: Request, exc: Exception) -> JSONResponse:
        route = request.scope.get("route")
        logger.warning(
            "database unreachable while serving %s",
            getattr(route, "path", request.method),
        )
        return problem(503, detail="The database is unreachable.")

    @app.exception_handler(LedgerError)
    async def ledger_rule(request: Request, exc: LedgerError) -> JSONResponse:
        return problem(exc.status, title=exc.title, detail=exc.message, extra=exc.details)

    return app
