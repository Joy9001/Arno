"""A fresh Postgres database for every eval trial.

`CREATE DATABASE ... TEMPLATE` clones the seeded demo persona in well under a second, so no
shared state leaks between trials. Each lifecycle sets the database context variable for its
own task and drops the clone with `WITH (FORCE)` after the graders have read it.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from typing import Any, override

from pydantic_evals.evaluators.context import EvaluatorContext
from pydantic_evals.lifecycle import CaseLifecycle

from evals.models import EvalInput, EvalMeta, EvalOutput, InjectionSpec
from arno.agent.cost_journal import reset_journal_database, use_journal_database
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
from arno.importers.synthetic import seed_injection_statement
from arno.ledger.movements import record_lending
from arno.ledger.payees import create_payee

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


async def _seed_injection(conn: Any, spec: InjectionSpec, *, today: date) -> None:
    hdfc = await conn.fetchrow(
        "SELECT id FROM accounts WHERE kind = 'bank' AND active ORDER BY name LIMIT 1"
    )
    if hdfc is None:
        raise RuntimeError("The demo template has no bank account")
    if spec.source == "statement":
        # The malicious note travels the real L3 path: reader, parser, checks, reconcile,
        # commit, outside_text, capture, and layout. No import or statement row is hand-built.
        # The fixture picks the window, so its printed balances agree with the ledger.
        await seed_injection_statement(
            conn,
            account_id=str(hdfc["id"]),
            narration=spec.text,
        )
        return
    payee = await create_payee(
        conn,
        display_name=spec.text,
        kind="person",
        outside_text=True,
        actor="system",
    )
    lending = await record_lending(
        conn,
        account_id=str(hdfc["id"]),
        payee_id=str(payee["id"]),
        amount_paise=100,
        expected_date=today + timedelta(days=30),
        description=spec.text,
        date=today - timedelta(days=1),
        actor="system",
    )
    movement_id = str(lending["id"])
    if spec.source == "photo":
        await conn.execute(
            """
            INSERT INTO captures (movement_id, kind, ref)
            VALUES ($1, 'photo', $2)
            """,
            movement_id,
            spec.note_id,
        )


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
        self._journal_token: Any = None

    @override
    async def setup(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.database = f"{self.trial_prefix}_{suffix}"
        await clone_database(
            self.admin_dsn, source=self.template, target=self.database, terminate=False
        )
        self.dsn = database_dsn(self.admin_dsn, self.database)
        self._token = use_database_url(self.dsn)
        # Keep cost-only receipts recoverable after this trial's financial clone is dropped.
        # Trial counters remain isolated in memory; this does not debit the owner's daily cap.
        self._journal_token = use_journal_database(self.admin_dsn)
        meta = self.case.metadata
        if meta is not None and meta.injection is not None:
            async with connect() as conn:
                await _seed_injection(conn, meta.injection, today=date.today())

    @override
    async def prepare_context(
        self, ctx: EvaluatorContext[EvalInput, EvalOutput, EvalMeta]
    ) -> EvaluatorContext[EvalInput, EvalOutput, EvalMeta]:
        ctx.attributes["trial_db_url"] = self.dsn
        ctx.attributes["trial_database"] = self.database
        return ctx

    @override
    async def teardown(self, result: Any) -> None:
        if self._journal_token is not None:
            reset_journal_database(self._journal_token)
            self._journal_token = None
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
