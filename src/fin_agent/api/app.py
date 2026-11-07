"""The FastAPI application: one process, one origin, API plus the static web build."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fin_agent import __version__
from fin_agent.agent.builder import build_agent
from fin_agent.agent.history import recover_crashed_runs
from fin_agent.agent.spend import make_spend_store
from fin_agent.agent.turns import TurnManager
from fin_agent.api import auth, capture, chat, health, home, imports, ledger, static, watch
from fin_agent.api.middleware import SecurityHeadersMiddleware
from fin_agent.api.problems import problem
from fin_agent.auth.enrollment import enrollment_message
from fin_agent.auth.setup import ensure_setup_token, setup_required
from fin_agent.config import Settings, get_settings
from fin_agent.db.bootstrap import set_agent_ro_password, set_backup_password
from fin_agent.db.conn import connect
from fin_agent.db.migrate import run_migrations
from fin_agent.db.pre_migration import dump_before_migrating
from fin_agent.jobs.runner import JobRunner, recover_interrupted_jobs
from fin_agent.jobs.schedules import ensure_builtin_schedules, sync_scheduled_questions
from fin_agent.ledger.accounts import ensure_system_accounts
from fin_agent.ledger.categories import ensure_default_categories
from fin_agent.ledger.errors import LedgerError
from fin_agent.logging_utils import configure_logging
from fin_agent.notifications import ensure_vapid_keys
from fin_agent.observability import bootstrap_observability, shutdown_observability

logger = logging.getLogger("fin_agent")


async def _bootstrap(settings: Settings) -> list[str]:
    applied: list[str] = []
    async with connect(settings.database_url) as conn:
        if settings.auto_migrate:
            await dump_before_migrating(conn, settings)
            applied = await run_migrations(conn)
            if applied:
                logger.info("applied migrations %s", applied)
        await set_agent_ro_password(conn, settings.agent_ro_password)
        await set_backup_password(conn, settings.backup_password)
        await ensure_default_categories(conn)
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
                    "First-run setup is open. Run `fin-agent setup` for a fresh enrollment link."
                )
    return applied


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = None
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
    app.state.agent = build_agent(spend_store=spend_store)
    app.state.turns = TurnManager(app.state.agent)
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
            shutdown_observability()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    bootstrap_observability()

    app = FastAPI(
        title="Fin-Agent",
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
    app.include_router(capture.router)
    app.include_router(chat.router)
    app.include_router(watch.router)
    app.include_router(static.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem(exc.status_code, detail=detail, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, exc: RequestValidationError):
        return problem(422, detail="The request body or query is not valid.")

    @app.exception_handler(asyncpg.PostgresConnectionError)
    @app.exception_handler(asyncpg.CannotConnectNowError)
    @app.exception_handler(asyncpg.ConnectionDoesNotExistError)
    @app.exception_handler(OSError)
    async def database_unreachable(request: Request, exc: Exception):
        route = request.scope.get("route")
        logger.warning(
            "database unreachable while serving %s",
            getattr(route, "path", request.method),
        )
        return problem(503, detail="The database is unreachable.")

    @app.exception_handler(LedgerError)
    async def ledger_rule(request: Request, exc: LedgerError):
        return problem(exc.status, title=exc.title, detail=exc.message, extra=exc.details)

    return app
