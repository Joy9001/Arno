"""The 48-hour backup alert, once per outage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from arno.jobs.backup import backup_alert
from arno.jobs.schedules import INDIA, builtin_rules, next_occurrence


async def _success(db, when: datetime) -> None:
    await db.execute(
        """
        INSERT INTO backup_runs (started_at, finished_at, status, dump_bytes)
        VALUES ($1, $2, 'success', 1024)
        """,
        when - timedelta(minutes=1),
        when,
    )


async def test_backup_alert_waits_48_hours_and_fires_once(db):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    await _success(db, now - timedelta(hours=47))
    assert await backup_alert(db, now=now) is None

    await db.execute("DELETE FROM backup_runs")
    await _success(db, now - timedelta(hours=49))
    first = await backup_alert(db, now=now)
    assert first is not None
    assert first["kind"] == "backup_stale"
    assert "48-hour" in first["body"]

    assert await backup_alert(db, now=now + timedelta(hours=2)) is None
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'backup_stale'") == 1


async def test_a_new_success_starts_a_new_episode(db):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    await _success(db, now - timedelta(hours=50))
    assert await backup_alert(db, now=now) is not None

    await _success(db, now - timedelta(hours=1))
    assert await backup_alert(db, now=now) is None
    later = now + timedelta(hours=49)
    second = await backup_alert(db, now=later)
    assert second is not None
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'backup_stale'") == 2


async def test_a_fresh_install_waits_48_hours_from_install(db):
    installed = await db.fetchval("SELECT created_at FROM settings WHERE id = 1")
    assert await backup_alert(db, now=installed + timedelta(hours=1)) is None
    assert await backup_alert(db, now=installed + timedelta(hours=47)) is None
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'backup_stale'") == 0

    assert await backup_alert(db, now=installed + timedelta(hours=48)) is not None
    assert await backup_alert(db, now=installed + timedelta(hours=72)) is None
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'backup_stale'") == 1


async def test_a_failed_attempt_is_not_treated_as_a_success(db):
    installed = await db.fetchval("SELECT created_at FROM settings WHERE id = 1")
    await db.execute(
        """
        INSERT INTO backup_runs (started_at, finished_at, status, error)
        VALUES ($1, $1, 'failed', 'restic is down')
        """,
        installed + timedelta(hours=2),
    )
    assert await backup_alert(db, now=installed + timedelta(hours=47)) is None
    assert await backup_alert(db, now=installed + timedelta(hours=49)) is not None
    assert await db.fetchval("SELECT count(*) FROM notifications WHERE kind = 'backup_stale'") == 1


def test_backup_check_runs_at_three_ist_which_is_2130_utc():
    rules = builtin_rules({"notification_time": "08:00", "weekly_briefing_dow": 0})
    after = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    occurrence = next_occurrence(rules["backup_check"], after=after)
    assert occurrence is not None
    assert occurrence.astimezone(INDIA).hour == 3
    assert occurrence.astimezone(UTC) == datetime(2026, 11, 3, 21, 30, tzinfo=UTC)
