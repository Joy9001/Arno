"""Step 8 probe 5, real Postgres: a plain jobs table under a killed worker, failures, and a missed schedule."""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).parent
DSN = os.environ["DSN"]
ENV = {**os.environ, "DSN": DSN}


def spawn():
    return subprocess.Popen([sys.executable, str(HERE / "step8_own_worker.py")], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def rows(c, prefix):
    cur = await c.execute("SELECT kind FROM attempts WHERE kind LIKE %s ORDER BY at", (prefix + "%",))
    return [r[0] for r in await cur.fetchall()]


async def wait_for(c, prefix, count, timeout):
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if len(await rows(c, prefix)) >= count:
            return round(time.perf_counter() - start, 1)
        await asyncio.sleep(0.1)
    return None


async def main():
    c = await psycopg.AsyncConnection.connect(DSN, autocommit=True)
    await c.execute("CREATE TABLE IF NOT EXISTS attempts(kind text, at timestamptz)")
    await c.execute("DROP TABLE IF EXISTS jobs, job_schedules")
    await c.execute(
        """CREATE TABLE jobs(id bigserial primary key, key text unique, kind text not null, payload jsonb not null default '{}',
           status text not null default 'pending', run_at timestamptz not null default now(), attempts int not null default 0,
           max_attempts int not null default 3, last_error text, started_at timestamptz, finished_at timestamptz)"""
    )
    await c.execute("CREATE TABLE job_schedules(kind text primary key, next_run_at timestamptz not null)")

    print("== Same key twice, and an enqueue in a rolled-back transaction")
    await c.execute("INSERT INTO jobs(key, kind, payload) VALUES ('s1', 'slow', '{\"seconds\": 60}')")
    cur = await c.execute("INSERT INTO jobs(key, kind) VALUES ('s1', 'slow') ON CONFLICT DO NOTHING RETURNING id")
    print("   same key inserted again:", await cur.fetchone())
    try:
        async with c.transaction():
            await c.execute("INSERT INTO jobs(key, kind) VALUES ('rolled', 'tick')")
            raise ValueError
    except ValueError:
        pass
    cur = await c.execute("SELECT count(*) FROM jobs WHERE key = 'rolled'")
    print("   rolled-back job visible:", (await cur.fetchone())[0] > 0)

    print("== The worker is killed mid-job")
    w = spawn()
    print("   first attempt started after:", await wait_for(c, "own_slow:attempt", 1, 30))
    await asyncio.sleep(1)
    w.kill()
    w.wait()
    w = spawn()
    print("   second attempt, after a kill and an immediate restart:", await wait_for(c, "own_slow:attempt", 2, 60), "seconds |", await rows(c, "own_slow:"))

    print("== A failing job retries with backoff, then stops")
    await c.execute("INSERT INTO jobs(key, kind) VALUES ('b1', 'boom')")
    print("   three attempts after:", await wait_for(c, "own_boom", 3, 30), "seconds")
    await asyncio.sleep(0.5)
    cur = await c.execute("SELECT status, attempts, last_error FROM jobs WHERE key = 'b1'")
    print("   ", await cur.fetchone())

    print("== A schedule every 5 s, the worker down for 22 s")
    await c.execute("UPDATE jobs SET status = 'done' WHERE key = 's1'")
    await c.execute("INSERT INTO job_schedules VALUES ('tick', now())")
    await asyncio.sleep(6)
    w.kill()
    w.wait()
    before = len(await rows(c, "own_tick"))
    await asyncio.sleep(22)
    boots = len(await rows(c, "own:boot"))
    w = spawn()
    await wait_for(c, "own:boot", boots + 1, 15)
    await asyncio.sleep(1.5)
    after = len(await rows(c, "own_tick"))
    print(f"   ticks before: {before} | ticks within 1.5 s of the restart: {after - before} (about 4 were missed)")
    await asyncio.sleep(6)
    print("   the schedule carries on:", len(await rows(c, "own_tick")) - after >= 1)
    cur = await c.execute("SELECT count(*), count(DISTINCT key) FROM jobs WHERE kind = 'tick'")
    print("   jobs, distinct keys:", await cur.fetchone())
    w.kill()
    w.wait()
    await c.close()


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
