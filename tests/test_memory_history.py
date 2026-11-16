"""A superseding memory keeps its old value and date; a changed ledger fact shows its previous."""

from __future__ import annotations

from datetime import date, datetime

from fastapi.testclient import TestClient

from arno.api.app import create_app
from arno.auth.sessions import COOKIE_NAME
from arno.db.conn import Connection, connect
from arno.ledger.goals import create_goal, update_goal
from arno.ledger.schedules import create_schedule, update_schedule
from arno.ledger.settings import update_settings
from arno.tools.read import active_preferences, get_financial_context
from arno.tools.write import delete_preference_for_good, preference


async def _remember(
    conn: Connection, text: str, *, kind: str = "fact", subject: str | None = None
) -> dict:
    return await preference(
        conn,
        action="remember",
        text=text,
        preference_id=None,
        message_id=None,
        kind=kind,
        subject=subject,
    )


async def test_a_memory_of_the_same_kind_and_subject_replaces_the_old_value(ledger):
    first = await _remember(ledger, "Prefers tea", subject=" Drink  Choice ")
    changed = await _remember(ledger, "Prefers coffee", subject="drink choice")

    assert changed["id"] == first["id"]
    assert await ledger.fetchval("SELECT count(*) FROM preferences") == 1
    assert await ledger.fetchval("SELECT subject FROM preferences") == "drink choice"

    context = await get_financial_context(ledger)
    memory = next(row for row in context["preferences"] if row["id"] == first["id"])
    assert memory["text"] == "Prefers coffee"
    assert memory["previous"]["text"] == "Prefers tea"
    saved = datetime.fromisoformat(memory["previous"]["changed_at"])
    replaced = datetime.fromisoformat(memory["previous"]["superseded_at"])
    assert saved <= replaced
    assert memory["changed_at"] == replaced

    plain = await _remember(ledger, "Mira is my flatmate")
    unchanged = next(row for row in await active_preferences(ledger) if row["id"] == plain["id"])
    assert unchanged["previous"] is None


async def test_the_context_shows_the_previous_floor_and_goal_priority_after_a_change(ledger):
    await update_settings(ledger, floor_paise=1_000_000)
    await update_settings(ledger, floor_paise=2_000_000)
    goal = await create_goal(ledger, name="Emergency fund", target_paise=50_000_000, priority=2)
    await update_goal(ledger, goal["id"], priority=4)
    salary = await create_schedule(
        ledger,
        kind="income_fixed",
        name="Salary",
        rule="FREQ=MONTHLY;BYMONTHDAY=1",
        starts=date(2026, 1, 1),
        amount_paise=10_000_000,
    )
    await update_schedule(ledger, salary["id"], amount_paise=12_000_000)

    context = await get_financial_context(ledger)
    assert context["settings"]["floor"]["paise"] == 2_000_000
    assert context["settings"]["floor_history"]["previous"] == 1_000_000
    assert context["settings"]["floor_history"]["changed_at"] is not None
    updated = next(row for row in context["goals"] if row["id"] == goal["id"])
    assert updated["priority"] == 4
    assert updated["priority_history"]["previous"] == 2
    assert updated["priority_history"]["changed_at"] is not None
    paid = next(row for row in context["schedules"] if row["id"] == salary["id"])
    assert paid["amount"]["paise"] == 12_000_000
    assert paid["amount_history"]["previous"] == 10_000_000
    assert paid["amount_history"]["changed_at"] is not None


async def test_delete_for_good_removes_the_memory_and_its_superseded_value(ledger):
    first = await _remember(ledger, "Prefers tea", subject="drink choice")
    await _remember(ledger, "Prefers coffee", subject="drink choice")

    await delete_preference_for_good(ledger, first["id"])

    assert await ledger.fetchval("SELECT count(*) FROM preferences") == 0
    assert await active_preferences(ledger) == []
    rows = await ledger.fetch(
        """
        SELECT before, after FROM audit_log
        WHERE table_name = 'preferences' AND row_id = $1
        """,
        first["id"],
    )
    assert len(rows) >= 2
    for row in rows:
        assert "Prefers" not in str(row["before"])
        assert "Prefers" not in str(row["after"])


async def test_memories_and_changed_priorities_survive_a_restart(authed_client, settings, ledger):
    await _remember(ledger, "Prefers tea", subject="drink choice")
    await _remember(ledger, "Prefers coffee", subject="drink choice")
    await update_settings(ledger, floor_paise=1_000_000)
    await update_settings(ledger, floor_paise=2_000_000)
    goal = await create_goal(ledger, name="Emergency fund", target_paise=50_000_000, priority=2)
    await update_goal(ledger, goal["id"], priority=4)

    # A new connection, then a new app instance: both read the same database, not process state.
    async with connect() as fresh:
        context = await get_financial_context(fresh)
        memories = await active_preferences(fresh)
    memory = next(row for row in memories if row["body"] == "Prefers coffee")
    assert memory["previous"]["text"] == "Prefers tea"
    assert memory["previous"]["superseded_at"]
    assert context["settings"]["floor_history"]["previous"] == 1_000_000
    restarted_goal = next(row for row in context["goals"] if row["id"] == goal["id"])
    assert restarted_goal["priority"] == 4
    assert restarted_goal["priority_history"]["previous"] == 2

    token = authed_client.cookies[COOKIE_NAME]
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.set(COOKIE_NAME, token)
        response = restarted.get("/api/preferences")
    assert response.status_code == 200, response.text
    shown = next(row for row in response.json()["preferences"] if row["body"] == "Prefers coffee")
    assert shown["previous"]["text"] == "Prefers tea"
    assert shown["previous"]["superseded_at"]
