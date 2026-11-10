"""Memory kinds: the default, remember, classify, and the settings endpoint, all undoable."""

from __future__ import annotations

import pytest

from arno.tools import write as write_tools


async def _remember(db, text: str, kind: str | None = None) -> dict:
    return await write_tools.preference(
        db,
        action="remember",
        text=text,
        preference_id=None,
        message_id=None,
        kind=kind,
    )


async def _kind(db, preference_id: str) -> str:
    return await db.fetchval("SELECT kind FROM preferences WHERE id = $1", preference_id)


async def _undo(db, undo_id: str) -> None:
    await write_tools.undo(db, undo_id=undo_id, conversation_id=None, message_id=None)


async def test_a_memory_inserted_without_a_kind_is_a_preference(db):
    row = await db.fetchrow(
        "INSERT INTO preferences (body, created_by) VALUES ('Use UPI first', 'user') RETURNING kind"
    )
    assert row["kind"] == "preference"


async def test_remember_stores_the_chosen_kind(demo_ledger):
    remembered = await _remember(demo_ledger, "Mira is my flatmate", kind="fact")

    assert remembered["status"] == "remembered"
    assert remembered["kind"] == "fact"
    assert await _kind(demo_ledger, remembered["id"]) == "fact"


async def test_remembering_the_same_text_reclassifies_it_and_undo_restores_it(demo_ledger):
    first = await _remember(demo_ledger, "Family transfers are essential expenses")
    assert first["kind"] == "preference"

    again = await _remember(demo_ledger, "Family transfers are essential expenses", kind="rule")
    assert again["status"] == "remembered"
    assert again["id"] == first["id"]
    assert again["kind"] == "rule"
    assert again["undo_id"]
    assert await _kind(demo_ledger, first["id"]) == "rule"

    await _undo(demo_ledger, again["undo_id"])
    assert await _kind(demo_ledger, first["id"]) == "preference"


async def test_classify_changes_the_kind_and_undo_restores_it(demo_ledger):
    created = await _remember(demo_ledger, "Mira is my flatmate")

    classified = await write_tools.preference(
        demo_ledger,
        action="classify",
        text=None,
        preference_id=created["id"],
        message_id=None,
        kind="fact",
    )
    assert classified["status"] == "classified"
    assert classified["kind"] == "fact"
    assert classified["card"]["title"] == "Reclassified"
    assert classified["card"]["undo_id"] == classified["undo_id"]
    assert await _kind(demo_ledger, created["id"]) == "fact"

    await _undo(demo_ledger, classified["undo_id"])
    assert await _kind(demo_ledger, created["id"]) == "preference"


async def test_remembering_without_a_kind_keeps_an_existing_kind(demo_ledger):
    fact = await _remember(demo_ledger, "Mira is my flatmate", kind="fact")

    again = await _remember(demo_ledger, "Mira is my flatmate")

    assert again["id"] == fact["id"]
    assert again["kind"] == "fact"
    assert again["undo_id"] is None


async def test_classify_without_a_kind_is_rejected(demo_ledger):
    created = await _remember(demo_ledger, "Mira is my flatmate", kind="fact")

    with pytest.raises(write_tools.ToolInputError):
        await write_tools.preference(
            demo_ledger,
            action="classify",
            text=None,
            preference_id=created["id"],
            message_id=None,
        )
    assert await _kind(demo_ledger, created["id"]) == "fact"


async def test_an_unknown_kind_is_rejected(demo_ledger):
    with pytest.raises(write_tools.ToolInputError):
        await _remember(demo_ledger, "Use Hinglish when I do", kind="note")


async def test_the_kind_endpoint_changes_kind_and_undo_restores_it(authed_client, db):
    preference = await db.fetchrow(
        "INSERT INTO preferences (body, created_by, kind) "
        "VALUES ('Family transfers are essential expenses', 'agent', 'preference') "
        "RETURNING id"
    )

    response = authed_client.patch(
        f"/api/preferences/{preference['id']}/kind", json={"kind": "rule"}
    )
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["id"] == preference["id"]
    assert changed["kind"] == "rule"
    assert changed["undo_id"]
    assert await _kind(db, preference["id"]) == "rule"

    await _undo(db, changed["undo_id"])
    assert await _kind(db, preference["id"]) == "preference"


async def test_the_kind_endpoint_rejects_unknown_ids_and_bad_kinds(authed_client, db):
    preference = await db.fetchrow(
        "INSERT INTO preferences (body, created_by) VALUES ('Use UPI first', 'user') RETURNING id"
    )

    missing = authed_client.patch("/api/preferences/prf_missing/kind", json={"kind": "fact"})
    invalid = authed_client.patch(
        f"/api/preferences/{preference['id']}/kind", json={"kind": "note"}
    )

    assert missing.status_code == 404, missing.text
    assert invalid.status_code == 422, invalid.text
