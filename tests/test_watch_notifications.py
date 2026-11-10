"""The inbox, the seeded chat handoff, and contentless push."""

from __future__ import annotations

import json

from arno.notifications import create_notification, send_push_for_kind


async def test_unread_badge_and_mark_read(authed_client, db):
    first = await create_notification(
        db,
        kind="reminder",
        title="Rent is due tomorrow",
        body="₹25,000 is due.",
        dedupe_key="reminder:sch_1:2026-11-04",
    )
    await create_notification(
        db,
        kind="risk_warning",
        title="Your projection is riskier",
        body="The floor probability fell.",
    )

    listed = authed_client.get("/api/notifications")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["unread_count"] == 2
    assert first is not None
    assert [item["id"] for item in payload["notifications"]].count(first["id"]) == 1

    read = authed_client.post(f"/api/notifications/{first['id']}/read")
    assert read.status_code == 200
    assert read.json()["read_at"] is not None
    assert authed_client.get("/api/notifications").json()["unread_count"] == 1

    assert authed_client.post("/api/notifications/read-all").json()["updated"] == 1
    assert authed_client.get("/api/notifications").json()["unread_count"] == 0


async def test_a_notification_opens_a_new_chat_and_preserves_outside_text(authed_client, db):
    row = await create_notification(
        db,
        kind="new_subscription",
        title="New recurring charge: Netflix",
        body="Netflix charged ₹649 about every month. Add it as an obligation?",
        data={
            "merchant": "Netflix",
            "outside_text": True,
            "amount_paise": 64900,
            "interval_days": 30,
        },
        dedupe_key="pattern:new:netflix:30:2026-10-01",
    )

    assert row is not None
    opened = authed_client.post(f"/api/notifications/{row['id']}/chat")
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["thread_id"]
    assert body["outside_text"] is True
    assert "<outside>Netflix</outside>" in body["message"]

    thread = await db.fetchrow("SELECT * FROM conversations WHERE id = $1", body["thread_id"])
    assert thread["outside_text_at"] is not None
    message = await db.fetchrow(
        "SELECT * FROM messages WHERE conversation_id = $1 ORDER BY created_at",
        body["thread_id"],
    )
    assert "<outside>Netflix</outside>" in message["body_text"]
    assert (
        await db.fetchval("SELECT read_at FROM notifications WHERE id = $1", row["id"])
    ) is not None


async def test_push_subscription_and_a_contentless_ping(authed_client, db, monkeypatch):
    key = authed_client.get("/api/push/key")
    assert key.status_code == 200
    assert len(key.json()["public_key"]) > 20

    subscribed = authed_client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://example.invalid/push/abc",
            "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
        },
    )
    assert subscribed.status_code == 201, subscribed.text
    assert subscribed.json()["subscribed"] is True

    sent: list[dict] = []

    def fake_webpush(**kwargs):
        sent.append(kwargs)
        return "ok"

    monkeypatch.setattr("arno.notifications.webpush", fake_webpush)
    await send_push_for_kind(db, "weekly_briefing")
    assert len(sent) == 1
    payload = json.loads(sent[0]["data"])
    assert payload["body"] == "Your weekly briefing is ready"
    assert "₹" not in sent[0]["data"]
    assert "Netflix" not in sent[0]["data"]
    assert sent[0]["vapid_claims"]["sub"]

    removed = authed_client.request(
        "DELETE",
        "/api/push/subscribe",
        json={"endpoint": "https://example.invalid/push/abc"},
    )
    assert removed.status_code == 200
    assert removed.json()["removed"] is True


async def test_settings_scheduled_questions_and_job_status_round_trip(authed_client):
    patched = authed_client.patch(
        "/api/settings",
        json={
            "notification_time": "09:15",
            "weekly_briefing_dow": 0,
            "reminder_lead_days": 5,
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["reminder_lead_days"] == 5
    assert patched.json()["notification_time"].startswith("09:15")

    created = authed_client.post(
        "/api/scheduled-questions",
        json={
            "prompt": "Every Sunday, what did I spend on food?",
            "rule": "FREQ=WEEKLY;BYDAY=SU",
            "spend_limit_usd": 1.5,
        },
    )
    assert created.status_code == 201, created.text
    question = created.json()
    assert question["next_run_at"] is not None
    assert question["spend_limit_usd"] == "1.50"

    listed = authed_client.get("/api/scheduled-questions")
    assert listed.status_code == 200
    assert listed.json()["questions"][0]["id"] == question["id"]

    archived = authed_client.delete(f"/api/scheduled-questions/{question['id']}")
    assert archived.status_code == 200
    assert archived.json()["active"] is False

    jobs = authed_client.get("/api/jobs")
    assert jobs.status_code == 200
    kinds = {item["kind"] for item in jobs.json()["schedules"]}
    assert "weekly_briefing" in kinds
    assert "reminders" in kinds
    assert "backup_check" in kinds
