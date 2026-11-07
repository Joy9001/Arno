"""Settings-only preference text edits: identity preservation, audit, and privacy."""

from __future__ import annotations

from datetime import datetime


async def _insert_preference(
    db, body: str, *, active: bool = True, created_by: str = "agent"
) -> dict:
    return await db.fetchrow(
        """
        INSERT INTO preferences (body, created_by, active)
        VALUES ($1, $2, $3) RETURNING *
        """,
        body,
        created_by,
        active,
    )


async def test_edit_rewrites_body_and_keeps_identity_and_audits_as_user(authed_client, db):
    preference = await _insert_preference(db, "Prefers UPI for groceries", active=False)

    response = authed_client.patch(
        f"/api/preferences/{preference['id']}/content",
        json={"body": "  Prefers cards for groceries  "},
    )
    assert response.status_code == 200, response.text
    edited = response.json()

    assert edited["body"] == "Prefers cards for groceries"
    assert edited["id"] == preference["id"]
    assert edited["active"] is False
    assert edited["created_by"] == "agent"
    assert edited["source_message_id"] == preference["source_message_id"]
    assert datetime.fromisoformat(edited["updated_at"]) > datetime.fromisoformat(
        str(preference["updated_at"])
    )

    audit = await db.fetchrow(
        """
        SELECT actor, before, after FROM audit_log
        WHERE table_name = 'preferences' AND row_id = $1
        ORDER BY created_at DESC LIMIT 1
        """,
        preference["id"],
    )
    assert audit["actor"] == "user"
    assert audit["before"]["body"] == "Prefers UPI for groceries"
    assert audit["after"]["body"] == "Prefers cards for groceries"


async def test_edit_rejects_blank_whitespace_extra_fields_and_unknown_ids(authed_client, db):
    preference = await _insert_preference(db, "Prefers UPI")

    blank = authed_client.patch(f"/api/preferences/{preference['id']}/content", json={"body": ""})
    whitespace = authed_client.patch(
        f"/api/preferences/{preference['id']}/content", json={"body": "   \n  "}
    )
    extra = authed_client.patch(
        f"/api/preferences/{preference['id']}/content",
        json={"body": "Prefers cards", "active": False},
    )
    missing = authed_client.patch(
        "/api/preferences/prf_missing/content", json={"body": "Prefers cards"}
    )

    assert blank.status_code == 422, blank.text
    assert whitespace.status_code == 422, whitespace.text
    assert extra.status_code == 422, extra.text
    assert missing.status_code == 404, missing.text


async def test_delete_for_good_scrubs_the_edit_history_too(authed_client, db):
    marker = "sushi budget is personal"
    preference = await _insert_preference(db, marker)

    edited = authed_client.patch(
        f"/api/preferences/{preference['id']}/content",
        json={"body": f"{marker}, keep it quiet"},
    )
    assert edited.status_code == 200, edited.text

    deleted = authed_client.delete(f"/api/preferences/{preference['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "deleted"

    assert (
        await db.fetchrow("SELECT count(*) AS n FROM preferences WHERE id = $1", preference["id"])
    )["n"] == 0
    rows = await db.fetch(
        """
        SELECT before, after FROM audit_log
        WHERE table_name = 'preferences' AND row_id = $1
        """,
        preference["id"],
    )
    assert len(rows) >= 2
    for row in rows:
        assert marker not in str(row["before"])
        assert marker not in str(row["after"])
