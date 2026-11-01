"""Step 9 probe 8, real Postgres, no LLM: the spend store while the shell stops and restarts Postgres, on a pool and on fresh connections."""
import asyncio
import os
import sys
import time
import warnings
from contextlib import asynccontextmanager
from decimal import Decimal

import asyncpg
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import SpendLimits
from pydantic_ai_harness.spend import Budget, PostgresSpendStore

warnings.filterwarnings("ignore")
DSN = os.environ["DSN"]


class FreshConnections:
    """Satisfies the store's pool protocol by opening one connection per use."""

    @asynccontextmanager
    async def acquire(self):
        conn = await asyncio.wait_for(asyncpg.connect(DSN), 3)
        try:
            yield conn
        finally:
            await conn.close()


async def answer(messages, info):
    yield "ok"


async def main():
    kind = sys.argv[1]
    pool = FreshConnections() if kind == "fresh" else await asyncpg.create_pool(DSN, min_size=1, max_size=2, timeout=3)
    limits = SpendLimits(budgets=[Budget(usd=Decimal("1000"), window="day")], store=PostgresSpendStore(pool), price=lambda *a, **k: Decimal("0.01"))
    agent = Agent(FunctionModel(stream_function=answer), capabilities=[limits])
    start, last = time.perf_counter(), None
    while time.perf_counter() - start < 24:
        try:
            async with agent.run_stream("hi") as r:
                await asyncio.wait_for(r.get_output(), 4)
            state = "ok"
        except BaseException as e:  # noqa: BLE001
            state = type(e).__name__
        if state != last:
            print(f"{kind}: {time.perf_counter() - start:5.1f}s {state}", flush=True)
            last = state
        await asyncio.sleep(0.5)


asyncio.run(main())
