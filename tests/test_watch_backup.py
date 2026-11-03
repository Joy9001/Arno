"""The 48-hour backup alert, once per outage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fin_agent.jobs.backup import backup_alert


def _success(db, when: datetime) -> None:
    db.execute(
        """
        INSERT INTO backup_runs (started_at, finished_at, status, dump_bytes)
        VALUES (%s, %s, 'success', 1024)
        """,
        (when - timedelta(minutes=1), when),
    )
    db.commit()


def test_backup_alert_waits_48_hours_and_fires_once(db):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    _success(db, now - timedelta(hours=47))
    assert backup_alert(db, now=now) is None

    db.execute("DELETE FROM backup_runs")
    db.commit()
    _success(db, now - timedelta(hours=49))
    first = backup_alert(db, now=now)
    db.commit()
    assert first is not None
    assert first["kind"] == "backup_stale"
    assert "48-hour" in first["body"]

    assert backup_alert(db, now=now + timedelta(hours=2)) is None
    assert (
        db.execute(
            "SELECT count(*) AS n FROM notifications WHERE kind = 'backup_stale'"
        ).fetchone()["n"]
        == 1
    )


def test_a_new_success_starts_a_new_episode(db):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    _success(db, now - timedelta(hours=50))
    assert backup_alert(db, now=now) is not None
    db.commit()

    _success(db, now - timedelta(hours=1))
    assert backup_alert(db, now=now) is None
    later = now + timedelta(hours=49)
    second = backup_alert(db, now=later)
    db.commit()
    assert second is not None
    assert (
        db.execute(
            "SELECT count(*) AS n FROM notifications WHERE kind = 'backup_stale'"
        ).fetchone()["n"]
        == 2
    )
