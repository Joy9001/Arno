"""Build the one agent: tools, instructions, and the harness capabilities."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.subagents import SubAgent, SubAgents, TaskControl

from arno.agent.capabilities import build_capabilities
from arno.agent.deps import AgentDeps
from arno.agent.instructions import render_instructions
from arno.agent.models import backup_model, primary_model
from arno.agent.toolset import build_read_toolset, build_write_toolset
from arno.db.conn import connect

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
    categories: list[str] = []
    try:
        async with connect() as conn:
            rows = await conn.fetch("SELECT name FROM categories WHERE active ORDER BY name")
        categories = [str(row["name"]) for row in rows]
    except Exception:  # noqa: BLE001 - a missing database must not lose the prompt
        pass
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


WOKEN_TEXT = """

A background task reported
- No one sent a message. This turn started because a task you delegated settled while the user
  was away; its report is the first message. Tell the user what happened in two or three plain
  sentences, from the report only. Nothing reached the ledger unless the report says an import is
  ready for review.
- Ready for review: say so; the review is on its import card.
- Out of steps (outcome `budget`): say why it stopped and point to Continue on the run card. Never
  resume it yourself.
- Interrupted by a restart (outcome `cancelled`): say so, and point to Continue on the run card.
- Failed: say why in plain words, never the raw error. What you may do next is in the block
  below.
- Never ask the user to upload a file again that the thread already has: a failed import keeps
  its file, and its import card has Retry.
"""

WOKEN_RETRY = """- This turn carries the failed imports' files. When the failure was a model or
  network error, or a script mistake the report names, start the parser once more with
  `delegate_task("parser", "<what failed and what to do differently>", background=True)` and say
  you are retrying. Otherwise do not: say why it failed, and that Retry on the import card runs
  it again.
"""

WOKEN_NO_RETRY = """- Do not start the parser again in this turn: this report is from an automatic
  retry, or no file failed. Say why it stopped and point to Retry on the import card.
"""


async def _statement_names(ids: tuple[str, ...]) -> list[str]:
    """The filenames of this turn's statement attachments, for the instruction block."""
    try:
        async with connect() as conn:
            rows = await conn.fetch(
                "SELECT id, filename FROM attachments WHERE id = ANY($1::text[])", list(ids)
            )
    except Exception:  # noqa: BLE001 - a missing database must not lose the prompt
        return []
    found = {str(row["id"]): str(row["filename"] or "statement") for row in rows}
    return [found[identifier] for identifier in ids if identifier in found]


async def _instructions(ctx: Any, *, read_only: bool = False) -> str:
    text = render_instructions(await _categories())
    deps = getattr(ctx, "deps", None)
    attachments = getattr(deps, "statement_attachments", ())
    woken = getattr(deps, "woken_by", ())
    if attachments and not woken:
        count = len(attachments)
        noun = "file" if count == 1 else "files"
        names = await _statement_names(tuple(attachments))
        listed = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        named = f" ({listed})" if listed else ""
        text += f"""

Uploaded statement files
- This turn has {count} statement {noun}{named}. Your first action is `delegate_task("parser",
  "Import the statement files from this turn.", background=True)`, before any other tool call and
  before any prose, then end your turn. Never put an attachment ID in the task text: the parser
  already has this turn's files. One upload is one task, and each file becomes an import card.
- This block is about the message you are answering now. It lists that message's files, plus
  any file an earlier message uploaded that no parser has read yet.
  Never say a file did not arrive while this block names one: a statement has no text to search
  for. If you already answered without handing it over, hand it to the parser now.
- The parser reports a status, counts, and anything blocking. It never writes to the ledger, and
  you cannot commit an import. If it asks a question, answer from the ledger when you can and ask
  the user only when you cannot. If a parser task was interrupted by a restart, resume it with
  `delegate_task(..., resume=<task_id>)` when the user asks to continue. Never ask for a
  statement password in chat; it goes on the import card.
"""
    paused = getattr(getattr(ctx, "deps", None), "paused_parser_tasks", ())
    if paused and not read_only:
        ids = ", ".join(f"`{task_id}`" for task_id in paused)
        text += f"""

Paused statement import
- The parser stopped before it finished in this thread (out of steps, or cut off by an app
  restart): task {ids}. Nothing reached the ledger.
  When the user asks to continue, resume it with
  `delegate_task("parser", "Continue reading the statement.", resume="<task_id>",
  background=True)` and end your turn. Each resume has a fresh step budget.
"""
    photos = getattr(deps, "photo_outcomes", ())
    if photos and not read_only:
        listed = "\n".join(f"  - Photo {index}: {note}" for index, note in enumerate(photos, 1))
        text += f"""

Photos with this message
- The message you are answering carried {len(photos)} photo(s). The host already read each one
  and showed its card to the user:
{listed}
- A photo is not a statement: never hand it to the parser, and never say it did not arrive. A
  screenshot listing many transactions cannot be logged from a photo; say so, and ask for the
  statement or export instead.
"""
    if woken and not read_only:
        text += WOKEN_TEXT
        if getattr(deps, "may_retry", False) and attachments:
            text += WOKEN_RETRY
        else:
            text += WOKEN_NO_RETRY
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
) -> Agent[AgentDeps, DeferredToolRequests | str]:
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
    if backup is not None and getattr(primary, "provider_name", None) in {
        "chatgpt",
        "openai-chatgpt",
    }:
        from pydantic_ai.exceptions import UserError

        raise UserError("ChatGPT plan use cannot fall back automatically. Remove CHAT_FALLBACK_*.")
    if spend_store is None and model is None:
        # A production caller (CLI chat, a host command) gets the same daily ledger as the API
        # instead of a private, absent cap. Tests and evals pass their own model and store.
        from arno.agent.spend import SharedSpendStore

        spend_store = SharedSpendStore()
    if read_only:
        toolsets = [build_read_toolset()]
    else:
        toolsets = [build_read_toolset(), build_write_toolset()]

    async def instructions_fn(ctx: Any) -> str:
        return await _instructions(ctx, read_only=read_only)

    subagents = None
    if parser_agent is not None and not read_only:
        from arno.agent.parser import PARSE_USAGE_LIMITS

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
        name=name or ("arno-scheduled-question" if read_only else "arno"),
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
