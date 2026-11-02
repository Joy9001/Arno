"""The jobs table and its one loop."""

from __future__ import annotations

import time
from datetime import datetime

from psycopg.types.json import Jsonb

from fin_agent.jobs.runner import (
    JobRunner,
    enqueue_job,
    recover_interrupted_jobs,
)


def _job(conn, key: str) -> dict:
    return conn.execute("SELECT * FROM jobs WHERE key = %s", (key,)).fetchone()


def test_enqueue_is_deduplicated_by_key_and_same_transaction_as_data(db):
    conn = db
    conn.execute("INSERT INTO notifications (kind, title) VALUES ('test', 'before')")
    import_id = "imp_test"
    conn.execute(
        """
        INSERT INTO imports (id, file_hash, status)
        VALUES (%s, 'hash', 'parsing')
        """,
        (import_id,),
    )
    first = enqueue_job(conn, key="import:imp_test", kind="import_statement", payload={"x": 1})
    second = enqueue_job(conn, key="import:imp_test", kind="import_statement", payload={"x": 2})
    assert first == second
    conn.commit()
    assert _job(conn, "import:imp_test")["payload"]["x"] == 1

    conn.execute("INSERT INTO imports (id, file_hash, status) VALUES ('imp_roll', 'h2', 'parsing')")
    enqueue_job(conn, key="import:imp_roll", kind="import_statement")
    conn.rollback()
    assert (
        conn.execute("SELECT count(*) AS n FROM imports WHERE id = 'imp_roll'").fetchone()["n"] == 0
    )
    assert _job(conn, "import:imp_roll") is None


def test_boot_recovery_requeues_running_jobs(db):
    db.execute(
        """
        INSERT INTO jobs (key, kind, status, started_at)
        VALUES ('a', 'recompute_baselines', 'running', now()),
               ('b', 'recompute_baselines', 'pending', NULL)
        """
    )
    db.commit()
    assert recover_interrupted_jobs(db) == 1
    db.commit()
    assert db.execute("SELECT status FROM jobs WHERE key = 'a'").fetchone()["status"] == "pending"


def test_a_failing_job_retries_three_times_then_fails_and_notifies(db):
    attempts = {"n": 0}

    def failing(payload):
        attempts["n"] += 1
        raise RuntimeError("nope")

    runner = JobRunner(registry={"bad": failing}, timeouts={"bad": 5})
    enqueue = db.execute(
        "INSERT INTO jobs (key, kind) VALUES ('bad:1', 'bad') RETURNING id"
    ).fetchone()
    db.commit()

    import asyncio

    asyncio.run(runner.run_once())
    row = _job(db, "bad:1")
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["run_at"] > datetime.now(row["run_at"].tzinfo)
    db.execute("UPDATE jobs SET run_at = now() WHERE id = %s", (enqueue["id"],))
    db.commit()

    asyncio.run(runner.run_once())
    db.execute("UPDATE jobs SET run_at = now() WHERE id = %s", (enqueue["id"],))
    db.commit()
    asyncio.run(runner.run_once())

    row = _job(db, "bad:1")
    assert row["status"] == "failed" and row["attempts"] == 3
    assert row["last_error"].startswith("RuntimeError")
    assert (
        db.execute("SELECT count(*) AS n FROM notifications WHERE kind = 'job_failed'").fetchone()[
            "n"
        ]
        == 1
    )


def test_a_timeout_is_a_failure_with_a_marker(db):
    def slow(payload):
        del payload
        time.sleep(0.3)

    runner = JobRunner(registry={"slow": slow}, timeouts={"slow": 0.05})
    db.execute("INSERT INTO jobs (key, kind) VALUES ('slow:1', 'slow')")
    db.commit()
    import asyncio

    asyncio.run(runner.run_once())
    row = _job(db, "slow:1")
    assert row["status"] == "pending"
    assert row["timed_out_at"] is not None
    assert "timed out" in row["last_error"]


def test_the_loop_claims_at_most_two_jobs_at_once(db):
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
        db.execute(
            "INSERT INTO jobs (key, kind, payload) VALUES (%s, 'sleepy', %s)",
            (f"sleepy:{index}", Jsonb({"n": index})),
        )
    db.commit()

    import asyncio
    from datetime import date as Date

    runner._last_baseline_day = Date.today()

    async def scenario():
        claimed = await runner.tick()
        assert claimed == 2
        assert len(runner._running) == 2
        await asyncio.gather(*list(runner._running))
        assert len(started) == 2
        assert await runner.tick() == 1

    asyncio.run(scenario())
    assert sorted(started) == [0, 1, 2]
