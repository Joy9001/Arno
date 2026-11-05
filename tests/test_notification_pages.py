"""Cursor pages over the inbox, and Home's bounded unread-first read."""

from __future__ import annotations


def _insert_notification(db, *, title: str, minutes_ago: int, read: bool = False) -> str:
    row = db.execute(
        """
        INSERT INTO notifications (kind, title, created_at, read_at)
        VALUES (
            'reminder',
            %s,
            now() - make_interval(mins => %s),
            CASE WHEN %s THEN now() ELSE NULL END
        )
        RETURNING id
        """,
        (title, minutes_ago, read),
    ).fetchone()
    return row["id"]


def test_notifications_page_by_cursor_without_duplicates(authed_client, db):
    created = [
        _insert_notification(db, title=f"Item {index}", minutes_ago=index) for index in range(5)
    ]
    db.commit()

    first = authed_client.get("/api/notifications", params={"limit": 2}).json()
    assert first["unread_count"] == 5
    assert [item["id"] for item in first["notifications"]] == [created[0], created[1]]
    assert first["next_cursor"]

    second = authed_client.get(
        "/api/notifications", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [item["id"] for item in second["notifications"]] == [created[2], created[3]]
    assert second["next_cursor"]

    third = authed_client.get(
        "/api/notifications", params={"limit": 2, "cursor": second["next_cursor"]}
    ).json()
    assert [item["id"] for item in third["notifications"]] == [created[4]]
    assert third["next_cursor"] is None

    seen = [item["id"] for page in (first, second, third) for item in page["notifications"]]
    assert len(seen) == len(set(seen)) == 5


def test_notifications_cursor_is_stable_when_timestamps_match(authed_client, db):
    db.execute(
        """
        INSERT INTO notifications (kind, title, created_at)
        SELECT 'reminder', 'Same instant ' || g, timestamptz '2026-11-01 09:00:00+00'
        FROM generate_series(1, 4) AS g
        """
    )
    db.commit()
    expected = {row["id"] for row in db.execute("SELECT id FROM notifications").fetchall()}

    seen: list[str] = []
    cursor = None
    for _ in range(4):
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = authed_client.get("/api/notifications", params=params).json()
        seen.extend(item["id"] for item in page["notifications"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(set(seen)) == 4
    assert set(seen) == expected


def test_home_surfaces_an_unread_older_than_the_previous_two_hundred(authed_client, db):
    db.execute(
        """
        INSERT INTO notifications (kind, title, created_at, read_at)
        SELECT 'reminder', 'Read item ' || g, now() - make_interval(mins => g), now()
        FROM generate_series(1, 205) AS g
        """
    )
    unread = _insert_notification(db, title="Old unread reminder", minutes_ago=400, read=False)
    db.commit()

    body = authed_client.get("/api/home?horizon_days=90").json()
    assert body["unread_count"] == 1
    assert body["needs_you"][0]["id"] == unread

    # The full list still reaches it with a cursor; nothing is stranded past a fixed window.
    first = authed_client.get("/api/notifications", params={"limit": 200}).json()
    assert len(first["notifications"]) == 200
    assert first["next_cursor"]
    rest = authed_client.get(
        "/api/notifications", params={"limit": 200, "cursor": first["next_cursor"]}
    ).json()
    assert unread in {item["id"] for item in rest["notifications"]}


def test_an_invalid_notification_cursor_is_rejected(authed_client):
    response = authed_client.get("/api/notifications", params={"cursor": "not-a-cursor"})
    assert response.status_code == 422
    assert "cursor" in response.json()["detail"].lower()
