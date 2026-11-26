"""The background layer: spawn, the minute loop's claim, the daily tasks, and the webhook."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from arno.config import get_settings
from arno.jobs import background, loop
from arno.notifications import create_notification, post_webhook
from arno.scheduled_questions import (
    BRIEFING_ID,
    INDIA,
    create_question,
    ensure_default_question,
)


async def test_spawn_does_nothing_when_jobs_are_off():
    async def work() -> None:
        raise AssertionError("must not run")

    assert get_settings().jobs_enabled is False
    assert background.spawn(work(), name="off") is None


async def test_spawn_isolates_a_failure_and_drops_the_reference(monkeypatch, clean_database):
    from dataclasses import replace

    import arno.config as config

    monkeypatch.setattr(config, "_cached", replace(get_settings(), jobs_enabled=True))
    ran: list[str] = []

    async def boom() -> None:
        ran.append("boom")
        raise RuntimeError("expected")

    async def fine() -> None:
        ran.append("fine")

    first = background.spawn(boom(), name="boom")
    second = background.spawn(fine(), name="fine")
    assert first is not None and second is not None
    await background.drain(5)
    assert sorted(ran) == ["boom", "fine"]
    assert not background._tasks


async def test_the_default_briefing_seeds_once_and_an_archive_sticks(db):
    await ensure_default_question(db)
    row = await db.fetchrow("SELECT * FROM scheduled_questions WHERE id = $1", BRIEFING_ID)
    assert "FREQ=WEEKLY;BYDAY=SU;BYHOUR=8;BYMINUTE=0" == row["rule"]
    assert row["next_run_at"] > datetime.now(UTC)
    await db.execute("UPDATE scheduled_questions SET active = false WHERE id = $1", BRIEFING_ID)
    await ensure_default_question(db)
    assert await db.fetchval("SELECT count(*) FROM scheduled_questions") == 1
    assert await db.fetchval("SELECT active FROM scheduled_questions") is False


async def test_a_due_question_is_claimed_once_and_moves_on(db):
    question = await create_question(db, prompt="Spend?", rule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0")
    old = datetime.now(INDIA) - timedelta(days=5)
    await db.execute("UPDATE scheduled_questions SET next_run_at = $1", old)
    now = datetime.now(INDIA)

    claimed = await loop.claim_due_questions(db, now)
    assert claimed == [(question["id"], old.astimezone(INDIA).strftime("%Y-%m-%dT%H:%M"))]
    assert await db.fetchval("SELECT next_run_at FROM scheduled_questions") > now
    # A restart (or a second claimer) finds nothing due: the missed days fire once.
    assert await loop.claim_due_questions(db, now) == []


async def test_a_finished_rule_deactivates_after_its_last_run(db):
    await create_question(db, prompt="Once", rule="FREQ=DAILY;COUNT=2;BYHOUR=9;BYMINUTE=0")
    await db.execute("UPDATE scheduled_questions SET next_run_at = now() - interval '1 hour'")
    later = datetime.now(INDIA) + timedelta(days=3)
    assert len(await loop.claim_due_questions(db, later)) == 1
    assert await db.fetchval("SELECT active FROM scheduled_questions") is False


async def test_recover_resumes_queued_and_running_categorizations(db, monkeypatch):
    seen: list[str] = []

    def fake_spawn(coro, *, name):
        coro.close()
        seen.append(name)

    monkeypatch.setattr(loop, "spawn", fake_spawn)
    await db.execute(
        """
        INSERT INTO imports (id, file_hash, status, categorize) VALUES
            ('imp_q', 'a', 'committed', '{"state": "queued"}'),
            ('imp_r', 'b', 'committed', '{"state": "running"}'),
            ('imp_d', 'c', 'committed', '{"state": "done"}')
        """
    )
    assert await loop.recover() == 2
    assert sorted(seen) == ["categorize-imp_q", "categorize-imp_r"]


async def test_risk_warning_dedupes_within_a_week(ledger, monkeypatch):
    from arno.jobs import risk

    projection = {
        "data_quality": {},
        "floor_probability": 0.4,
        "floor_threshold": 0.9,
        "floor_paise": 0,
        "first_floor_crossing": None,
        "source_ids": [],
    }

    async def fake(*_a, **_k):
        return projection

    monkeypatch.setattr(risk.projection_tools, "project", fake)
    monday = datetime(2026, 11, 2).date()
    assert await risk.risk_warning(ledger, as_of=monday) is not None
    assert await risk.risk_warning(ledger, as_of=monday + timedelta(days=1)) is None
    assert await risk.risk_warning(ledger, as_of=monday + timedelta(days=7)) is not None


async def test_webhook_posts_form_text_to_telegram_and_plain_text_elsewhere():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    await post_webhook(
        "https://api.telegram.org/bot1/sendMessage?chat_id=2",
        "Rent due",
        "Due 1st",
        transport=transport,
    )
    await post_webhook("https://ntfy.sh/arno", "Rent due", "Due 1st", transport=transport)
    telegram, ntfy = seen
    assert telegram.content == b"text=Rent+due%0ADue+1st"
    assert ntfy.headers["Title"] == "Rent due"
    assert ntfy.content == b"Due 1st"


async def test_a_failing_webhook_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    await post_webhook("https://ntfy.sh/x", "t", None, transport=httpx.MockTransport(handler))


async def test_only_a_new_notification_posts_the_webhook(db, monkeypatch):
    from dataclasses import replace

    import arno.config as config
    from arno import notifications

    monkeypatch.setattr(
        config, "_cached", replace(get_settings(), notify_webhook_url="https://ntfy.sh/x")
    )
    sent: list[str] = []

    def fake_spawn(coro, *, name):
        coro.close()
        sent.append(name)

    monkeypatch.setattr(notifications, "spawn", fake_spawn)
    await create_notification(db, kind="k", title="t", dedupe_key="same")
    await create_notification(db, kind="k", title="t", dedupe_key="same")
    assert len(sent) == 1


async def test_erase_refuses_while_a_background_task_runs(db, monkeypatch):
    from dataclasses import replace

    import arno.config as config
    from arno.ledger.deletion import erase_everything
    from arno.ledger.errors import ConflictLedgerError

    monkeypatch.setattr(config, "_cached", replace(get_settings(), jobs_enabled=True))
    started, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        started.set()
        await release.wait()

    task = background.spawn(hold(), name="hold")
    assert task is not None
    await asyncio.wait_for(started.wait(), 10)
    with pytest.raises(ConflictLedgerError):
        await erase_everything(db)
    release.set()
    await background.drain(5)
    await erase_everything(db)


async def test_drain_cancels_what_outlives_the_timeout(monkeypatch, clean_database):
    from dataclasses import replace

    import arno.config as config

    monkeypatch.setattr(config, "_cached", replace(get_settings(), jobs_enabled=True))
    task = background.spawn(asyncio.sleep(60), name="slow")
    assert task is not None
    await background.drain(0.2)
    assert task.cancelled()


@pytest.mark.parametrize("outcome", ["done", "partial", "failed"])
async def test_categorize_entry_point_records_its_state(db, monkeypatch, outcome):
    from arno.importers import categorize

    await db.execute(
        "INSERT INTO imports (id, file_hash, status, categorize) "
        "VALUES ('imp_x', 'h', 'committed', '{\"state\": \"queued\"}')"
    )

    async def fake_run(conn, import_id, *, model=None):
        if outcome == "failed":
            raise RuntimeError("model down")
        return {"status": outcome, "labeled": 3, "uncategorized": 1, "failed_payees": 0}

    monkeypatch.setattr(categorize, "run_categorize", fake_run)
    monkeypatch.setattr(categorize, "primary_model", lambda: None)
    await categorize.handle_categorize_import("imp_x")
    state = await db.fetchval("SELECT categorize FROM imports WHERE id = 'imp_x'")
    assert state["state"] == outcome
    assert "started_at" in state and "finished_at" in state
    if outcome == "failed":
        assert state["error"] == "model down"
    else:
        assert (state["labeled"], state["uncategorized"]) == (3, 1)
