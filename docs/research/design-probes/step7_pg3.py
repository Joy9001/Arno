"""Step 7: run_query's recipe on psycopg 3 against a throwaway Postgres, as agent_ro. Synthetic tables only."""
import asyncio
import sys
import time

import psycopg

DSN = "host=localhost port=15433 dbname=postgres user=agent_ro"
TIMEOUT = 3.0


async def run_query(sql):
    """One statement, one read-only transaction always rolled back, and a client-side deadline."""
    conn = await psycopg.AsyncConnection.connect(DSN, autocommit=False)
    try:
        await conn.set_read_only(True)

        async def body():
            await conn.execute("SET LOCAL statement_timeout = '2s'")
            cur = await conn.execute(sql, prepare=True)
            rows = await cur.fetchmany(1000) if cur.description else []
            await conn.rollback()
            return rows

        return await asyncio.wait_for(body(), TIMEOUT)
    finally:
        await conn.close()


async def main():
    cases = {
        "plain read": "SELECT count(*) FROM movements",
        "two statements": "SELECT 1; SELECT 2",
        "turn the timeout off, then sleep": "SET statement_timeout = 0; SELECT pg_sleep(4)",
        "set_config off and sleep in one SELECT": "SELECT set_config('statement_timeout', '0', true), pg_sleep(4)",
        "write": "INSERT INTO movements VALUES (2, 1)",
        "write inside a CTE": "WITH x AS (INSERT INTO movements VALUES (3, 1) RETURNING id) SELECT * FROM x",
        "read-write first, as the one statement": "SET TRANSACTION READ WRITE",
        "auth table": "SELECT * FROM auth_sessions",
        "server file": "SELECT pg_read_file('postgresql.conf')",
        "COPY to a program": "COPY movements TO PROGRAM 'whoami'",
        "a long self-join": "SELECT count(*) FROM generate_series(1, 100000) a, generate_series(1, 100000) b",
    }
    for name, sql in cases.items():
        t = time.perf_counter()
        try:
            rows = await run_query(sql)
            print(f"{name:42} ok {rows!r:.40} ({time.perf_counter() - t:.1f}s)")
        except (psycopg.Error, asyncio.TimeoutError) as e:
            print(f"{name:42} {type(e).__name__}: {str(e).splitlines()[0][:70] if str(e) else ''} ({time.perf_counter() - t:.1f}s)")
    check = await psycopg.AsyncConnection.connect(DSN, autocommit=True)
    cur = await check.execute("SELECT count(*) FROM movements")
    print("rows in movements afterwards:", await cur.fetchone())
    cur = await check.execute("SHOW statement_timeout")
    print("a fresh connection's timeout:", await cur.fetchone())
    await check.close()


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
