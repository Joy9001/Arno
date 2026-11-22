"""Notification resolution with undo parity (C18 box 2) and failure recovery APIs (C18 box 3)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart
from pywebpush import WebPushException

from arno.jobs.briefing import send_weekly_briefing
from arno.jobs.patterns import run_pattern_detection
from arno.jobs.reminders import send_reminders
from arno.jobs.runner import _fail, enqueue_job
from arno.ledger.accounts import create_account
from arno.ledger.audit import undo_group
from arno.ledger.movements import record_expense
from arno.ledger.schedule_payments import link_payment, unlink_payment
from arno.ledger.schedules import create_schedule
from arno.notifications import send_push_for_kind
from tests.agent_helpers import streaming_model
from tests.invariants import check_ledger


async def _setup_schedule_and_alerts(ledger) -> tuple[dict[str, Any], dict[str, Any], date]:
    bank = await create_account(
        ledger,
        kind="bank",
        name="Recovery Bank",
        opening_balance_paise=1_000_000,
    )
    due = date(2026, 10, 5)
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Rent",
        amount_paise=250_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 10, 5),
    )
    reminders = await send_reminders(ledger, as_of=date(2026, 10, 4), lead_days=3)
    missed = await run_pattern_detection(ledger, as_of=date(2026, 10, 10))
    assert any(item["kind"] == "reminder" for item in reminders)
    assert any(item["kind"] == "missed_recurring" for item in missed)
    return bank, schedule, due


async def test_supported_payment_resolves_alerts_and_undo_restores_open_state(
    session_client, ledger
):
    bank, schedule, due = await _setup_schedule_and_alerts(ledger)

    inbox = session_client.get("/api/notifications")
    assert inbox.status_code == 200
    body = inbox.json()
    assert body["unread_count"] == 2
    assert {item["kind"] for item in body["notifications"]} == {"reminder", "missed_recurring"}
    assert all(
        item["resolved_at"] is None and item["resolved_by"] is None
        for item in body["notifications"]
    )
    assert session_client.get("/api/home").json()["unread_count"] == 2

    movement = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=250_000,
        description="October rent payment",
        date=due,
    )
    linked = await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=due,
        movement_id=movement["id"],
    )
    assert linked["status"] == "linked"

    rows = await ledger.fetch(
        "SELECT kind, resolved_at, resolved_by FROM notifications ORDER BY kind"
    )
    assert len(rows) == 2
    assert all(row["resolved_at"] is not None for row in rows)
    assert all(row["resolved_by"] == movement["id"] for row in rows)

    # Resolved alerts leave the active inbox and home badge, but remain available on request.
    after_pay = session_client.get("/api/notifications").json()
    assert after_pay["unread_count"] == 0
    assert after_pay["notifications"] == []
    assert session_client.get("/api/home").json()["unread_count"] == 0

    resolved_page = session_client.get(
        "/api/notifications", params={"include_resolved": "true"}
    ).json()
    assert len(resolved_page["notifications"]) == 2
    assert all(item["resolved_by"] == movement["id"] for item in resolved_page["notifications"])
    assert all(item["resolved_at"] is not None for item in resolved_page["notifications"])
    assert await check_ledger(ledger) == []

    # Undoing the link write group restores both notifications inside the same audited undo.
    await undo_group(ledger, linked["undo_id"])
    reopened = session_client.get("/api/notifications").json()
    assert reopened["unread_count"] == 2
    assert {item["kind"] for item in reopened["notifications"]} == {"reminder", "missed_recurring"}
    assert all(
        item["resolved_at"] is None and item["resolved_by"] is None
        for item in reopened["notifications"]
    )
    assert await check_ledger(ledger) == []

    # Re-link, then undo the payment movement itself: reversal must also reopen the alerts.
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=due,
        movement_id=movement["id"],
    )
    assert session_client.get("/api/notifications").json()["unread_count"] == 0

    await undo_group(ledger, movement["undo_id"])
    after_movement_undo = session_client.get("/api/notifications").json()
    assert after_movement_undo["unread_count"] == 2
    assert all(
        item["resolved_at"] is None and item["resolved_by"] is None
        for item in after_movement_undo["notifications"]
    )
    assert await check_ledger(ledger) == []


async def test_unlinked_partial_and_elapsed_date_leave_alerts_open(ledger):
    bank, schedule, due = await _setup_schedule_and_alerts(ledger)

    # Recording a movement without matching it to the occurrence leaves both alerts open.
    first = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Partial rent",
        date=due,
    )
    rows = await ledger.fetch("SELECT resolved_at FROM notifications")
    assert all(row["resolved_at"] is None for row in rows)

    # A partial payment disproves the missed-payment warning but leaves the reminder open.
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=due,
        movement_id=first["id"],
    )
    rows = await ledger.fetch("SELECT kind, resolved_at FROM notifications")
    assert next(row for row in rows if row["kind"] == "reminder")["resolved_at"] is None
    assert next(row for row in rows if row["kind"] == "missed_recurring")["resolved_at"] is not None

    # Elapsing past due/later dates alone must never mark open alerts resolved.
    await send_reminders(ledger, as_of=date(2026, 11, 2), lead_days=3)
    await run_pattern_detection(ledger, as_of=date(2026, 11, 2))
    rows = await ledger.fetch(
        "SELECT kind, resolved_at FROM notifications WHERE dedupe_key LIKE $1 "
        "AND kind IN ('reminder', 'missed_recurring')",
        f"%:{schedule['id']}:2026-10-05",
    )
    assert len(rows) == 2
    assert next(row for row in rows if row["kind"] == "reminder")["resolved_at"] is None
    assert next(row for row in rows if row["kind"] == "missed_recurring")["resolved_at"] is not None

    # Completing the occurrence amount resolves them; unlinking the top-up reopens them.
    second = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=150_000,
        description="Remaining rent",
        date=due,
    )
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=due,
        movement_id=second["id"],
    )
    resolved_rows = await ledger.fetch(
        "SELECT kind, resolved_at, resolved_by FROM notifications WHERE dedupe_key LIKE $1 "
        "AND kind IN ('reminder', 'missed_recurring')",
        f"%:{schedule['id']}:2026-10-05",
    )
    assert all(row["resolved_at"] is not None for row in resolved_rows)
    assert (
        next(row for row in resolved_rows if row["kind"] == "reminder")["resolved_by"]
        == second["id"]
    )
    assert (
        next(row for row in resolved_rows if row["kind"] == "missed_recurring")["resolved_by"]
        == first["id"]
    )

    await unlink_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=due,
        movement_id=second["id"],
    )
    reopened_rows = await ledger.fetch(
        "SELECT kind, resolved_at, resolved_by FROM notifications WHERE dedupe_key LIKE $1 "
        "AND kind IN ('reminder', 'missed_recurring')",
        f"%:{schedule['id']}:2026-10-05",
    )
    assert next(row for row in reopened_rows if row["kind"] == "reminder")["resolved_at"] is None
    assert (
        next(row for row in reopened_rows if row["kind"] == "missed_recurring")["resolved_by"]
        == first["id"]
    )
    assert await check_ledger(ledger) == []


async def test_failed_job_state_is_visible_and_retryable_over_api(session_client, db):
    job_id = await enqueue_job(db, key="recovery:job:1", kind="weekly_briefing")
    job_row = await db.fetchrow_required("SELECT * FROM jobs WHERE id = $1", job_id)
    await _fail(db, dict(job_row), "connection timed out", timed_out=True, final=True)

    jobs_payload = session_client.get("/api/jobs").json()
    failed = next(item for item in jobs_payload["recent"] if item["id"] == job_id)
    assert failed["status"] == "failed"
    assert failed["last_error"] == "connection timed out"

    inbox = session_client.get("/api/notifications").json()
    alert = next(item for item in inbox["notifications"] if item["kind"] == "job_failed")
    assert alert["data"]["job_id"] == job_id

    retried = session_client.post(f"/api/jobs/{job_id}/retry")
    assert retried.status_code == 200, retried.text
    retried_body = retried.json()
    assert retried_body["status"] == "pending"
    assert retried_body["attempts"] == 0
    assert retried_body["last_error"] is None

    after_retry = session_client.get("/api/notifications").json()
    assert all(item["id"] != alert["id"] for item in after_retry["notifications"])
    resolved = await db.fetchrow(
        "SELECT resolved_at, resolved_by FROM notifications WHERE id = $1", alert["id"]
    )
    assert resolved["resolved_at"] is not None
    assert resolved["resolved_by"] == "job_retry"


async def test_briefing_model_outage_persists_recovery_state_and_later_recovers(session_client, db):
    def down(messages, info):
        del messages, info
        raise TimeoutError("provider unreachable")

    outage = await send_weekly_briefing(
        db,
        as_of=date(2026, 11, 1),
        period="2026-W44-outage",
        model=streaming_model(down),
    )
    assert outage is not None

    inbox = session_client.get("/api/notifications").json()
    item = next(row for row in inbox["notifications"] if row["id"] == outage["id"])
    assert item["data"]["prose"] == "template"
    assert item["data"]["prose_error"] == "TimeoutError"
    assert item["body"] == item["data"]["template"]

    settings = session_client.get("/api/settings").json()
    assert settings["last_model_error_type"] == "TimeoutError"
    assert settings["last_model_error_at"] is not None

    def recovered(messages, info):
        del messages, info
        return ModelResponse(parts=[TextPart("All systems normal this week.")])

    ok = await send_weekly_briefing(
        db,
        as_of=date(2026, 11, 8),
        period="2026-W45-recovered",
        model=streaming_model(recovered),
    )
    assert ok is not None
    assert ok["data"]["prose"] == "model"
    assert ok["data"]["prose_error"] is None

    # This is the shared last error, which may also describe an unrelated chat failure.
    assert session_client.get("/api/settings").json()["last_model_error_type"] == "TimeoutError"


async def test_push_opt_out_and_permission_failures_expose_status_endpoint(
    session_client, db, monkeypatch
):
    initial = session_client.get("/api/push/status")
    assert initial.status_code == 200
    assert initial.json()["status"] == "unsubscribed"
    assert initial.json()["subscribed"] is False
    assert initial.json()["inbox_fallback_active"] is True

    sub = session_client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://example.invalid/push/recovery",
            "keys": {"p256dh": "p256dh-val", "auth": "auth-val"},
        },
    )
    assert sub.status_code == 201
    active = session_client.get("/api/push/status").json()
    assert active["status"] == "active"
    assert active["subscribed"] is True
    assert active["subscription_count"] == 1
    assert active["opted_out"] is False
    assert active["inbox_fallback_active"] is True

    unsub = session_client.request(
        "DELETE",
        "/api/push/subscribe",
        json={"endpoint": "https://example.invalid/push/recovery"},
    )
    assert unsub.status_code == 200
    opted_out = session_client.get("/api/push/status").json()
    assert opted_out["status"] == "opted_out"
    assert opted_out["opted_out"] is True
    assert opted_out["opted_out_at"] is not None
    assert opted_out["subscribed"] is False
    assert opted_out["inbox_fallback_active"] is True

    # Re-subscribing clears the opt-out state; a 500 error leaves subscription in degraded state.
    resub = session_client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://example.invalid/push/recovery",
            "keys": {"p256dh": "p256dh-val", "auth": "auth-val"},
        },
    )
    assert resub.status_code == 201

    def server_error(**kwargs):
        del kwargs
        raise WebPushException("gateway down", response=SimpleNamespace(status=500, text=""))

    monkeypatch.setattr("arno.notifications.webpush", server_error)
    assert await send_push_for_kind(db, "weekly_briefing") == 0
    degraded = session_client.get("/api/push/status").json()
    assert degraded["status"] == "degraded"
    assert degraded["subscription_count"] == 1
    assert degraded["last_error"].startswith("delivery_failed:")
    assert degraded["last_error_at"] is not None
    assert degraded["inbox_fallback_active"] is True

    # A 410 permission revocation prunes the subscription and exposes permission_revoked.
    def revoked(**kwargs):
        del kwargs
        raise WebPushException("gone", response=SimpleNamespace(status=410, text=""))

    monkeypatch.setattr("arno.notifications.webpush", revoked)
    assert await send_push_for_kind(db, "weekly_briefing") == 0
    revoked_status = session_client.get("/api/push/status").json()
    assert revoked_status["status"] == "permission_revoked"
    assert revoked_status["permission_revoked"] is True
    assert revoked_status["subscription_count"] == 0
    assert revoked_status["last_error"] == "permission_revoked: HTTP 410"
    assert revoked_status["last_error_at"] is not None
    assert revoked_status["inbox_fallback_active"] is True


async def test_job_activity_reports_every_finish_after_the_cursor(session_client, db):
    await db.execute("DELETE FROM jobs")
    await db.execute(
        """
        INSERT INTO jobs (key, kind, status, started_at, finished_at)
        SELECT 'audit:finish:' || g, 'x', 'done',
               now() - interval '10 seconds', now() - interval '5 seconds'
        FROM generate_series(1, 60) AS g
        """
    )
    since = await db.fetchval("SELECT now() - interval '1 minute'")
    body = session_client.get("/api/jobs/activity", params={"since": since.isoformat()}).json()
    assert len(body["finished"]) == 60
    assert {job["key"] for job in body["finished"]} == {
        f"audit:finish:{index}" for index in range(1, 61)
    }
