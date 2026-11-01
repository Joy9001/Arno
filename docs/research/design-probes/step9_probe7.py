"""Step 9 probe 7, real Postgres, no LLM: the daily spend ceiling through PostgresSpendStore, across a restart."""
import asyncio
import os
import warnings
from decimal import Decimal

import asyncpg
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import SpendLimits
from pydantic_ai_harness.spend import Budget, PostgresSpendStore

warnings.filterwarnings("ignore")


async def answer(messages, info):
    yield "ok"


async def turn(limits, label):
    agent = Agent(FunctionModel(stream_function=answer), capabilities=[limits])
    try:
        async with agent.run_stream("hi") as r:
            await r.get_output()
        print(f"   {label}: answered")
    except Exception as e:  # noqa: BLE001
        print(f"   {label}: stopped by {type(e).__name__}: {str(e)[:90]}")


async def main():
    dsn = os.environ["DSN"]
    print("== A process with a $1 daily ceiling, each response priced at $0.40")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    limits = SpendLimits(budgets=[Budget(usd=Decimal("1"), window="day")], store=PostgresSpendStore(pool), price=lambda *a, **k: Decimal("0.40"))
    for i in range(4):
        await turn(limits, f"turn {i + 1}")
    await pool.close()

    print("== A new pool and a new store: what a restart leaves behind")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    limits = SpendLimits(budgets=[Budget(usd=Decimal("1"), window="day")], store=PostgresSpendStore(pool), price=lambda *a, **k: Decimal("0.40"))
    await turn(limits, "turn after the restart")
    rows = await pool.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    print("   tables it created:", [r["tablename"] for r in rows])
    await pool.close()

    print("== The default store, for comparison, across a restart")
    limits = SpendLimits(budgets=[Budget(usd=Decimal("1"), window="day")], price=lambda *a, **k: Decimal("0.40"))
    for i in range(4):
        await turn(limits, f"turn {i + 1}")
    limits = SpendLimits(budgets=[Budget(usd=Decimal("1"), window="day")], price=lambda *a, **k: Decimal("0.40"))
    await turn(limits, "turn after the restart")


asyncio.run(main())
