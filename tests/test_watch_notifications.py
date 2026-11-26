"""The inbox and the seeded chat handoff."""

from __future__ import annotations

from arno.notifications import create_notification, seed_chat_text


async def test_unread_badge_and_mark_read(session_client, db):
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

    listed = session_client.get("/api/notifications")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["unread_count"] == 2
    assert first is not None
    assert [item["id"] for item in payload["notifications"]].count(first["id"]) == 1

    read = session_client.post(f"/api/notifications/{first['id']}/read")
    assert read.status_code == 200
    assert read.json()["read_at"] is not None
    assert session_client.get("/api/notifications").json()["unread_count"] == 1

    assert session_client.post("/api/notifications/read-all").json()["updated"] == 1
    assert session_client.get("/api/notifications").json()["unread_count"] == 0


async def test_a_notification_opens_a_new_chat_seeded_with_it(session_client, db):
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
    opened = session_client.post(f"/api/notifications/{row['id']}/chat")
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["thread_id"]
    assert "Netflix" in body["message"]

    message = await db.fetchrow(
        "SELECT * FROM messages WHERE conversation_id = $1 ORDER BY created_at",
        body["thread_id"],
    )
    assert "Netflix" in message["body_text"]
    assert (
        await db.fetchval("SELECT read_at FROM notifications WHERE id = $1", row["id"])
    ) is not None


async def test_opening_a_notification_starts_arno_s_own_reply(session_client, db, monkeypatch):
    calls: list[str] = []
    prompts: list[str] = []

    async def record(app, conn, *, thread, deps, prompt):
        del app, conn, deps
        calls.append(str(thread["id"]))
        prompts.append(prompt[1])

    monkeypatch.setattr("arno.api.watch._start_seeded_turn", record)
    row = await create_notification(
        db,
        kind="risk_warning",
        title="Your projection is riskier",
        body="The chance of staying above zero is 55%.",
        data={"outside_text": True},
    )
    assert row is not None

    opened = session_client.post(f"/api/notifications/{row['id']}/chat")

    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["started"] is True
    assert calls == [body["thread_id"]]
    seeded = await db.fetchrow(
        "SELECT role, body_text FROM messages WHERE conversation_id = $1 ORDER BY created_at",
        body["thread_id"],
    )
    assert seeded["role"] == "user"
    assert "Help me with this." in seeded["body_text"]
    # The seeded message is the turn's prompt, not only a row the UI shows.
    assert "Help me with this." in prompts[0]


async def test_arno_reads_the_seeded_notification_and_answers_it(session_client, db):
    import asyncio

    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from tests.agent_helpers import streaming_model

    seen: list[str] = []

    def answer(messages, info):
        last = messages[-1]
        if isinstance(last, ModelRequest):
            seen.extend(
                str(part.content) for part in last.parts if isinstance(part, UserPromptPart)
            )
        return ModelResponse(parts=[TextPart("Here is what that means.")])

    row = await create_notification(
        db, kind="risk_warning", title="Riskier", body="The chance is 55%.", data={}
    )
    assert row is not None
    app = session_client.app
    with app.state.agent.override(model=streaming_model(answer)):
        opened = session_client.post(f"/api/notifications/{row['id']}/chat")
        assert opened.status_code == 200, opened.text
        thread_id = opened.json()["thread_id"]
        turn = app.state.turns.get(thread_id)
        assert turn is not None
        await asyncio.wait_for(turn.done.wait(), timeout=30)

    assert any("The chance is 55%." in text for text in seen)
    reply = await db.fetchrow(
        "SELECT body_text FROM messages WHERE conversation_id = $1 AND role = 'assistant'",
        thread_id,
    )
    assert reply is not None and "Here is what that means." in reply["body_text"]
    users = await db.fetchval(
        "SELECT count(*) FROM messages WHERE conversation_id = $1 AND role = 'user'", thread_id
    )
    assert users == 1


async def test_settings_and_scheduled_questions_round_trip(session_client):
    patched = session_client.patch(
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

    created = session_client.post(
        "/api/scheduled-questions",
        json={
            "prompt": "Every Sunday, what did I spend on food?",
            "rule": "FREQ=WEEKLY;BYDAY=SU",
        },
    )
    assert created.status_code == 201, created.text
    question = created.json()
    assert question["next_run_at"] is not None

    listed = session_client.get("/api/scheduled-questions")
    assert listed.status_code == 200
    assert question["id"] in [item["id"] for item in listed.json()["questions"]]

    archived = session_client.delete(f"/api/scheduled-questions/{question['id']}")
    assert archived.status_code == 200
    assert archived.json()["active"] is False


async def test_notification_dedupe_key_inserts_once_and_orders_newest_first(db):
    first = await create_notification(db, kind="reminder", title="One", dedupe_key="dedupe:one")
    duplicate = await create_notification(db, kind="reminder", title="Two", dedupe_key="dedupe:one")
    assert duplicate is None
    assert await db.fetchval("SELECT count(*) FROM notifications") == 1

    await db.execute(
        """
        INSERT INTO notifications (kind, title, created_at)
        VALUES ('reminder', 'Older', '2026-11-01 08:00:00+00'),
               ('reminder', 'Same instant', '2026-11-01 09:00:00+00'),
               ('reminder', 'Same instant too', '2026-11-01 09:00:00+00')
        """
    )
    rows = await db.fetch("SELECT * FROM notifications ORDER BY created_at DESC, id DESC")
    listed = [row["title"] for row in rows]
    assert listed[0] == "One"
    assert set(listed[1:3]) == {"Same instant", "Same instant too"}
    assert listed[3] == "Older"
    assert first is not None


def test_a_reminder_seeds_its_schedule_name_and_amount():
    text = seed_chat_text(
        {
            "kind": "reminder",
            "title": "Rent is due tomorrow",
            "body": "Rent costs ₹25,000.",
            "data": {"name": "Rent", "outside_text": True},
        }
    )
    assert "Rent is due tomorrow" in text
    assert "₹25,000" in text


async def test_notification_pagination_is_stable_across_equal_timestamps(session_client, db):
    await db.execute(
        """
        INSERT INTO notifications (kind, title, created_at)
        SELECT 'reminder', 'Item ' || g, timestamptz '2026-11-01 09:00:00+00'
        FROM generate_series(1, 7) AS g
        """
    )
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = session_client.get("/api/notifications", params=params).json()
        seen.extend(item["id"] for item in page["notifications"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 7
    assert session_client.get("/api/notifications", params={"limit": 0}).status_code == 422
    assert session_client.get("/api/notifications", params={"limit": 201}).status_code == 422


def test_watch_endpoints_require_a_session(client):
    for path in (
        "/api/notifications",
        "/api/notifications/read-all",
        "/api/scheduled-questions",
    ):
        method = "POST" if path.endswith(("read-all",)) else "GET"
        assert client.request(method, path).status_code == 401, path
