"""A pgtask worker process, killed and restarted by step8_probe4.py."""
import asyncio
import os
import sys

import psycopg
from pgtask import Task, TaskRegistry, Worker

DSN = os.environ["DSN"]
tasks = TaskRegistry(queue_name="jobs")


async def note(kind):
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as c:
        await c.execute("INSERT INTO attempts(kind, at) VALUES (%s, now())", (kind,))


@tasks.task("slow")
async def slow(task: Task, payload: dict) -> dict:
    await note(f"slow:attempt{task.attempt}")
    await asyncio.sleep(float(payload.get("seconds", 120)))
    await note("slow:done")
    return {"ok": True}


@tasks.task("tick")
async def tick(task: Task, payload: dict) -> dict:
    await note("tick")
    return {"ok": True}


async def main():
    await Worker(DSN, tasks, concurrency=2).run()


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
