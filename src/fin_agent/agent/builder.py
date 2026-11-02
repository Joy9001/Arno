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


def _instructions(ctx: Any) -> str:
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
    return render_instructions(categories)


def build_agent(
    *,
    model: Any = None,
    backup: Any = None,
    spend_store: Any = None,
    tracer_provider: Any = None,
    run_budget_usd: Decimal | None = None,
    context_window: int | None = None,
) -> Agent[AgentDeps, str]:
    """Build an agent. Tests pass a `TestModel` or `FunctionModel`; production reads env.

    `run_budget_usd` and `context_window` exist for eval trials: one spend ceiling for the
    whole trial, and a smaller window so a long thread really compacts.
    """
    primary = model or primary_model()
    if backup is None and model is None:
        backup = backup_model()
    return Agent(
        primary,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        instructions=_instructions,
        toolsets=[build_read_toolset(), build_write_toolset()],
        capabilities=build_capabilities(
            primary=primary,
            backup=backup,
            spend_store=spend_store,
            tracer_provider=tracer_provider,
            run_budget_usd=run_budget_usd,
            context_window=context_window,
        ),
    )


__all__ = ["build_agent"]
