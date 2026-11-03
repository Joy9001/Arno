"""Build the one agent: tools, instructions, and the harness capabilities."""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests

from fin_agent.agent.capabilities import build_capabilities
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.instructions import render_instructions
from fin_agent.agent.models import backup_model, primary_model
from fin_agent.agent.toolset import build_read_toolset, build_write_toolset
from fin_agent.db.conn import connect

READ_ONLY_SUFFIX = """

Scheduled question
- You are running on your own schedule, with nobody present to answer or undo. Read-only: never
  write, never propose a write as if it already happened. Answer for the inbox in a few short
  paragraphs, with the numbers and the data-quality notes. If something is missing, say what it
  is instead of asking a question.
"""


def _categories() -> list[str]:
    try:
        with closing(connect()) as conn:
            rows = conn.execute("SELECT name FROM categories WHERE active ORDER BY name").fetchall()
        categories = [str(row["name"]) for row in rows]
    except Exception:  # noqa: BLE001 - a missing database must not lose the prompt
        categories = []
    if not categories:
        categories = [
            "Food",
            "Groceries",
            "Transport",
            "Rent",
            "Utilities",
            "Shopping",
            "Health",
            "Entertainment",
            "Family Transfer",
            "Savings & Investments",
        ]
    return categories


def _instructions(ctx: Any, *, read_only: bool = False) -> str:
    del ctx
    text = render_instructions(_categories())
    return text + READ_ONLY_SUFFIX if read_only else text


def build_agent(
    *,
    model: Any = None,
    backup: Any = None,
    spend_store: Any = None,
    tracer_provider: Any = None,
    run_budget_usd: Decimal | None = None,
    question_budget_usd: Decimal | None = None,
    context_window: int | None = None,
    read_only: bool = False,
) -> Agent[AgentDeps, str]:
    """Build an agent. Tests pass a `TestModel` or `FunctionModel`; production reads env.

    `run_budget_usd` and `context_window` exist for eval trials: one spend ceiling for the
    whole trial, and a smaller window so a long thread really compacts. `read_only` builds the
    scheduled-question agent: the same read tools and Code Mode, no write tools, one run ceiling.
    """
    primary = model or primary_model()
    if backup is None and model is None:
        backup = backup_model()
    if read_only:
        toolsets = [build_read_toolset()]
    else:
        toolsets = [build_read_toolset(), build_write_toolset()]
    return Agent(
        primary,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        instructions=lambda ctx: _instructions(ctx, read_only=read_only),
        toolsets=toolsets,
        capabilities=build_capabilities(
            primary=primary,
            backup=backup,
            spend_store=spend_store,
            tracer_provider=tracer_provider,
            run_budget_usd=run_budget_usd,
            question_budget_usd=question_budget_usd,
            context_window=context_window,
        ),
    )


__all__ = ["READ_ONLY_SUFFIX", "build_agent"]
