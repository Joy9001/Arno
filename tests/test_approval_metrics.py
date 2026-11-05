"""Approval offers and approvals are counted from real events."""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ApprovalRequired

from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.toolset import _gate


class _Ctx:
    def __init__(self, deps: AgentDeps, *, approved: bool = False) -> None:
        self.deps = deps
        self.tool_name = "update_setup"
        self.tool_call_approved = approved


def _flagged_thread(db, thread_id: str = "approval-thread") -> AgentDeps:
    db.execute(
        "INSERT INTO conversations (id, title, outside_text_at) VALUES (%s, 'flag', now())",
        (thread_id,),
    )
    db.commit()
    return AgentDeps(conversation_id=thread_id, message_id="msg-1", run_id="run-1")


def test_an_offer_counts_once_and_an_approval_counts_once(db):
    deps = _flagged_thread(db)
    with pytest.raises(ApprovalRequired):
        _gate(_Ctx(deps), "update setup")
    assert deps.approvals_offered == 1
    _gate(_Ctx(deps, approved=True), "update setup")
    assert deps.approvals == 1
