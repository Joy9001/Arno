"""Build the one agent: plain PydanticAI with the read and write toolsets."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic_ai import Agent, RunContext, Tool, ToolReturn
from pydantic_ai.capabilities import (
    AbstractCapability,
    Caching,
    ModelFallback,
    ProcessHistory,
    WebSearch,
)
from pydantic_ai.messages import DeferredToolRequests, ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.ui.vercel_ai.response_types import SourceUrlChunk

from arno.agent.capabilities import (
    InferenceContext,
    RootSpanIO,
    ToolErrorsToModel,
    trim_old_tool_payloads,
)
from arno.agent.deps import AgentDeps
from arno.agent.images import ImageAttachments
from arno.agent.instructions import READ_ONLY_SUFFIX, render_instructions
from arno.agent.models import backup_model, primary_model
from arno.agent.toolset import build_read_toolset, build_write_toolset
from arno.agent.usage import usage_hooks
from arno.config import get_settings
from arno.db.conn import connect
from arno.ledger.common import today as ledger_today
from arno.tools.search import brave_search

DEFAULT_CATEGORIES = [
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


async def _categories() -> list[str]:
    categories: list[str] = []
    try:
        async with connect() as conn:
            rows = await conn.fetch("SELECT name FROM categories WHERE active ORDER BY name")
        categories = [str(row["name"]) for row in rows]
    except Exception:  # noqa: BLE001 - a missing database must not lose the prompt
        pass
    return categories or DEFAULT_CATEGORIES


async def today_and_categories(ctx: Any) -> str:
    """The date and the active categories, read fresh on every request."""
    return (
        f"Today is {ledger_today().isoformat()}.\n"
        f"Categories: {', '.join(sorted(await _categories()))}"
    )


def _native_search(ctx: RunContext[AgentDeps]) -> WebSearchTool | None:
    """The model's own search, only when the run's model supports it.

    A model without it (an OpenAI-compatible chat endpoint) gets none, so it never errors.
    `TestModel` claims support for every native tool but cannot run one, so it gets none.
    """
    model = ctx.model
    if isinstance(model, TestModel) or not isinstance(model, Model):
        return None
    supported = model.profile.get("supported_native_tools", frozenset())
    return WebSearchTool(optional=True) if WebSearchTool in supported else None


def web_search(api_key: str | None) -> WebSearch[AgentDeps]:
    """Web search: the model's own tool when it has one, else Brave when a key is set.

    The native tool is optional, so a model without search and no key simply has none.
    """
    local = None
    if api_key:

        async def web_search(ctx: RunContext[AgentDeps], query: str) -> ToolReturn:
            """Search the web for current facts such as rates, rules, and prices. Cite each claim
            with [n] matching the result id. Never put the user's balances, account numbers,
            payees, or other personal details in the query."""
            try:
                results = await brave_search(
                    query, api_key=api_key, first_id=ctx.deps.web_sources + 1
                )
            except httpx.HTTPError:
                return ToolReturn(return_value={"error": "Web search is unavailable right now."})
            ctx.deps.web_sources += len(results)
            return ToolReturn(
                return_value=results,
                metadata=[
                    SourceUrlChunk(
                        source_id=item["id"],
                        url=item["url"],
                        title=item["title"],
                        provider_metadata={"arno": {"snippet": item["snippet"]}},
                    )
                    for item in results
                ],
            )

        local = Tool(web_search, takes_ctx=True)
    return WebSearch(native=_native_search, local=local or False)


def build_agent(
    *,
    model: Any = None,
    backup: Any = None,
    context_window: int | None = None,
    read_only: bool = False,
    name: str | None = None,
) -> Agent[AgentDeps, DeferredToolRequests | str]:
    """Build an agent. Tests pass a `TestModel` or `FunctionModel`; production reads env.

    `context_window` exists for eval trials: a smaller window so old tool payloads really trim.
    `read_only` builds the scheduled-question agent: the read tools only.
    `name` becomes the native `gen_ai.agent.name`; instrumentation is the process default.
    """
    primary = model or primary_model()
    if backup is None and model is None:
        backup = backup_model()
    toolsets = [build_read_toolset()]
    if not read_only:
        toolsets.append(build_write_toolset())

    def trim(ctx: RunContext[AgentDeps], messages: list[ModelMessage]) -> list[ModelMessage]:
        return trim_old_tool_payloads(ctx, messages, window=context_window)

    capabilities: list[AbstractCapability[Any]] = [
        InferenceContext(),
        RootSpanIO(),
        Caching(),
        ToolErrorsToModel(),
        ProcessHistory(trim),
        ImageAttachments(),
        web_search(get_settings().brave_api_key),
    ]
    if backup is not None:
        capabilities.append(ModelFallback(backup, sticky=False))
    capabilities.append(usage_hooks("chat"))
    return Agent(
        primary,
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        instructions=[
            render_instructions() + (READ_ONLY_SUFFIX if read_only else ""),
            today_and_categories,
        ],
        toolsets=toolsets,
        name=name or ("arno-scheduled-question" if read_only else "arno"),
        capabilities=capabilities,
    )


__all__ = ["READ_ONLY_SUFFIX", "build_agent"]
