"""A superseding memory keeps its old value and date, and survives a restart."""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from arno.api.app import create_app
from arno.db.conn import Connection, connect
from arno.tools.read import active_preferences
from arno.tools.write import change_preference, delete_preference_for_good
from tests.conftest import OWNER_EMAIL


async def _remember(
    conn: Connection, text: str, *, kind: str = "fact", subject: str | None = None
) -> dict:
    return await change_preference(conn, action="add", text=text, kind=kind, subject=subject)


async def test_a_memory_of_the_same_kind_and_subject_replaces_the_old_value(ledger):
    first = await _remember(ledger, "Prefers tea", subject=" Drink  Choice ")
    changed = await _remember(ledger, "Prefers coffee", subject="drink choice")

    assert changed["id"] == first["id"]
    assert await ledger.fetchval("SELECT count(*) FROM preferences") == 1
    assert await ledger.fetchval("SELECT subject FROM preferences") == "drink choice"

    memory = next(row for row in await active_preferences(ledger) if row["id"] == first["id"])
    assert memory["body"] == "Prefers coffee"
    assert memory["previous"]["text"] == "Prefers tea"
    saved = datetime.fromisoformat(memory["previous"]["changed_at"])
    replaced = datetime.fromisoformat(memory["previous"]["superseded_at"])
    assert saved <= replaced

    plain = await _remember(ledger, "Mira is my flatmate")
    unchanged = next(row for row in await active_preferences(ledger) if row["id"] == plain["id"])
    assert unchanged["previous"] is None


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


async def test_memories_survive_a_restart(session_client, settings, ledger):
    await _remember(ledger, "Prefers tea", subject="drink choice")
    await _remember(ledger, "Prefers coffee", subject="drink choice")

    # A new connection, then a new app instance: both read the same database, not process state.
    async with connect() as fresh:
        memories = await active_preferences(fresh)
    memory = next(row for row in memories if row["body"] == "Prefers coffee")
    assert memory["previous"]["text"] == "Prefers tea"
    assert memory["previous"]["superseded_at"]

    with TestClient(create_app(settings)) as restarted:
        restarted.headers["Tailscale-User-Login"] = OWNER_EMAIL
        response = restarted.get("/api/preferences")
    assert response.status_code == 200, response.text
    shown = next(row for row in response.json()["preferences"] if row["body"] == "Prefers coffee")
    assert shown["previous"]["text"] == "Prefers tea"
    assert shown["previous"]["superseded_at"]
