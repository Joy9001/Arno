"""Approval offers and approvals are counted from real events."""

from __future__ import annotations

import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.toolset import _gate


def _ctx(deps: AgentDeps, *, approved: bool = False) -> RunContext[AgentDeps]:
    return RunContext(
        deps=deps,
        model=TestModel(),
        usage=RunUsage(),
        tool_name="update_setup",
        tool_call_approved=approved,
    )


async def _flagged_thread(db, thread_id: str = "approval-thread") -> AgentDeps:
    await db.execute(
        "INSERT INTO conversations (id, title, outside_text_at) VALUES ($1, 'flag', now())",
        thread_id,
    )
    return AgentDeps(conversation_id=thread_id, message_id="msg-1", run_id="run-1")


async def test_an_offer_counts_once_and_an_approval_counts_once(db):
    deps = await _flagged_thread(db)
    with pytest.raises(ApprovalRequired):
        await _gate(_ctx(deps), "update setup")
    assert deps.approvals_offered == 1
    await _gate(_ctx(deps, approved=True), "update setup")
    assert deps.approvals == 1
