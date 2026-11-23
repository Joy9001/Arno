"""A task report that settles while no one is chatting starts a turn that tells the user."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
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

from arno.agent import history as history_mod
from arno.agent.builder import _instructions
from arno.agent.deps import AgentDeps
from arno.agent.turns import ChatTurn, TurnManager
from arno.agent.wake import (
    RETRY_MARK,
    KeepRetryMark,
    WakePolicy,
    may_retry,
    waiting_reports,
    waiting_threads,
)
from arno.api.tasks import _follow_up
from arno.api.wake import start_server_turn, wake

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


async def _failed_import(conn, task_id: str) -> str:
    """A statement the reported task failed on, with its file still stored."""
    import hashlib

    from arno.importers.pipeline import create_import
    from arno.ledger.audit import new_id

    payload = b"Date,Narration,Debit,Credit,Balance\n"
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', 'statement.csv', 'text/csv', $2, $3, $4)
        """,
        attachment_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    row = await create_import(conn, attachment_id=attachment_id)
    await conn.execute(
        "UPDATE imports SET status = 'failed', task_id = $1 WHERE id = $2", task_id, row["id"]
    )
    return attachment_id


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
        failed_file = await _failed_import(ledger, TASK)
        turns = TurnManager(agent, tasks=owner)
        app = SimpleNamespace(state=SimpleNamespace(tasks=owner, turns=turns, agent=agent))
        rewoken: list[str] = []

        async def record_wake(thread_id: str) -> None:
            rewoken.append(thread_id)

        turns.waker = record_wake

        assert await wake(app, THREAD)
        turn = turns.get(THREAD)
        assert turn is not None and turn.deps.woken_by == (TASK,) and turn.deps.may_retry
        # The one retry carries the failed import's file, so the parser can read it again.
        assert turn.deps.statement_attachments == (failed_file,)
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
    deps = AgentDeps(
        conversation_id=THREAD,
        woken_by=(TASK,),
        may_retry=True,
        statement_attachments=("att_failed",),
    )
    text = await _instructions(SimpleNamespace(deps=deps))
    assert "A background task reported" in text and "start the parser once more" in text
    assert "Do not start the parser again" not in text
    # A woken turn's files are the failed run's: the upload block for a new message is not shown.
    assert "Uploaded statement files" not in text

    deps.may_retry = False
    assert "Do not start the parser again" in await _instructions(SimpleNamespace(deps=deps))
    assert "A background task reported" not in await _instructions(
        SimpleNamespace(deps=AgentDeps(conversation_id=THREAD))
    )


async def test_waiting_threads_recover_only_recent_undelivered_reports():
    owner = DelegationTasks()
    now = time.time()
    owner.records[TASK] = _report()
    owner.records["6" * 32] = _report(
        "6" * 32, conversation_id="conv_old", finished_at=now - 90_000
    )
    owner.records["5" * 32] = _report("5" * 32, conversation_id="conv_delivered", delivered=True)
    owner.records["4" * 32] = _report("4" * 32, conversation_id="conv_stopped", user_stopped=True)
    owner.records["3" * 32] = _report("3" * 32, conversation_id="conv_child", parent_id="1" * 32)
    owner.records["2" * 32] = _report("2" * 32, conversation_id="conv_running", status="running")
    since = now - 86_400

    assert waiting_threads(owner, since=since) == {THREAD}

    turns = TurnManager(SimpleNamespace())
    woken = asyncio.Event()
    scheduled: list[str] = []

    async def record_wake(thread_id: str) -> None:
        scheduled.append(thread_id)
        woken.set()

    turns.waker = record_wake
    for thread_id in waiting_threads(owner, since=since):
        turns.schedule_wake(thread_id)
    await asyncio.wait_for(woken.wait(), timeout=5)
    assert scheduled == [THREAD]


async def test_a_failed_wake_start_clears_the_run_marker(ledger, monkeypatch):
    await history_mod.create_thread(ledger, title="Wake", thread_id=THREAD)
    agent = Agent(
        FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("ok")])),
        deps_type=AgentDeps,
    )
    turns = TurnManager(agent)
    app = SimpleNamespace(state=SimpleNamespace(tasks=None, turns=turns, agent=agent))

    def fail_start(**kwargs):
        del kwargs
        raise RuntimeError("cannot start")

    monkeypatch.setattr(turns, "start", fail_start)
    thread = await history_mod.get_thread(ledger, THREAD)
    assert thread is not None
    with pytest.raises(RuntimeError, match="cannot start"):
        await start_server_turn(app, ledger, thread=thread, deps=AgentDeps(conversation_id=THREAD))

    row = await ledger.fetchrow(
        "SELECT run_started_at, active_run_id FROM conversations WHERE id = $1", THREAD
    )
    assert row["run_started_at"] is None
    assert row["active_run_id"] is None


async def test_a_parser_task_without_statement_files_is_refused_before_it_runs():
    from arno.agent.wake import ParserNeedsFiles

    class Event:
        vetoed: str | None = None

        def veto(self, reason: str) -> None:
            self.vetoed = reason

    policy = ParserNeedsFiles()
    refused = Event()
    await policy.needs_files(
        cast(Any, SimpleNamespace(deps=AgentDeps(conversation_id=THREAD))), cast(Any, refused)
    )
    assert refused.vetoed and "no statement file" in refused.vetoed

    allowed = Event()
    await policy.needs_files(
        cast(
            Any,
            SimpleNamespace(deps=AgentDeps(conversation_id=THREAD, statement_attachments=("a",))),
        ),
        cast(Any, allowed),
    )
    assert allowed.vetoed is None
