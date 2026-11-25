"""A fresh Postgres database for every eval trial.

`CREATE DATABASE ... TEMPLATE` clones the seeded demo persona in well under a second, so no
shared state leaks between trials. Each lifecycle sets the database context variable for its
own task and drops the clone with `WITH (FORCE)` after the graders have read it.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from typing import Any, override

from pydantic_evals.evaluators.context import EvaluatorContext
from pydantic_evals.lifecycle import CaseLifecycle

from arno.db.bootstrap import set_agent_ro_password, set_backup_password
from arno.db.conn import connect, reset_database_url, use_database_url
from arno.db.migrate import run_migrations
from arno.demo.persona import seed_demo
from arno.demo.template import (
    clone_database,
    create_database,
    database_dsn,
    drop_database,
)
from evals.models import EvalInput, EvalMeta, EvalOutput

DEFAULT_TEMPLATE = "arno_eval_template"
TRIAL_PREFIX = "arno_eval_trial"


async def build_eval_template(
    admin_dsn: str,
    *,
    template: str = DEFAULT_TEMPLATE,
    anchor: date | None = None,
    statement_coverage: bool = True,
    coverage_gap: bool = False,
) -> str:
    """Build the seeded template from scratch, never from the user's ledger.

    A self-hoster's real database is not touched: the template is its own database, created
    empty, migrated, and seeded with the synthetic persona. `coverage_gap=True` leaves the oldest
    of the persona's three covered months as a gap for honesty cases.
    """
    anchor = anchor or date.today()
    await create_database(admin_dsn, template)
    dsn = database_dsn(admin_dsn, template)
    async with connect(dsn) as conn:
        await run_migrations(conn)
        await set_agent_ro_password(conn, os.environ.get("AGENT_RO_PASSWORD"))
        await set_backup_password(conn, os.environ.get("BACKUP_PASSWORD"))
        await seed_demo(
            conn,
            today=anchor,
            reset=True,
            statement_coverage=statement_coverage,
            coverage_gap=coverage_gap,
        )
    return template


class TrialDatabase(CaseLifecycle[EvalInput, EvalOutput, EvalMeta]):
    """Clone the template for one trial, then drop it after the evaluators finish."""

    def __init__(
        self,
        case: Any,
        *,
        admin_dsn: str,
        template: str = DEFAULT_TEMPLATE,
        trial_prefix: str = TRIAL_PREFIX,
    ) -> None:
        super().__init__(case)
        self.admin_dsn = admin_dsn
        self.template = template
        self.trial_prefix = trial_prefix
        self.database = ""
        self.dsn = ""
        self._token: Any = None

    @override
    async def setup(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.database = f"{self.trial_prefix}_{suffix}"
        await clone_database(
            self.admin_dsn, source=self.template, target=self.database, terminate=False
        )
        self.dsn = database_dsn(self.admin_dsn, self.database)
        self._token = use_database_url(self.dsn)
        meta = self.case.metadata
        if meta is not None and meta.unknown_balance:
            async with connect() as conn:
                await conn.execute(
                    "UPDATE accounts SET statement_balance_paise = NULL, "
                    "statement_balance_as_of = NULL WHERE liquid"
                )

    @override
    async def prepare_context(
        self, ctx: EvaluatorContext[EvalInput, EvalOutput, EvalMeta]
    ) -> EvaluatorContext[EvalInput, EvalOutput, EvalMeta]:
        ctx.attributes["trial_db_url"] = self.dsn
        ctx.attributes["trial_database"] = self.database
        return ctx

    @override
    async def teardown(self, result: Any) -> None:
        if self._token is not None:
            reset_database_url(self._token)
            self._token = None
        if self.database:
            try:
                await drop_database(self.admin_dsn, self.database)
            except Exception:
                # A failed trial must not hide the reason it failed behind a cleanup error.
                pass
            self.database = ""


def lifecycle_factory(
    admin_dsn: str,
    *,
    template: str = DEFAULT_TEMPLATE,
    trial_prefix: str = TRIAL_PREFIX,
) -> Any:
    def make(case: Any) -> TrialDatabase:
        return TrialDatabase(
            case, admin_dsn=admin_dsn, template=template, trial_prefix=trial_prefix
        )

    return make


__all__ = [
    "DEFAULT_TEMPLATE",
    "TRIAL_PREFIX",
    "TrialDatabase",
    "build_eval_template",
    "lifecycle_factory",
]
