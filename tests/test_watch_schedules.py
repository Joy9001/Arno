"""The recurring scheduler: India-time rules, one job per period, and no duplicate on restart."""

from __future__ import annotations

from datetime import datetime, timedelta

from fin_agent.jobs.schedules import (
    BUILTIN_KINDS,
    INDIA,
    builtin_rules,
    enqueue_due_schedules,
    ensure_builtin_schedules,
    next_occurrence,
    period_for,
    sync_scheduled_questions,
)
from fin_agent.scheduled_questions import create_question


def _job_count(db, kind: str) -> int:
    return int(
        db.execute("SELECT count(*) AS n FROM jobs WHERE kind = %s", (kind,)).fetchone()["n"]
    )


def test_builtin_schedules_seed_once_and_follow_the_notification_time(db):
    ensure_builtin_schedules(db)
    ensure_builtin_schedules(db)
    rows = db.execute("SELECT * FROM job_schedules ORDER BY kind").fetchall()
    assert {row["kind"] for row in rows} == set(BUILTIN_KINDS)
    assert len(rows) == len(BUILTIN_KINDS)
    briefing = db.execute("SELECT * FROM job_schedules WHERE kind = 'weekly_briefing'").fetchone()
    local = briefing["next_run_at"].astimezone(INDIA)
    assert local.weekday() == 6
    assert (local.hour, local.minute) == (8, 0)

    db.execute(
        "UPDATE settings SET notification_time = '09:30', weekly_briefing_dow = 3 WHERE id = 1"
    )
    db.commit()
    ensure_builtin_schedules(db)
    briefing = db.execute("SELECT * FROM job_schedules WHERE kind = 'weekly_briefing'").fetchone()
    assert "BYDAY=WE" in briefing["rule"]
    assert "BYHOUR=9" in briefing["rule"]
    assert "BYMINUTE=30" in briefing["rule"]


def test_a_missed_weekly_run_fires_once_and_moves_on_from_now(db):
    ensure_builtin_schedules(db)
    old = datetime.now(INDIA) - timedelta(days=42)
    db.execute("UPDATE job_schedules SET next_run_at = %s WHERE kind = 'weekly_briefing'", (old,))
    db.commit()

    assert enqueue_due_schedules(db) == 1
    db.commit()
    jobs = db.execute("SELECT * FROM jobs WHERE kind = 'weekly_briefing'").fetchall()
    assert len(jobs) == 1
    assert jobs[0]["key"] == f"weekly_briefing:{period_for('weekly_briefing', old)}"
    assert jobs[0]["payload"]["period"] == period_for("weekly_briefing", old)
    schedule = db.execute("SELECT * FROM job_schedules WHERE kind = 'weekly_briefing'").fetchone()
    assert schedule["next_run_at"] > datetime.now(INDIA)
    assert schedule["last_run_at"] is not None

    # A restart that re-reads the same due row must not add a second job for that period.
    db.execute("UPDATE job_schedules SET next_run_at = %s WHERE kind = 'weekly_briefing'", (old,))
    db.commit()
    enqueue_due_schedules(db)
    db.commit()
    assert _job_count(db, "weekly_briefing") == 1


def test_nightly_jobs_use_their_own_period_keys(db):
    ensure_builtin_schedules(db)
    old = datetime.now(INDIA) - timedelta(days=2)
    for kind in ("risk_check", "pattern_detection", "recompute_baselines", "backup_check"):
        db.execute("UPDATE job_schedules SET next_run_at = %s WHERE kind = %s", (old, kind))
    db.commit()
    assert enqueue_due_schedules(db) == 4
    db.commit()
    keys = {
        row["kind"]: row["key"]
        for row in db.execute(
            "SELECT kind, key FROM jobs WHERE kind <> 'weekly_briefing'"
        ).fetchall()
    }
    for kind in ("risk_check", "pattern_detection", "recompute_baselines", "backup_check"):
        assert keys[kind] == f"{kind}:{period_for(kind, old)}"


def test_a_scheduled_question_is_mirrored_and_keyed_by_the_question(db):
    question = create_question(
        db,
        prompt="What did I spend on food this week?",
        rule="FREQ=WEEKLY;BYDAY=SU",
    )
    db.commit()
    schedule = db.execute(
        "SELECT * FROM job_schedules WHERE kind = 'scheduled_question'"
    ).fetchone()
    assert schedule["payload"]["scheduled_question_id"] == question["id"]
    assert schedule["next_run_at"] > datetime.now(INDIA)

    old = datetime.now(INDIA) - timedelta(days=1)
    db.execute("UPDATE job_schedules SET next_run_at = %s WHERE id = %s", (old, schedule["id"]))
    db.commit()
    enqueue_due_schedules(db)
    db.commit()
    job = db.execute("SELECT * FROM jobs WHERE kind = 'scheduled_question'").fetchone()
    assert job["key"].startswith(f"scheduled_question:{question['id']}:")

    from fin_agent.scheduled_questions import archive_question

    archive_question(db, question["id"])
    db.commit()
    sync_scheduled_questions(db)
    db.commit()
    schedule = db.execute(
        "SELECT * FROM job_schedules WHERE kind = 'scheduled_question'"
    ).fetchone()
    assert schedule["active"] is False


def test_builtin_rules_are_valid_rrules():
    rules = builtin_rules({"notification_time": "08:00", "weekly_briefing_dow": 0})
    now = datetime.now(INDIA)
    for rule in rules.values():
        assert next_occurrence(rule, after=now) is not None
