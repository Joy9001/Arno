"""Background work as the app sees it: the activity poll, retry, and an import's own state."""

from __future__ import annotations

from datetime import datetime

from fin_agent.importers.pipeline import import_payload
from fin_agent.notifications import create_notification

JOB_VIEW_KEYS = {
    "id",
    "key",
    "kind",
    "status",
    "attempts",
    "attempt",
    "max_attempts",
    "run_at",
    "created_at",
    "started_at",
    "finished_at",
    "last_error",
    "result",
    "subject",
    "import_id",
}


async def _job(conn, key: str) -> dict:
    return await conn.fetchrow("SELECT * FROM jobs WHERE key = $1", key)


async def _committed_import(conn, import_id: str = "imp_activity") -> str:
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, institution, period_start, period_end,
                             committed_at)
        VALUES ($1, $2, 'committed', 'HDFC Bank', '2026-10-01', '2026-10-31', now())
        """,
        import_id,
        f"hash-{import_id}",
    )
    return import_id


async def _insert_job(
    conn,
    *,
    key: str,
    kind: str,
    status: str,
    import_id: str | None = None,
    attempts: int = 0,
    last_error: str | None = None,
    result: dict | None = None,
    extra: dict | None = None,
    finished: bool = False,
) -> dict:
    payload = {"import_id": import_id} if import_id else {}
    payload.update(extra or {})
    row = await conn.fetchrow(
        """
        INSERT INTO jobs (key, kind, payload, status, attempts, last_error, result,
                          started_at, finished_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7,
                CASE WHEN $4 IN ('running', 'done', 'failed') THEN now() END,
                CASE WHEN $8 THEN now() END)
        RETURNING *
        """,
        key,
        kind,
        payload,
        status,
        attempts,
        last_error,
        result,
        finished,
    )
    return row


# The activity poll ------------------------------------------------------------------------


async def test_activity_without_since_lists_only_active_work(authed_client, db):
    await db.execute("DELETE FROM jobs")
    await _insert_job(db, key="p:1", kind="x", status="pending")
    await _insert_job(db, key="r:1", kind="x", status="running")
    await _insert_job(db, key="d:1", kind="x", status="done", finished=True)

    body = authed_client.get("/api/jobs/activity").json()

    assert [job["key"] for job in body["active"]] == ["r:1", "p:1"]
    assert body["finished"] == []
    assert datetime.fromisoformat(body["now"])
    assert set(body["active"][0]) == JOB_VIEW_KEYS


async def test_activity_with_since_returns_only_later_finishes(authed_client, db):
    await db.execute("DELETE FROM jobs")
    await _insert_job(db, key="old:1", kind="x", status="done", finished=True)
    await db.execute("UPDATE jobs SET finished_at = now() - interval '1 hour' WHERE key = 'old:1'")
    await _insert_job(db, key="new:1", kind="x", status="failed", finished=True)

    since = await db.fetchval("SELECT now() - interval '30 minutes'")
    body = authed_client.get("/api/jobs/activity", params={"since": since.isoformat()}).json()

    assert [job["key"] for job in body["finished"]] == ["new:1"]


async def test_activity_is_a_pure_read(authed_client, db):
    before = await db.fetchval("SELECT count(*) FROM job_schedules")
    assert authed_client.get("/api/jobs/activity").status_code == 200
    assert await db.fetchval("SELECT count(*) FROM job_schedules") == before
    bad = authed_client.get("/api/jobs/activity", params={"since": "not-a-date"})
    assert bad.status_code == 422


def test_activity_needs_a_session(client):
    assert client.get("/api/jobs/activity").status_code == 401


# Retry ------------------------------------------------------------------------------------


async def test_retry_resets_a_failed_job_and_clears_its_alert(authed_client, db):
    job = await _insert_job(
        db,
        key="bad:1",
        kind="x",
        status="failed",
        attempts=3,
        last_error="RuntimeError: nope",
        result={"status": "done"},
        finished=True,
    )
    await db.execute("UPDATE jobs SET timed_out_at = now() WHERE id = $1", job["id"])
    notice = await create_notification(
        db,
        kind="job_failed",
        title="x failed",
        data={"job_id": job["id"]},
        dedupe_key=f"job_failed:{job['id']}",
    )

    retried = authed_client.post(f"/api/jobs/{job['id']}/retry")

    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["status"] == "pending"
    assert body["attempts"] == 0 and body["attempt"] == 1
    assert body["last_error"] is None and body["result"] is None
    assert body["started_at"] is None and body["finished_at"] is None
    row = await _job(db, "bad:1")
    assert row["timed_out_at"] is None
    assert await db.fetchval("SELECT read_at FROM notifications WHERE id = $1", notice["id"])


async def test_retry_refuses_a_done_job_and_an_unknown_one(authed_client, db):
    done = await _insert_job(db, key="done:1", kind="x", status="done", finished=True)

    assert authed_client.post(f"/api/jobs/{done['id']}/retry").status_code == 409
    assert authed_client.post("/api/jobs/job_missing/retry").status_code == 404


async def test_job_status_recent_rows_carry_the_job_view_and_a_subject(authed_client, db):
    import_id = await _committed_import(db)
    await _insert_job(
        db,
        key="categorize_import:imp_activity",
        kind="categorize_import",
        status="pending",
        import_id=import_id,
    )

    body = authed_client.get("/api/jobs").json()

    recent = body["recent"][0]
    assert set(recent) == JOB_VIEW_KEYS
    assert recent["subject"] == "HDFC Bank statement, Oct 2026"
    assert recent["import_id"] == import_id


# An import's own categorize state ---------------------------------------------------------


async def test_categorize_state_is_null_before_commit(db):
    await db.execute(
        "INSERT INTO imports (id, file_hash, status) VALUES ('imp_open', 'h', 'review')"
    )
    assert (await import_payload(db, "imp_open"))["categorize"] is None


async def test_categorize_state_queued_and_retrying(db):
    import_id = await _committed_import(db, "imp_queued")
    row = await _insert_job(
        db,
        key="categorize_import:imp_queued",
        kind="categorize_import",
        status="pending",
        import_id=import_id,
    )
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "queued"
    assert state["attempt"] == 1 and state["max_attempts"] == 3
    assert state["next_run_at"] == row["run_at"].isoformat()
    assert state["job_id"] == row["id"]

    await db.execute(
        "UPDATE jobs SET attempts = 1, last_error = 'RuntimeError: nope' WHERE id = $1",
        row["id"],
    )
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "retrying"
    assert state["attempt"] == 2
    assert state["error"] == "RuntimeError: nope"


async def test_categorize_state_running_and_failed(db):
    import_id = await _committed_import(db, "imp_running")
    row = await _insert_job(
        db,
        key="categorize_import:imp_running",
        kind="categorize_import",
        status="running",
        import_id=import_id,
    )
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "running"
    assert state["started_at"] is not None

    await db.execute(
        "UPDATE jobs SET status = 'failed', attempts = 3, last_error = 'boom' WHERE id = $1",
        row["id"],
    )
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "failed"
    assert state["attempt"] == 3
    assert state["error"] == "boom"


async def test_categorize_state_done_partial_and_legacy(db):
    import_id = await _committed_import(db, "imp_done")
    row = await _insert_job(
        db,
        key="categorize_import:imp_done",
        kind="categorize_import",
        status="done",
        import_id=import_id,
        finished=True,
        result={
            "status": "done",
            "run_id": "run_1",
            "labeled": 5,
            "uncategorized": 1,
            "failed_payees": 0,
        },
    )
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "done"
    assert (state["labeled"], state["uncategorized"], state["failed_payees"]) == (5, 1, 0)

    await db.execute(
        "UPDATE jobs SET result = $1 WHERE id = $2",
        {
            "status": "partial",
            "run_id": "run_1",
            "labeled": 3,
            "uncategorized": 3,
            "failed_payees": 2,
        },
        row["id"],
    )
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "partial"
    assert state["failed_payees"] == 2

    # A row from before `jobs.result`: the run row still carries the counts.
    await db.execute("UPDATE jobs SET result = NULL WHERE id = $1", row["id"])
    await db.execute(
        """
        INSERT INTO categorize_runs (id, import_id, mode, labeled, uncategorized, finished_at)
        VALUES ('cat_old', $1, 'now', 4, 2, now())
        """,
        import_id,
    )
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "done"
    assert (state["labeled"], state["uncategorized"]) == (4, 2)
    assert state["finished_at"] is not None


async def test_categorize_state_waits_on_a_batch(db):
    import_id = await _committed_import(db, "imp_batch")
    pending = await _insert_job(
        db,
        key="collect_batch:cat_run:1",
        kind="collect_batch",
        status="pending",
        import_id=import_id,
        extra={"run_id": "cat_run"},
    )
    await _insert_job(
        db,
        key="categorize_import:imp_batch",
        kind="categorize_import",
        status="done",
        import_id=import_id,
        finished=True,
        result={"status": "batch_submitted", "batch_id": "cat_run", "run_id": "cat_run"},
    )

    # The finished run points at the collector it queued.
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "waiting_on_batch"
    assert state["next_run_at"] == pending["run_at"].isoformat()

    # Once the collector is the newest job, it says the same thing.
    await db.execute("UPDATE jobs SET created_at = now() WHERE key = 'collect_batch:cat_run:1'")
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "waiting_on_batch"
    assert state["job_id"] == pending["id"]

    await db.execute(
        "UPDATE jobs SET status = 'failed', last_error = 'dead' WHERE id = $1", pending["id"]
    )
    state = (await import_payload(db, import_id))["categorize"]
    assert state["state"] == "failed"
    assert state["error"] == "dead"


async def test_a_retried_job_that_fails_again_raises_a_fresh_alert(authed_client, db):
    from fin_agent.jobs.runner import JobGaveUp, JobRunner

    def handler(payload):
        del payload
        raise JobGaveUp("Stopped at the daily spend ceiling.")

    runner = JobRunner(registry={"ceiling": handler}, timeouts={"ceiling": 5})
    job = await db.fetchrow(
        "INSERT INTO jobs (key, kind) VALUES ('again:1', 'ceiling') RETURNING id"
    )

    await runner.run_once()
    assert authed_client.post(f"/api/jobs/{job['id']}/retry").status_code == 200
    await runner.run_once()

    alerts = await db.fetch(
        "SELECT read_at FROM notifications WHERE kind = 'job_failed' ORDER BY created_at, id"
    )
    assert len(alerts) == 2
    assert alerts[0]["read_at"] is not None and alerts[1]["read_at"] is None
