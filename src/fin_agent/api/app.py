"""The FastAPI application: one process, one origin, API plus the static web build."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fin_agent import __version__
from fin_agent.api import auth, health, static
from fin_agent.api.middleware import SecurityHeadersMiddleware
from fin_agent.api.problems import problem
from fin_agent.auth.setup import issue_setup_token, setup_required
from fin_agent.config import Settings, get_settings
from fin_agent.db.bootstrap import set_agent_ro_password
from fin_agent.db.conn import connect
from fin_agent.db.migrate import run_migrations

logger = logging.getLogger("fin_agent")


def _bootstrap(settings: Settings) -> None:
    conn = connect(settings.database_url)
    try:
        if settings.auto_migrate:
            applied = run_migrations(conn)
            if applied:
                logger.info("applied migrations %s", applied)
        set_agent_ro_password(conn, settings.agent_ro_password)
        if setup_required(conn):
            # Every start while setup is open prints a fresh token and replaces the old hash, so
            # the line in the current container log always works.
            token = issue_setup_token(conn, ttl_hours=settings.setup_token_ttl_hours)
            logger.warning(
                "First-run setup is open. Open %s and enter this setup token: %s",
                settings.rp_origin,
                token,
            )
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap(app.state.settings)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

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
    app.include_router(static.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem(exc.status_code, detail=detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, exc: RequestValidationError):
        return problem(422, detail="The request body or query is not valid.")

    @app.exception_handler(psycopg.OperationalError)
    async def database_unreachable(request: Request, exc: psycopg.OperationalError):
        logger.warning("database unreachable while serving %s", request.url.path)
        return problem(503, detail="The database is unreachable.")

    return app
