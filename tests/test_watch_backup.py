"""The 48-hour backup alert, once per outage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from arno.jobs.backup import backup_alert


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
