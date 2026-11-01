"""Step 8 probe 4, real Postgres: pgtask and a plain jobs table under a killed worker and a missed schedule."""
import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from pgtask import Client, TaskRegistry

HERE = Path(__file__).parent
DSN = os.environ["DSN"]
ENV = {**os.environ, "DSN": DSN}
tasks = TaskRegistry(queue_name="jobs")


@tasks.task("slow")
async def slow(task, payload):
    return {}


@tasks.task("tick")
async def tick(task, payload):
    return {}


def spawn(script):
    return subprocess.Popen([sys.executable, str(HERE / script)], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def rows(c, prefix):
    cur = await c.execute("SELECT kind FROM attempts WHERE kind LIKE %s ORDER BY at", (prefix + "%",))
    return [r[0] for r in await cur.fetchall()]


async def wait_for(c, prefix, count, timeout):
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if len(await rows(c, prefix)) >= count:
            return round(time.perf_counter() - start, 1)
        await asyncio.sleep(0.25)
    return None


async def main():
    c = await psycopg.AsyncConnection.connect(DSN, autocommit=True)
    await c.execute("CREATE TABLE IF NOT EXISTS attempts(kind text, at timestamptz)")
    client = await Client.connect(DSN)
    await client.migrate()

    print("== pgtask footprint")
    cur = await c.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'pgtask'")
    print("   tables:", (await cur.fetchone())[0], end=" | ")
    cur = await c.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'pgtask'")
    print("functions:", (await cur.fetchone())[0], end=" | ")
    cur = await c.execute("SELECT pg_get_function_identity_arguments(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'pgtask' AND p.proname = 'put_schedule'")
    sig = await cur.fetchone()
    print("put_schedule:", sig[0][:420] if sig else None)
    cur = await c.execute("SELECT column_name, column_default FROM information_schema.columns WHERE table_schema = 'pgtask' AND table_name = 'schedules' ORDER BY ordinal_position")
    print("   schedules columns:", [r[0] for r in await cur.fetchall()])
    cur = await c.execute("SELECT column_name, column_default FROM information_schema.columns WHERE table_schema = 'pgtask' AND table_name = 'queues' ORDER BY ordinal_position")
    print("   queue defaults:", [(r[0], r[1]) for r in await cur.fetchall() if r[1] and ("lease" in r[0] or "attempt" in r[0] or "retry" in r[0])])

    print("== pgtask: idempotency and transactional enqueue")
    a = await client.enqueue(slow.request({"seconds": 60}, idempotency_key="job:1"))
    b = await client.enqueue(slow.request({"seconds": 60}, idempotency_key="job:1"))
    print("   same key, same task:", a.task_id if hasattr(a, "task_id") else a, "==", (b.task_id if hasattr(b, "task_id") else b))
    conn = await psycopg.AsyncConnection.connect(DSN)
    try:
        async with conn.transaction():
            await Client.enqueue_on(conn, tick.request({}, idempotency_key="rolled-back"))
            raise ValueError("roll back")
    except ValueError:
        pass
    await conn.close()
    cur = await c.execute("SELECT count(*) FROM pgtask.tasks WHERE name = 'tick'")
    print("   rolled-back task visible:", (await cur.fetchone())[0] > 0)

    print("== pgtask: the worker is killed mid-task")
    w = spawn("step8_pgtask_worker.py")
    t = await wait_for(c, "slow:attempt", 1, 30)
    print("   first attempt started after:", t)
    await asyncio.sleep(1)
    w.kill()
    w.wait()
    w = spawn("step8_pgtask_worker.py")
    t = await wait_for(c, "slow:attempt", 2, 200)
    print("   after kill and an immediate restart, the second attempt started after:", t, "seconds |", await rows(c, "slow:"))
    w.kill()
    w.wait()

    print("== plain table: the same")
    await c.execute(
        """CREATE TABLE jobs(id bigserial primary key, key text unique, kind text not null, payload jsonb not null default '{}',
           status text not null default 'pending', run_at timestamptz not null default now(), attempts int not null default 0,
           max_attempts int not null default 3, last_error text, started_at timestamptz, finished_at timestamptz)"""
    )
    await c.execute("CREATE TABLE job_schedules(kind text primary key, next_run_at timestamptz not null)")
    await c.execute("INSERT INTO jobs(key, kind, payload) VALUES ('s1', 'slow', '{\"seconds\": 60}') ON CONFLICT DO NOTHING")
    cur = await c.execute("INSERT INTO jobs(key, kind) VALUES ('s1', 'slow') ON CONFLICT DO NOTHING RETURNING id")
    print("   same key inserted again:", await cur.fetchone())
    w = spawn("step8_own_worker.py")
    t = await wait_for(c, "own_slow:attempt", 1, 30)
    print("   first attempt started after:", t)
    await asyncio.sleep(1)
    w.kill()
    w.wait()
    w = spawn("step8_own_worker.py")
    t = await wait_for(c, "own_slow:attempt", 2, 60)
    print("   after kill and an immediate restart, the second attempt started after:", t, "seconds |", await rows(c, "own_slow:"))

    print("== plain table: a failing job retries with backoff, then stops")
    await c.execute("INSERT INTO jobs(key, kind) VALUES ('b1', 'boom')")
    t = await wait_for(c, "own_boom", 3, 30)
    await asyncio.sleep(1)
    cur = await c.execute("SELECT status, attempts, last_error FROM jobs WHERE key = 'b1'")
    print("   three attempts after:", t, "seconds |", await cur.fetchone())

    print("== plain table: a schedule every 5 s, the worker down for 22 s")
    await c.execute("UPDATE jobs SET status = 'done' WHERE key = 's1'")
    await c.execute("INSERT INTO job_schedules VALUES ('tick', now())")
    await asyncio.sleep(6)
    w.kill()
    w.wait()
    before = len(await rows(c, "own_tick"))
    await asyncio.sleep(22)
    w = spawn("step8_own_worker.py")
    await wait_for(c, "own:boot", 3, 15)
    await asyncio.sleep(1.5)
    after = len(await rows(c, "own_tick"))
    print(f"   ticks before: {before} | ticks within 1.5 s of the restart: {after - before} (about 4 were missed)")
    w.kill()
    w.wait()

    print("== pgtask: the same schedule with misfire policy latest")
    if sig:
        print("   (signature above; see the next run)")
    await c.close()


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
