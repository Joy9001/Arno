"""The deletion preview a request shows, and the erase-everything it warns about (C21 box 5)."""

from __future__ import annotations

import json

from arno.db.conn import connect
from arno.ledger import deletion
from tests.conftest import _dsn
from tests.invariants import check_ledger


def _counts(payload: dict) -> dict[str, int]:
    return {row["table"]: row["count"] for row in payload["removes"]}


async def test_the_everything_preview_counts_exactly_what_the_erase_removes(demo_ledger):
    preview = await deletion.preview(demo_ledger, "everything")

    result = await deletion.erase_everything(demo_ledger)

    assert result["removes"] == preview["removes"]
    assert result["total"] == sum(row["count"] for row in preview["removes"])
    counts = _counts(preview)
    assert counts["movements"] > 0
    assert counts["payees"] > 0
    assert counts["accounts"] > 3, "the demo's own accounts count; the system three do not"
    assert counts["audit_log"] > 0


async def test_after_the_erase_nothing_remains_to_preview_and_the_ledger_still_checks(demo_ledger):
    await deletion.erase_everything(demo_ledger)

    after = await deletion.preview(demo_ledger, "everything")

    assert after["removes"] == []
    assert await check_ledger(demo_ledger) == []
    assert await demo_ledger.fetchval("SELECT count(*) FROM movements") == 0
    assert await demo_ledger.fetchval("SELECT count(*) FROM preferences") == 0
    assert await demo_ledger.fetchval("SELECT count(*) FROM conversations") == 0


async def test_the_erase_keeps_the_owner_signed_in_without_a_step_up(session_client, demo_ledger):
    erased = session_client.post("/api/deletion/everything", json={"confirm": "delete everything"})
    assert erased.status_code == 200, erased.text
    assert erased.json()["total"] > 0

    assert session_client.get("/api/accounts").status_code == 200
    assert session_client.get("/api/settings").status_code == 200
    assert await check_ledger(demo_ledger) == []


async def test_a_running_task_blocks_the_erase_until_it_finishes(session_client, db):
    await db.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('Takes the 7:10 train', 'user')"
    )
    before = _counts(await deletion.preview(db, "everything"))

    # A spawned background task holds this lock shared while it runs.
    async with connect(_dsn()) as task:
        await task.execute("SELECT pg_advisory_lock_shared($1)", deletion.ERASE_LOCK_KEY)
        refused = session_client.post(
            "/api/deletion/everything", json={"confirm": "delete everything"}
        )

        assert refused.status_code == 409, refused.text
        assert "running" in refused.json()["detail"]
        assert _counts(await deletion.preview(db, "everything")) == before

    erased = session_client.post("/api/deletion/everything", json={"confirm": "delete everything"})
    assert erased.status_code == 200, erased.text
    assert await db.fetchval("SELECT count(*) FROM preferences") == 0


async def test_a_wrong_confirmation_phrase_is_refused_and_the_ledger_is_unchanged(
    session_client, demo_ledger
):
    before = _counts(await deletion.preview(demo_ledger, "everything"))

    refused = session_client.post("/api/deletion/everything", json={"confirm": "delete all"})

    assert refused.status_code == 400, refused.text
    assert _counts(await deletion.preview(demo_ledger, "everything")) == before


def test_the_deletion_routes_need_a_session(client):
    assert client.get("/api/deletion/preview").status_code == 401
    assert (
        client.post("/api/deletion/everything", json={"confirm": "delete everything"}).status_code
        == 401
    )


async def test_the_preview_names_a_remote_provider_without_any_key_and_a_local_one_as_local(
    monkeypatch, db
):
    monkeypatch.setenv("CHAT_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("CHAT_MODEL", "example-chat")
    monkeypatch.setenv("CHAT_PROVIDER", "example")
    monkeypatch.setenv("CHAT_API_KEY", "sk-live-must-never-be-echoed")

    preview = await deletion.preview(db, "everything")

    text = json.dumps(preview)
    assert "sk-live-must-never-be-echoed" not in text
    roles = {item["role"]: item for item in preview["providers"]}
    assert roles["chat"]["provider"] == "example"
    assert roles["chat"]["host"] == "api.example.com"
    assert "cannot be recalled by Arno" in roles["chat"]["note"]
    assert roles["vision"]["host"] == "127.0.0.1"
    assert "stays on this machine" in roles["vision"]["note"]
    assert "stays on this machine" in roles["stt"]["note"]


async def test_the_preview_states_the_backup_window_and_that_no_purge_date_is_guaranteed(db):
    preview = await deletion.preview(db, "everything")

    policy = preview["backups"]["policy"]
    note = preview["backups"]["note"]
    assert "keep-daily 7" in policy
    assert "keep-weekly 4" in policy
    assert "no guaranteed purge date" in note
    assert "about 4 weeks" in note
    assert "off-site snapshots" in note


async def test_the_memory_preview_counts_the_row_and_names_the_scrubbed_audit_rows(db):
    memory = await db.fetchrow(
        "INSERT INTO preferences (body, created_by) VALUES ('Keeps filter coffee', 'user') "
        "RETURNING *"
    )
    await db.execute(
        "UPDATE preferences SET body = 'Keeps filter coffee, no sugar' WHERE id = $1",
        memory["id"],
    )

    preview = await deletion.preview(db, "memory", memory["id"])

    assert _counts(preview) == {"preferences": 1}
    assert any("audit rows" in item["what"] for item in preview["retains"])
    assert any("chat history" in item["why"] for item in preview["retains"])


async def test_the_statement_preview_counts_the_stored_file_and_names_what_survives(db):
    attachment = await db.fetchrow(
        """
        INSERT INTO attachments (kind, filename, content_type, size_bytes, sha256, data)
        VALUES ('statement', 'hdfc.pdf', 'application/pdf', 3, 'abc123', $1::bytea)
        RETURNING id
        """,
        b"PDF",
    )
    import_id = await db.fetchval(
        "INSERT INTO imports (file_hash, attachment_id) VALUES ('hash-1', $1) RETURNING id",
        attachment["id"],
    )

    preview = await deletion.preview(db, "statement", import_id)

    assert _counts(preview) == {"attachments": 1}
    retains = " ".join(f"{item['what']} {item['why']}" for item in preview["retains"])
    assert "statement lines" in retains
    assert "Undo import" in retains


async def test_the_preview_route_answers_for_one_item_and_refuses_an_unknown_one(
    session_client, db
):
    memory = await db.fetchrow(
        "INSERT INTO preferences (body, created_by) VALUES ('Takes the 7:10 train', 'user') "
        "RETURNING *"
    )

    found = session_client.get(f"/api/deletion/preview?scope=memory&id={memory['id']}")

    assert found.status_code == 200, found.text
    assert _counts(found.json()) == {"preferences": 1}
    missing = session_client.get("/api/deletion/preview?scope=memory&id=prf_missing")
    assert missing.status_code == 404
