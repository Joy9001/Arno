"""Step 8 probe 7: a connection pool queried once a second while the shell stops and restarts Postgres."""
import asyncio
import os
import time

from psycopg_pool import AsyncConnectionPool


async def main():
    pool = AsyncConnectionPool(os.environ["DSN"], min_size=1, max_size=3, open=False, check=AsyncConnectionPool.check_connection, timeout=1)
    await pool.open(wait=True)
    start = time.perf_counter()
    last = None
    while time.perf_counter() - start < 26:
        try:
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
            state = "ok"
        except Exception as e:  # noqa: BLE001
            state = type(e).__name__
        if state != last:
            print(f"{time.perf_counter() - start:5.1f}s {state}", flush=True)
            last = state
        await asyncio.sleep(1)
    await pool.close()


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
