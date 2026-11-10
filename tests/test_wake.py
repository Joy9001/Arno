"""A task report that settles while no one is chatting starts a turn that tells the user."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.subagents import (
    DelegationTask,
    DelegationTasks,
    TaskAssignedEvent,
    TaskFollowUpEvent,
    is_task_report,
)

from fin_agent.agent import history as history_mod
from fin_agent.agent.builder import _instructions
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.turns import ChatTurn, TurnManager
from fin_agent.agent.wake import (
    RETRY_MARK,
    KeepRetryMark,
    WakePolicy,
    may_retry,
    waiting_reports,
)
from fin_agent.api.tasks import _follow_up
from fin_agent.api.wake import wake

THREAD = "conv_wake"
TASK = "9" * 32

# The event hooks below ignore the run context entirely.
_CTX = cast(RunContext[Any], None)


def _report(task_id: str = TASK, **fields) -> DelegationTask:
    values: dict[str, Any] = {
        "agent_name": "parser",
        "prompt": "Import the statement files from this turn.",
        "conversation_id": THREAD,
        "background": True,
        "status": "finished",
        "outcome": "failed",
        "output": "The parser script failed: NameError: name 'files' is not defined.",
        "finished_at": time.time(),
    }
    values.update(fields)
    return DelegationTask(id=task_id, **values)


def _assigned(prompt: str = "Import the statement again.") -> TaskAssignedEvent:
    return TaskAssignedEvent(task_id="a" * 32, agent_name="parser", prompt=prompt)


async def test_the_policy_allows_one_marked_retry_and_no_resume():
    policy = WakePolicy(may_retry=True)
    first, second = _assigned(), _assigned()
    await policy.one_retry(_CTX, first)
    await policy.one_retry(_CTX, second)
    assert not first.vetoed and first.prompt.startswith(RETRY_MARK)
    assert second.vetoed

    resume = TaskFollowUpEvent(task_id=TASK, agent_name="parser", prompt="Continue.")
    await policy.no_resume(_CTX, resume)
    assert resume.vetoed

    no_retry = WakePolicy(may_retry=False)
    event = _assigned()
    await no_retry.one_retry(_CTX, event)
    assert event.vetoed


async def test_a_continued_retry_keeps_its_mark():
    owner = DelegationTasks()
    owner.records[TASK] = _report(prompt=RETRY_MARK + "Import again.", outcome="budget")
    owner.records["8" * 32] = _report("8" * 32)
    keep = KeepRetryMark(owner)
    retried = TaskFollowUpEvent(task_id=TASK, agent_name="parser", prompt="Continue reading.")
    first = TaskFollowUpEvent(task_id="8" * 32, agent_name="parser", prompt="Continue reading.")
    await keep.keep(_CTX, retried)
    await keep.keep(_CTX, first)
    assert retried.prompt == RETRY_MARK + "Continue reading."
    assert first.prompt == "Continue reading."


async def test_waiting_ends_when_the_report_is_old():
    turns = TurnManager(SimpleNamespace())
    assert _follow_up(_report(), turns) == "waiting"
    assert _follow_up(_report(finished_at=time.time() - 3600), turns) is None


async def test_a_retry_report_gets_no_second_retry_and_a_stopped_task_waits_silently():
    owner = DelegationTasks()
    owner.records[TASK] = _report()
    owner.records["8" * 32] = _report("8" * 32, user_stopped=True)
    owner.records["7" * 32] = _report("7" * 32, delivered=True)
    reports = waiting_reports(owner, THREAD)
    assert [record.id for record in reports] == [TASK]
    assert may_retry(reports)
    assert not may_retry([_report(prompt=RETRY_MARK + "Import again.")])


async def test_a_waiting_report_starts_a_turn_that_reads_it_first(ledger):
    requests: list[ModelRequest] = []

    def reply(messages, info):
        requests.append(messages[-1])
        return ModelResponse(parts=[TextPart("Your statement import failed; nothing changed.")])

    async def stream(messages, info):
        part = reply(messages, info).parts[0]
        assert isinstance(part, TextPart)
        yield part.content

    agent = Agent(FunctionModel(reply, stream_function=stream), deps_type=AgentDeps)
    owner = DelegationTasks()
    async with owner.opened():
        await history_mod.create_thread(ledger, title="Statement", thread_id=THREAD)
        owner.records[TASK] = _report()
        turns = TurnManager(agent, tasks=owner)
        app = SimpleNamespace(state=SimpleNamespace(tasks=owner, turns=turns, agent=agent))
        rewoken: list[str] = []

        async def record_wake(thread_id: str) -> None:
            rewoken.append(thread_id)

        turns.waker = record_wake

        assert await wake(app, THREAD)
        turn = turns.get(THREAD)
        assert turn is not None and turn.deps.woken_by == (TASK,) and turn.deps.may_retry
        # No one is watching and the report came from a statement: lasting writes ask first.
        assert turn.deps.flagged
        assert _follow_up(owner.records[TASK], turns) == "replying"
        await turn.done.wait()
        task = turn.task
        assert task is not None
        await task
        await asyncio.sleep(0)
        # A finished turn checks once more for a report that arrived while it ran.
        assert rewoken == [THREAD]

        # The report is the turn's first request, read once, and nothing else asked for it.
        assert any(
            isinstance(part, UserPromptPart) and is_task_report(part) for part in requests[0].parts
        )
        assert owner.records[TASK].delivered
        assert _follow_up(owner.records[TASK], turns) is None
        assert not await wake(app, THREAD)

        rows = await ledger.fetch(
            "SELECT role, body_text FROM messages WHERE conversation_id = $1", THREAD
        )
        assert [(row["role"], row["body_text"]) for row in rows] == [
            ("assistant", "Your statement import failed; nothing changed.")
        ]
        running = await ledger.fetchval(
            "SELECT run_started_at FROM conversations WHERE id = $1", THREAD
        )
        assert running is None


async def test_a_thread_with_a_running_turn_is_not_woken(ledger):
    owner = DelegationTasks()
    owner.records[TASK] = _report()
    turns = TurnManager(SimpleNamespace(), tasks=owner)
    # The user's own turn is running: it reads the report, or wakes the thread when it ends.
    turns.turns[THREAD] = ChatTurn(
        thread_id=THREAD,
        message_id="msg_running",
        run_id="run_running",
        history=[],
        deps=AgentDeps(conversation_id=THREAD),
    )
    app = SimpleNamespace(state=SimpleNamespace(tasks=owner, turns=turns, agent=None))
    assert not await wake(app, THREAD)
    assert _follow_up(owner.records[TASK], turns) == "waiting"


async def test_the_woken_turn_is_told_what_to_say_and_whether_it_may_retry(ledger):
    deps = AgentDeps(conversation_id=THREAD, woken_by=(TASK,), may_retry=True)
    text = await _instructions(SimpleNamespace(deps=deps))
    assert "A background task reported" in text and "start the parser once more" in text
    assert "Do not start the parser again" not in text

    deps.may_retry = False
    assert "Do not start the parser again" in await _instructions(SimpleNamespace(deps=deps))
    assert "A background task reported" not in await _instructions(
        SimpleNamespace(deps=AgentDeps(conversation_id=THREAD))
    )
