"""The jobs table and its one loop."""

from __future__ import annotations

import time
from datetime import datetime

from fin_agent.jobs.runner import (
    JobRunner,
    enqueue_job,
    recover_interrupted_jobs,
)


async def _job(conn, key: str) -> dict:
    return await conn.fetchrow("SELECT * FROM jobs WHERE key = $1", key)


async def test_enqueue_is_deduplicated_by_key_and_same_transaction_as_data(db):
    conn = db
    await conn.execute("INSERT INTO notifications (kind, title) VALUES ('test', 'before')")
    import_id = "imp_test"
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status)
        VALUES ($1, 'hash', 'parsing')
        """,
        import_id,
    )
    first = await enqueue_job(
        conn, key="import:imp_test", kind="import_statement", payload={"x": 1}
    )
    second = await enqueue_job(
        conn, key="import:imp_test", kind="import_statement", payload={"x": 2}
    )
    assert first == second
    assert (await _job(conn, "import:imp_test"))["payload"]["x"] == 1

    tx = conn.transaction()
    await tx.start()
    await conn.execute(
        "INSERT INTO imports (id, file_hash, status) VALUES ('imp_roll', 'h2', 'parsing')"
    )
    await enqueue_job(conn, key="import:imp_roll", kind="import_statement")
    await tx.rollback()
    assert await conn.fetchval("SELECT count(*) FROM imports WHERE id = 'imp_roll'") == 0
    assert await _job(conn, "import:imp_roll") is None


async def test_boot_recovery_requeues_running_jobs(db):
    await db.execute(
        """
        INSERT INTO jobs (key, kind, status, started_at)
        VALUES ('a', 'recompute_baselines', 'running', now()),
               ('b', 'recompute_baselines', 'pending', NULL)
        """
    )
    assert await recover_interrupted_jobs(db) == 1
    assert (await db.fetchrow("SELECT status FROM jobs WHERE key = 'a'"))["status"] == "pending"


async def test_a_failing_job_retries_three_times_then_fails_and_notifies(db):
    attempts = {"n": 0}

    def failing(payload):
        attempts["n"] += 1
        raise RuntimeError("nope")

    runner = JobRunner(registry={"bad": failing}, timeouts={"bad": 5})
    enqueue = await db.fetchrow("INSERT INTO jobs (key, kind) VALUES ('bad:1', 'bad') RETURNING id")

    await runner.run_once()
    row = await _job(db, "bad:1")
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["run_at"] > datetime.now(row["run_at"].tzinfo)
    await db.execute("UPDATE jobs SET run_at = now() WHERE id = $1", enqueue["id"])

    await runner.run_once()
    await db.execute("UPDATE jobs SET run_at = now() WHERE id = $1", enqueue["id"])
    await runner.run_once()

    row = await _job(db, "bad:1")
    assert row["status"] == "failed" and row["attempts"] == 3
    assert row["last_error"].startswith("RuntimeError")
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'job_failed'") == 1


async def test_a_timeout_is_a_failure_with_a_marker(db):
    def slow(payload):
        del payload
        time.sleep(0.3)

    runner = JobRunner(registry={"slow": slow}, timeouts={"slow": 0.05})
    await db.execute("INSERT INTO jobs (key, kind) VALUES ('slow:1', 'slow')")

    await runner.run_once()
    row = await _job(db, "slow:1")
    assert row["status"] == "pending"
    assert row["timed_out_at"] is not None
    assert "timed out" in row["last_error"]


async def test_a_job_with_no_timeout_runs_to_completion(db):
    import asyncio

    async def slow(payload):
        del payload
        await asyncio.sleep(0.2)

    runner = JobRunner(registry={"slow": slow}, timeouts={"slow": None})
    await db.execute("INSERT INTO jobs (key, kind) VALUES ('slow:none', 'slow')")

    await runner.run_once()
    row = await _job(db, "slow:none")
    assert row["status"] == "done"
    assert row["timed_out_at"] is None


async def test_the_loop_claims_at_most_two_jobs_at_once(db):
    started: list[str] = []

    async def handler(payload):
        started.append(payload["n"])
        import asyncio

        await asyncio.sleep(0.1)

    runner = JobRunner(
        registry={"sleepy": handler},
        concurrency=2,
        poll_seconds=10,
        timeouts={"sleepy": 5},
    )
    for index in range(3):
        await db.execute(
            "INSERT INTO jobs (key, kind, payload) VALUES ($1, 'sleepy', $2)",
            f"sleepy:{index}",
            {"n": index},
        )

    import asyncio

    claimed = await runner.tick()
    assert claimed == 2
    assert len(runner._running) == 2
    await asyncio.gather(*list(runner._running))
    assert len(started) == 2
    assert await runner.tick() == 1
    await asyncio.gather(*list(runner._running))
    assert sorted(started) == [0, 1, 2]
