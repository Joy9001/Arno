"""Step 9 probe 1, real Postgres, no LLM: pydantic_evals with repeat, a cloned database per trial, grading on database state."""
import asyncio
import contextvars
import os
import statistics
import time
import uuid
import warnings
from collections import defaultdict
from dataclasses import dataclass

import psycopg
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.lifecycle import CaseLifecycle

warnings.filterwarnings("ignore")
ADMIN = os.environ["DSN"]
BASE = ADMIN.rsplit("/", 1)[0]
current_db = contextvars.ContextVar("current_db")
clone_times, drop_times, seen_dbs, attempts = [], [], [], defaultdict(int)


async def admin(sql):
    async with await psycopg.AsyncConnection.connect(ADMIN, autocommit=True) as c:
        await c.execute(sql)


class TrialDB(CaseLifecycle):
    """Each trial gets its own clone of the demo template."""

    async def setup(self):
        self.db = "trial_" + uuid.uuid4().hex[:10]
        start = time.perf_counter()
        await admin(f"CREATE DATABASE {self.db} TEMPLATE demo")
        clone_times.append(time.perf_counter() - start)
        current_db.set(self.db)
        seen_dbs.append(self.db)

    async def teardown(self, result):
        start = time.perf_counter()
        await admin(f"DROP DATABASE {self.db} WITH (FORCE)")
        drop_times.append(time.perf_counter() - start)


def scripted(name):
    async def stream(messages, info):
        done = any(isinstance(p, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for p in m.parts)
        if done:
            yield "Logged."
            return
        attempts[name] += 1
        # A flaky model: the 'flaky' case logs the wrong amount on its second trial.
        amount = 999 if name == "flaky" and attempts[name] == 2 else 450
        if name == "says_only":
            yield "Logged ₹450 for chai."  # claims success, writes nothing
            return
        yield {0: DeltaToolCall(name="record", json_args=f'{{"amount": {amount}}}', tool_call_id=f"c{uuid.uuid4().hex[:6]}")}

    return stream


def make_agent(case_name):
    agent = Agent(FunctionModel(stream_function=scripted(case_name)))

    @agent.tool_plain
    async def record(amount: int) -> str:
        """Writes a movement."""
        async with await psycopg.AsyncConnection.connect(f"{BASE}/{current_db.get()}", autocommit=True) as c:
            await c.execute("INSERT INTO movements(amount_paise, note) VALUES (%s, 'chai')", (amount * 100,))
        return "saved"

    return agent


async def task(inputs: dict) -> str:
    async with make_agent(inputs["name"]).run_stream(inputs["text"]) as r:
        return await r.get_output()


@dataclass
class RowExists(Evaluator):
    """Grades the database, not what the agent said."""

    amount_paise: int

    async def evaluate(self, ctx: EvaluatorContext) -> bool:
        async with await psycopg.AsyncConnection.connect(f"{BASE}/{current_db.get()}") as c:
            cur = await c.execute("SELECT count(*) FROM movements WHERE note = 'chai' AND amount_paise = %s", (self.amount_paise,))
            return (await cur.fetchone())[0] == 1


def pass_power(report, k):
    groups = defaultdict(list)
    for case in report.cases:
        base = getattr(case, "source_case_name", None) or case.name.split(" [")[0]
        groups[base].append(all(a.value for a in case.assertions.values()))
    for failure in report.failures:
        base = getattr(failure, "source_case_name", None) or failure.name.split(" [")[0]
        groups[base].append(False)
    return {name: (sum(v), len(v), all(v) and len(v) == k) for name, v in sorted(groups.items())}


async def main():
    await admin("DROP DATABASE IF EXISTS demo")
    await admin("CREATE DATABASE demo")
    async with await psycopg.AsyncConnection.connect(f"{BASE}/demo", autocommit=True) as c:
        await c.execute("CREATE TABLE movements(id bigserial primary key, amount_paise bigint, note text, at date default now())")
        await c.execute("INSERT INTO movements(amount_paise, note) SELECT (random()*100000)::bigint, 'seed ' || i FROM generate_series(1, 60000) i")
        await c.execute("CREATE INDEX ON movements(at)")
        cur = await c.execute("SELECT pg_size_pretty(pg_database_size('demo'))")
        print("demo template size, 60,000 rows:", (await cur.fetchone())[0])
    await admin("ALTER DATABASE demo IS_TEMPLATE true")

    dataset = Dataset(
        name="writes",
        cases=[
            Case(name="logs_chai", inputs={"name": "steady", "text": "chai 450"}, evaluators=(RowExists(45000),)),
            Case(name="flaky_model", inputs={"name": "flaky", "text": "chai 450"}, evaluators=(RowExists(45000),)),
            Case(name="claims_without_writing", inputs={"name": "says_only", "text": "chai 450"}, evaluators=(RowExists(45000),)),
        ]
    )
    start = time.perf_counter()
    report = await dataset.evaluate(task, repeat=3, max_concurrency=4, lifecycle=TrialDB, progress=False, task_timeout=30)
    print("wall time:", round(time.perf_counter() - start, 2), "s | trials:", len(report.cases), "| failures:", len(report.failures))
    print("names:", [c.name for c in report.cases][:4])
    print("pass^3 (passed, trials, verdict):", pass_power(report, 3))
    print("databases used:", len(set(seen_dbs)), "of", len(seen_dbs), "| clone median:", round(statistics.median(clone_times) * 1000), "ms | max:", round(max(clone_times) * 1000), "ms | drop median:", round(statistics.median(drop_times) * 1000), "ms")
    async with await psycopg.AsyncConnection.connect(ADMIN) as c:
        cur = await c.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'trial_%'")
        print("clones left behind:", (await cur.fetchone())[0])
    print("template untouched, chai rows:", end=" ")
    async with await psycopg.AsyncConnection.connect(f"{BASE}/demo") as c:
        cur = await c.execute("SELECT count(*) FROM movements WHERE note = 'chai'")
        print((await cur.fetchone())[0])
    print(report.render(include_input=False, include_output=False, include_durations=False)[:900])


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
