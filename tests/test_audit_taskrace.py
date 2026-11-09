"""Saving a task record survives a second writer on the same directory.

The harness writes one fixed `<id>.tmp` and renames it, serialised only per owner, so two owners
on one tasks directory lose each other's temp file and the loser raises `FileNotFoundError`. The
app's owner writes a temp name unique to each save instead, and makes the directory again if it
was cleaned up under it.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from pydantic_ai_harness.subagents import DelegationTask, DelegationTasks

from fin_agent.api.tasks import TaskOwner

ROUNDS = 60


def _record(task_id: str) -> DelegationTask:
    return DelegationTask(
        id=task_id, agent_name="parser", prompt="Import the statement.", conversation_id="conv_save"
    )


async def _save(owner: DelegationTasks, record: DelegationTask) -> None:
    for _ in range(ROUNDS):
        await owner.save(record)


async def test_two_owners_on_one_directory_both_save_the_same_task(tmp_path):
    record = _record("a" * 32)
    first = TaskOwner(directory=tmp_path)
    second = TaskOwner(directory=tmp_path)
    await asyncio.gather(_save(first, record), _save(second, record))

    saved = json.loads((tmp_path / f"{record.id}.json").read_text(encoding="utf-8"))
    assert saved["id"] == record.id
    assert not list(tmp_path.glob("*.tmp"))


async def test_a_save_survives_its_directory_being_removed(tmp_path):
    record = _record("b" * 32)
    owner = TaskOwner(directory=tmp_path / "tasks")
    await owner.save(record)
    shutil.rmtree(tmp_path / "tasks")

    await owner.save(record)

    assert json.loads((tmp_path / "tasks" / f"{record.id}.json").read_text())["id"] == record.id


async def test_an_unsafe_task_id_is_refused_before_it_reaches_the_filesystem(tmp_path):
    owner = TaskOwner(directory=tmp_path)
    with pytest.raises(ValueError):
        await owner.save(_record("../escape"))
    assert not list(tmp_path.iterdir())


def test_owners_on_one_directory_work_across_event_loops(tmp_path):
    # Two apps in one process (a TestClient per test) each own their own loop.
    record = _record("c" * 32)
    asyncio.run(TaskOwner(directory=tmp_path).save(record))
    asyncio.run(TaskOwner(directory=tmp_path).save(record))

    assert json.loads((tmp_path / f"{record.id}.json").read_text())["id"] == record.id
