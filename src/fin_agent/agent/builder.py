"""Build the one agent: tools, instructions, and the harness capabilities."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.subagents import SubAgent, SubAgents, TaskControl

from fin_agent.agent.capabilities import build_capabilities
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.instructions import render_instructions
from fin_agent.agent.models import backup_model, primary_model
from fin_agent.agent.toolset import build_read_toolset, build_write_toolset
from fin_agent.db.conn import connect

# How long a delegation may hold the chat turn before it continues in the background.
AUTO_BACKGROUND_SECONDS = 15.0

READ_ONLY_SUFFIX = """

Scheduled question
- You are running on your own schedule, with nobody present to answer or undo. Read-only: never
  write, never propose a write as if it already happened. Answer for the inbox in a few short
  paragraphs, with the numbers and the data-quality notes. If something is missing, say what it
  is instead of asking a question.
"""


async def _categories() -> list[str]:
    try:
        async with connect() as conn:
            rows = await conn.fetch("SELECT name FROM categories WHERE active ORDER BY name")
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


async def _instructions(ctx: Any, *, read_only: bool = False) -> str:
    text = render_instructions(await _categories())
    attachments = getattr(getattr(ctx, "deps", None), "statement_attachments", ())
    if attachments:
        count = len(attachments)
        noun = "file" if count == 1 else "files"
        text += f"""

Uploaded statement files
- This turn has {count} statement {noun}. Hand the upload to the `parser` subagent in the
  background with `delegate_task("parser", "Import the statement files from this turn.",
  background=True)` and end your turn. Never put an attachment ID in the task text: the parser
  already has this turn's files. One upload is one task, and each file becomes an import card.
- The parser reports a status, counts, and anything blocking. It never writes to the ledger, and
  you cannot commit an import. If it asks a question, answer from the ledger when you can and ask
  the user only when you cannot. If a parser task was interrupted by a restart, resume it with
  `delegate_task(..., resume=<task_id>)` when the user asks to continue. Never ask for a
  statement password in chat; it goes on the import card.
"""
    return text + READ_ONLY_SUFFIX if read_only else text


def build_agent(
    *,
    model: Any = None,
    backup: Any = None,
    spend_store: Any = None,
    run_budget_usd: Decimal | None = None,
    question_budget_usd: Decimal | None = None,
    context_window: int | None = None,
    read_only: bool = False,
    name: str | None = None,
    parser_agent: Any = None,
) -> Agent[AgentDeps, str]:
    """Build an agent. Tests pass a `TestModel` or `FunctionModel`; production reads env.

    `run_budget_usd` and `context_window` exist for eval trials: one spend ceiling for the
    whole trial, and a smaller window so a long thread really compacts. `read_only` builds the
    scheduled-question agent: the same read tools and Code Mode, no write tools, one run ceiling.
    `name` becomes the native `gen_ai.agent.name`; instrumentation is the process default.
    `parser_agent` is the parser subagent, offered to the chat agent through `SubAgents`.
    """
    primary = model or primary_model()
    if backup is None and model is None:
        backup = backup_model()
    if spend_store is None and model is None:
        # A production caller (CLI chat, a host command) gets the same daily ledger as the API
        # instead of a private, absent cap. Tests and evals pass their own model and store.
        from fin_agent.agent.spend import SharedSpendStore

        spend_store = SharedSpendStore()
    if read_only:
        toolsets = [build_read_toolset()]
    else:
        toolsets = [build_read_toolset(), build_write_toolset()]

    async def instructions_fn(ctx: Any) -> str:
        return await _instructions(ctx, read_only=read_only)

    subagents = None
    if parser_agent is not None and not read_only:
        from fin_agent.agent.parser import PARSE_USAGE_LIMITS

        subagents = SubAgents(
            agents=[
                SubAgent(
                    parser_agent,
                    usage_limits=PARSE_USAGE_LIMITS,
                    can_ask_questions=True,
                    resumable=True,
                )
            ],
            forward_usage=False,
            contain_errors=True,
            # A parse never holds a chat turn: a delegation the model didn't background (a resume,
            # say) becomes a background task once it has run this long.
            task_control=TaskControl(auto_background_after=AUTO_BACKGROUND_SECONDS),
        )

    return Agent(
        primary,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        instructions=instructions_fn,
        toolsets=toolsets,
        name=name or ("fin-agent-scheduled-question" if read_only else "fin-agent"),
        capabilities=build_capabilities(
            primary=primary,
            backup=backup,
            spend_store=spend_store,
            run_budget_usd=run_budget_usd,
            question_budget_usd=question_budget_usd,
            context_window=context_window,
            subagents=subagents,
        ),
    )


__all__ = ["READ_ONLY_SUFFIX", "build_agent"]
