"""A fresh Postgres database for every eval trial.

`CREATE DATABASE ... TEMPLATE` clones the seeded demo persona in well under a second, so no
shared state leaks between trials. Each lifecycle sets the database context variable for its
own task and drops the clone with `WITH (FORCE)` after the graders have read it.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from typing import Any

from pydantic_evals.evaluators.context import EvaluatorContext
from pydantic_evals.lifecycle import CaseLifecycle

from evals.models import EvalInput, EvalMeta, EvalOutput, InjectionSpec
from fin_agent.db.bootstrap import set_agent_ro_password, set_backup_password
from fin_agent.db.conn import connect, reset_database_url, use_database_url
from fin_agent.db.migrate import run_migrations
from fin_agent.demo.persona import seed_demo
from fin_agent.demo.template import (
    clone_database,
    create_database,
    database_dsn,
    drop_database,
)
from fin_agent.importers.synthetic import seed_injection_statement
from fin_agent.ledger.movements import record_lending
from fin_agent.ledger.payees import create_payee

DEFAULT_TEMPLATE = "fin_agent_eval_template"
TRIAL_PREFIX = "fin_agent_eval_trial"


def build_eval_template(
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
    create_database(admin_dsn, template)
    dsn = database_dsn(admin_dsn, template)
    conn = connect(dsn)
    try:
        run_migrations(conn)
        set_agent_ro_password(conn, os.environ.get("AGENT_RO_PASSWORD"))
        set_backup_password(conn, os.environ.get("BACKUP_PASSWORD"))
        seed_demo(
            conn,
            today=anchor,
            reset=True,
            statement_coverage=statement_coverage,
            coverage_gap=coverage_gap,
        )
        conn.commit()
    finally:
        conn.close()
    return template


async def _seed_injection(conn: Any, spec: InjectionSpec, *, today: date) -> None:
    hdfc = conn.execute(
        "SELECT id FROM accounts WHERE kind = 'bank' AND active ORDER BY name LIMIT 1"
    ).fetchone()
    if hdfc is None:
        raise RuntimeError("The demo template has no bank account")
    if spec.source == "statement":
        # The malicious note travels the real L3 path: reader, parser, checks, reconcile,
        # commit, outside_text, capture, and layout. No import or statement row is hand-built.
        await seed_injection_statement(
            conn,
            account_id=str(hdfc["id"]),
            narration=spec.text,
            day=today - timedelta(days=1),
            period_start=today - timedelta(days=30),
            period_end=today,
        )
        return
    payee = create_payee(
        conn,
        display_name=spec.text,
        kind="person",
        outside_text=True,
        actor="system",
    )
    lending = record_lending(
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
        conn.execute(
            """
            INSERT INTO captures (movement_id, kind, ref)
            VALUES (%s, 'photo', %s)
            """,
            (movement_id, spec.note_id),
        )
    conn.commit()


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

    async def setup(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.database = f"{self.trial_prefix}_{suffix}"
        clone_database(
            self.admin_dsn, source=self.template, target=self.database, terminate=False
        )
        self.dsn = database_dsn(self.admin_dsn, self.database)
        self._token = use_database_url(self.dsn)
        meta = self.case.metadata
        if meta is not None and meta.injection is not None:
            conn = connect()
            try:
                await _seed_injection(conn, meta.injection, today=date.today())
            finally:
                conn.close()

    async def prepare_context(
        self, ctx: EvaluatorContext[EvalInput, EvalOutput, EvalMeta]
    ) -> EvaluatorContext[EvalInput, EvalOutput, EvalMeta]:
        ctx.attributes["trial_db_url"] = self.dsn
        ctx.attributes["trial_database"] = self.database
        return ctx

    async def teardown(self, result: Any) -> None:
        if self._token is not None:
            reset_database_url(self._token)
            self._token = None
        if self.database:
            try:
                drop_database(self.admin_dsn, self.database)
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
