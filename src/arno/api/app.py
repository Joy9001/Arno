"""The FastAPI application: one process, one origin, API plus the static web build."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from arno import __version__
from arno.agent.builder import build_agent
from arno.agent.history import recover_crashed_runs
from arno.agent.turns import TurnManager
from arno.api import (
    auth,
    capture,
    chat,
    deletion,
    health,
    history,
    home,
    imports,
    ledger,
    recaps,
    static,
    watch,
)
from arno.api.middleware import SecurityHeadersMiddleware
from arno.api.problems import problem
from arno.config import Settings, get_settings
from arno.db.bootstrap import set_agent_ro_password, set_backup_password, warn_if_superuser
from arno.db.conn import close_pool, connect, open_pool
from arno.db.migrate import run_migrations
from arno.db.pre_migration import dump_before_migrating
from arno.jobs import loop as job_loop
from arno.jobs.background import drain
from arno.ledger.accounts import ensure_system_accounts
from arno.ledger.categories import ensure_default_categories
from arno.ledger.errors import CommitOutcomeUnknownError, LedgerError
from arno.logging_utils import configure_logging
from arno.observability import bootstrap_observability, shutdown_observability
from arno.scheduled_questions import ensure_default_question

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
        await ensure_system_accounts(conn)
        await ensure_default_question(conn)
        if not settings.owner_email and not settings.passphrase:
            logger.warning("No sign-in is configured: set ARNO_OWNER_EMAIL or ARNO_PASSPHRASE.")
    return applied


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    pool: asyncpg.Pool | None = None
    await _bootstrap(app.state.settings)
    async with connect(app.state.settings.database_url) as conn:
        recovered = await recover_crashed_runs(conn)
        if recovered:
            logger.warning("recovered %d interrupted chat turn(s)", len(recovered))
    pool = await open_pool(app.state.settings.database_url)
    try:
        app.state.agent = build_agent()
        app.state.turns = TurnManager(app.state.agent)
        if app.state.settings.jobs_enabled:
            app.state.minute_loop = asyncio.create_task(job_loop.run(), name="arno-minute-loop")
            resumed = await job_loop.recover()
            if resumed:
                logger.warning("resumed %d interrupted task(s)", resumed)

        yield
    finally:
        try:
            minute_loop = getattr(app.state, "minute_loop", None)
            if minute_loop is not None:
                minute_loop.cancel()
                await asyncio.gather(minute_loop, return_exceptions=True)
            await drain(10)
            turns = getattr(app.state, "turns", None)
            if turns is not None:
                await turns.drain()
        finally:
            if pool is not None:
                await close_pool(pool)
            shutdown_observability()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    app = FastAPI(
        title="Arno",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    bootstrap_observability(app)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
    # Added last, so it wraps the host check and every response carries the policy.
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(ledger.router)
    app.include_router(history.router)
    app.include_router(home.router)
    app.include_router(imports.router)
    app.include_router(recaps.router)
    app.include_router(capture.router)
    app.include_router(chat.router)
    app.include_router(watch.router)
    app.include_router(deletion.router)
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

    @app.exception_handler(CommitOutcomeUnknownError)
    async def commit_outcome_unknown(
        request: Request, exc: CommitOutcomeUnknownError
    ) -> JSONResponse:
        route = request.scope.get("route")
        logger.warning(
            "commit outcome unknown while serving %s",
            getattr(route, "path", request.method),
        )
        return problem(
            503,
            detail=(
                "The connection was lost while saving. Check whether the change was recorded "
                "before trying again."
            ),
        )

    @app.exception_handler(asyncpg.PostgresConnectionError)
    @app.exception_handler(asyncpg.CannotConnectNowError)
    @app.exception_handler(asyncpg.ConnectionDoesNotExistError)
    @app.exception_handler(asyncpg.AdminShutdownError)
    @app.exception_handler(asyncpg.CrashShutdownError)
    @app.exception_handler(asyncpg.InternalClientError)
    @app.exception_handler(OSError)
    async def database_unreachable(request: Request, exc: Exception) -> JSONResponse:
        # TimeoutError subclasses OSError; db connect timeouts become ConnectionError at conn.py.
        if isinstance(exc, TimeoutError):
            raise exc
        route = request.scope.get("route")
        target = getattr(route, "path", request.method)
        if isinstance(exc, asyncpg.InternalClientError):
            logger.exception("database client error while serving %s", target)
        else:
            logger.warning("database unreachable while serving %s", target)
        return problem(503, detail="The database is unreachable.")

    @app.exception_handler(LedgerError)
    async def ledger_rule(request: Request, exc: LedgerError) -> JSONResponse:
        return problem(exc.status, title=exc.title, detail=exc.message, extra=exc.details)

    return app
