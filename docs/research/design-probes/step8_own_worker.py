"""A plain jobs table and one loop: the prototype killed and restarted by step8_probe4.py."""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import psycopg

DSN = os.environ["DSN"]
EVERY = timedelta(seconds=5)


async def note(c, kind):
    await c.execute("INSERT INTO attempts(kind, at) VALUES (%s, now())", (kind,))


async def slow(c, job):
    await note(c, f"own_slow:attempt{job['attempts']}")
    await asyncio.sleep(float(job["payload"].get("seconds", 120)))
    await note(c, "own_slow:done")


async def tick(c, job):
    await note(c, f"own_tick:{job['key']}")


async def boom(c, job):
    await note(c, f"own_boom:attempt{job['attempts']}")
    raise RuntimeError("LLM down")


HANDLERS = {"slow": slow, "tick": tick, "boom": boom}


async def materialize(c):
    """A due schedule creates one job and moves on from now, so any downtime collapses to one run."""
    cur = await c.execute("SELECT kind, next_run_at FROM job_schedules WHERE next_run_at <= now() FOR UPDATE SKIP LOCKED")
    for kind, due in await cur.fetchall():
        await c.execute("INSERT INTO jobs(key, kind) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (f"{kind}:{due.isoformat()}", kind))
        await c.execute("UPDATE job_schedules SET next_run_at = now() + %s WHERE kind = %s", (EVERY, kind))


async def claim(c):
    cur = await c.execute(
        """UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = now()
           WHERE id = (SELECT id FROM jobs WHERE status = 'pending' AND run_at <= now()
                       ORDER BY run_at LIMIT 1 FOR UPDATE SKIP LOCKED)
           RETURNING id, key, kind, payload, attempts, max_attempts"""
    )
    row = await cur.fetchone()
    return dict(zip(("id", "key", "kind", "payload", "attempts", "max_attempts"), row)) if row else None


async def run_one(c, job):
    try:
        await HANDLERS[job["kind"]](c, job)
        await c.execute("UPDATE jobs SET status = 'done', finished_at = now() WHERE id = %s", (job["id"],))
    except Exception as e:  # noqa: BLE001
        if job["attempts"] >= job["max_attempts"]:
            await c.execute("UPDATE jobs SET status = 'failed', last_error = %s, finished_at = now() WHERE id = %s", (repr(e), job["id"]))
        else:
            await c.execute("UPDATE jobs SET status = 'pending', last_error = %s, run_at = now() + (2 ^ %s) * interval '1 second' WHERE id = %s", (repr(e), job["attempts"], job["id"]))


async def main():
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as c:
        # One process: anything marked running at boot was running when the process died.
        await c.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")
        await note(c, "own:boot")
        while True:
            async with c.transaction():
                await materialize(c)
            job = await claim(c)
            if job:
                asyncio.create_task(run_one(await psycopg.AsyncConnection.connect(DSN, autocommit=True), job))
            else:
                await asyncio.sleep(0.5)


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
