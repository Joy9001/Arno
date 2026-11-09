"""A turn the server starts when a background task reports while no one is chatting.

The harness keeps a settled task's report for the conversation's next run and leaves starting that
run to the host (`DelegationReports(priority='asap')` with no new prompt). These are the host's
rules for it: which reports start a turn, and what that turn may delegate.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic_ai.capabilities import AbstractCapability, on_event
from pydantic_ai_harness.subagents import (
    DelegationTask,
    DelegationTasks,
    TaskAssignedEvent,
    TaskFollowUpEvent,
)

RETRY_MARK = "Automatic retry. "
"""Starts the prompt of a task a server-started turn delegated, so its report can't retry."""


def waiting_reports(tasks: DelegationTasks, thread_id: str) -> list[DelegationTask]:
    """Settled reports this thread has not read yet. A task the user stopped needs no word."""
    return [
        record for record in tasks.reports(conversation_id=thread_id) if not record.user_stopped
    ]


def waiting_threads(tasks: DelegationTasks, *, since: float) -> set[str]:
    """Threads with a report that settled after `since` and is still waiting, for a restart.

    An older report is left for the thread's next message: a turn about something days old would
    surprise the user more than it helps.
    """
    return {
        record.conversation_id
        for record in tasks.records.values()
        if record.parent_id is None
        and record.background
        and record.status == "finished"
        and not record.delivered
        and not record.user_stopped
        and (record.finished_at or 0) >= since
    }


def may_retry(reports: Iterable[DelegationTask]) -> bool:
    """A report from a retry the server-started turn made gets no second retry."""
    return not any(record.prompt.startswith(RETRY_MARK) for record in reports)


class KeepRetryMark(AbstractCapability[Any]):
    """A resume rewrites a task's prompt, so a retry the user continues keeps its mark here."""

    def __init__(self, tasks: DelegationTasks) -> None:
        self.tasks = tasks
        self.id = "keep_retry_mark"

    @on_event(TaskFollowUpEvent)
    async def keep(self, ctx: Any, event: TaskFollowUpEvent) -> None:
        record = self.tasks.records.get(event.task_id)
        if record is not None and record.prompt.startswith(RETRY_MARK):
            if not event.prompt.startswith(RETRY_MARK):
                event.prompt = RETRY_MARK + event.prompt


class WakePolicy(AbstractCapability[Any]):
    """What a server-started turn may delegate: at most one fresh task, and never a resume.

    A resume of a paused run is the user's Continue. A new task is the one automatic retry, and
    only when the report it answers did not come from a retry itself.
    """

    def __init__(self, *, may_retry: bool) -> None:
        self.retries = 1 if may_retry else 0
        self.id = "wake_policy"

    @on_event(TaskFollowUpEvent)
    async def no_resume(self, ctx: Any, event: TaskFollowUpEvent) -> None:
        event.veto("Continuing a run is the user's choice. Point them to Continue on the run card.")

    @on_event(TaskAssignedEvent)
    async def one_retry(self, ctx: Any, event: TaskAssignedEvent) -> None:
        if self.retries < 1:
            event.veto(
                "This statement was already retried once without the user. Tell them why it "
                "failed and what they can do instead."
            )
            return
        self.retries -= 1
        event.prompt = RETRY_MARK + event.prompt


__all__ = [
    "RETRY_MARK",
    "KeepRetryMark",
    "WakePolicy",
    "may_retry",
    "waiting_reports",
    "waiting_threads",
]
