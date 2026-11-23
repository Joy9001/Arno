"""The recurring scheduler: India-time rules, one job per period, and no duplicate on restart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from arno.jobs.schedules import (
    BUILTIN_KINDS,
    INDIA,
    builtin_rules,
    enqueue_due_schedules,
    ensure_builtin_schedules,
    next_occurrence,
    period_for,
    sync_scheduled_questions,
)
from arno.scheduled_questions import create_question


async def _job_count(db, kind: str) -> int:
    return int(await db.fetchval("SELECT count(*) FROM jobs WHERE kind = $1", kind))


async def test_builtin_schedules_seed_once_and_follow_the_notification_time(db):
    await ensure_builtin_schedules(db)
    await ensure_builtin_schedules(db)
    rows = await db.fetch("SELECT * FROM job_schedules ORDER BY kind")
    assert {row["kind"] for row in rows} == set(BUILTIN_KINDS)
    assert len(rows) == len(BUILTIN_KINDS)
    briefing = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'weekly_briefing'")
    local = briefing["next_run_at"].astimezone(INDIA)
    assert local.weekday() == 6
    assert (local.hour, local.minute) == (8, 0)

    await db.execute(
        "UPDATE settings SET notification_time = '09:30', weekly_briefing_dow = 3 WHERE id = 1"
    )
    await ensure_builtin_schedules(db)
    briefing = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'weekly_briefing'")
    assert "BYDAY=WE" in briefing["rule"]
    assert "BYHOUR=9" in briefing["rule"]
    assert "BYMINUTE=30" in briefing["rule"]


async def test_a_missed_weekly_run_fires_once_and_moves_on_from_now(db):
    await ensure_builtin_schedules(db)
    old = datetime.now(INDIA) - timedelta(days=42)
    await db.execute(
        "UPDATE job_schedules SET next_run_at = $1 WHERE kind = 'weekly_briefing'", old
    )

    assert await enqueue_due_schedules(db) == 1
    jobs = await db.fetch("SELECT * FROM jobs WHERE kind = 'weekly_briefing'")
    assert len(jobs) == 1
    assert jobs[0]["key"] == f"weekly_briefing:{period_for('weekly_briefing', old)}"
    assert jobs[0]["payload"]["period"] == period_for("weekly_briefing", old)
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'weekly_briefing'")
    assert schedule["next_run_at"] > datetime.now(INDIA)
    assert schedule["last_run_at"] is not None

    # A restart that re-reads the same due row must not add a second job for that period.
    await db.execute(
        "UPDATE job_schedules SET next_run_at = $1 WHERE kind = 'weekly_briefing'", old
    )
    await enqueue_due_schedules(db)
    assert await _job_count(db, "weekly_briefing") == 1


async def test_nightly_jobs_use_their_own_period_keys(db):
    await ensure_builtin_schedules(db)
    old = datetime.now(INDIA) - timedelta(days=2)
    for kind in ("risk_check", "pattern_detection", "recompute_baselines", "backup_check"):
        await db.execute("UPDATE job_schedules SET next_run_at = $1 WHERE kind = $2", old, kind)
    assert await enqueue_due_schedules(db) == 4
    keys = {
        row["kind"]: row["key"]
        for row in await db.fetch("SELECT kind, key FROM jobs WHERE kind <> 'weekly_briefing'")
    }
    for kind in ("risk_check", "pattern_detection", "recompute_baselines", "backup_check"):
        assert keys[kind] == f"{kind}:{period_for(kind, old)}"


async def test_a_scheduled_question_is_mirrored_and_keyed_by_the_question(db):
    question = await create_question(
        db,
        prompt="What did I spend on food this week?",
        rule="FREQ=WEEKLY;BYDAY=SU",
    )
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'scheduled_question'")
    assert schedule["scheduled_question_id"] == question["id"]
    assert "scheduled_question_id" not in (schedule["payload"] or {})
    assert schedule["next_run_at"] > datetime.now(INDIA)

    old = datetime.now(INDIA) - timedelta(days=1)
    await db.execute("UPDATE job_schedules SET next_run_at = $1 WHERE id = $2", old, schedule["id"])
    await enqueue_due_schedules(db)
    job = await db.fetchrow("SELECT * FROM jobs WHERE kind = 'scheduled_question'")
    assert job["key"].startswith(f"scheduled_question:{question['id']}:")

    from arno.scheduled_questions import archive_question

    await archive_question(db, question["id"])
    await sync_scheduled_questions(db)
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'scheduled_question'")
    assert schedule["active"] is False


def test_builtin_rules_are_valid_rrules():
    rules = builtin_rules({"notification_time": "08:00", "weekly_briefing_dow": 0})
    now = datetime.now(INDIA)
    for rule in rules.values():
        assert next_occurrence(rule, after=now) is not None


async def test_cost_replay_is_not_enqueued_when_nothing_waits_to_export(db):
    await ensure_builtin_schedules(db)
    await db.execute(
        "UPDATE job_schedules SET next_run_at = now() - interval '1 minute' "
        "WHERE kind = 'cost_replay'"
    )

    assert await enqueue_due_schedules(db) == 0
    assert await _job_count(db, "cost_replay") == 0
    # The schedule still moved on, so the next check happens instead of repeating this one.
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'cost_replay'")
    assert schedule["next_run_at"] > datetime.now(INDIA)


async def test_cost_replay_gets_a_distinct_job_for_each_five_minute_occurrence(db):
    await ensure_builtin_schedules(db)
    # A finalized call the exporter has not accepted is what makes a replay job worth creating.
    await db.execute(
        """
        INSERT INTO llm_calls
            (attempt_id, trace_id, span_id, role, provider, model, billing_mode, status,
             started_at, finished_at, source, complete, price_revision)
        VALUES ('att_replay', 'trace', 'span', 'chat', 'openai', 'gpt-5', 'metered', 'finalized',
                now() - interval '10 minutes', now() - interval '6 minutes', 'provider', true, 'v1')
        """
    )
    first = datetime(2026, 11, 12, 8, 0, tzinfo=INDIA)
    for offset in (0, 5):
        at = first + timedelta(minutes=offset)
        await db.execute("UPDATE job_schedules SET next_run_at=$1 WHERE kind='cost_replay'", at)
        await enqueue_due_schedules(db, now=at + timedelta(seconds=1))
    jobs = await db.fetch("SELECT key FROM jobs WHERE kind='cost_replay'")
    assert len(jobs) == 2
    assert len({job["key"] for job in jobs}) == 2


async def test_deleting_scheduled_question_cascades_and_missing_question_id_is_rejected(db):
    import asyncpg
    import pytest

    question = await create_question(
        db,
        prompt="How much did I spend on travel?",
        rule="FREQ=WEEKLY;BYDAY=MO",
    )
    assert (
        await db.fetchval(
            "SELECT count(*) FROM job_schedules WHERE scheduled_question_id = $1",
            question["id"],
        )
        == 1
    )

    with pytest.raises(asyncpg.UniqueViolationError) as dup_exc:
        async with db.transaction():
            await db.execute(
                """
                INSERT INTO job_schedules
                    (kind, scheduled_question_id, payload, rule, next_run_at, dtstart)
                VALUES ('scheduled_question', $1, '{}'::jsonb, 'FREQ=WEEKLY', now(), now())
                """,
                question["id"],
            )
    assert (
        getattr(dup_exc.value, "constraint_name", None) == "job_schedules_scheduled_question_id_idx"
    )

    with pytest.raises(asyncpg.CheckViolationError) as missing_exc:
        async with db.transaction():
            await db.execute(
                """
                INSERT INTO job_schedules (kind, payload, rule, next_run_at, dtstart)
                VALUES ('scheduled_question', '{}'::jsonb, 'FREQ=WEEKLY', now(), now())
                """
            )
    assert (
        getattr(missing_exc.value, "constraint_name", None)
        == "job_schedules_scheduled_question_check"
    )

    with pytest.raises(asyncpg.CheckViolationError) as extra_exc:
        async with db.transaction():
            await db.execute(
                """
                INSERT INTO job_schedules
                    (kind, scheduled_question_id, payload, rule, next_run_at, dtstart)
                VALUES ('weekly_briefing', $1, '{}'::jsonb, 'FREQ=WEEKLY', now(), now())
                """,
                question["id"],
            )
    assert (
        getattr(extra_exc.value, "constraint_name", None)
        == "job_schedules_scheduled_question_check"
    )

    await db.execute("DELETE FROM scheduled_questions WHERE id = $1", question["id"])
    assert (
        await db.fetchval(
            "SELECT count(*) FROM job_schedules WHERE scheduled_question_id = $1",
            question["id"],
        )
        == 0
    )


async def test_scheduled_question_schedule_is_ist_and_keyed_by_question(db):
    question = await create_question(
        db, prompt="What did I spend?", rule="FREQ=DAILY;BYHOUR=9;BYMINUTE=15"
    )
    await db.execute("DELETE FROM job_schedules WHERE kind = 'scheduled_question'")

    after = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
    await sync_scheduled_questions(db, now=after)
    schedule = await db.fetchrow("SELECT * FROM job_schedules WHERE kind = 'scheduled_question'")
    assert schedule["scheduled_question_id"] == question["id"]
    assert "scheduled_question_id" not in (schedule["payload"] or {})
    assert schedule["next_run_at"] == datetime(2026, 11, 3, 3, 45, tzinfo=UTC)
