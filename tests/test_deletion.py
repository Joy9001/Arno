"""The deletion preview a request shows, and the erase-everything it warns about (C21 box 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai_harness.subagents import DelegationTask

from arno.ledger import deletion
from tests.fixtures.passkeys import complete_setup
from tests.invariants import check_ledger
from tests.software_authenticator import SoftwareAuthenticator


@pytest.fixture(autouse=True)
def _task_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Give every test in this module its own parser task folder."""
    folder = tmp_path / "tasks"
    monkeypatch.setenv("ARNO_TASKS_DIR", str(folder))
    return folder


def _counts(payload: dict) -> dict[str, int]:
    return {row["table"]: row["count"] for row in payload["removes"]}


def _save(tasks, task_id: str, text: str, *, running: bool = False) -> None:
    record = DelegationTask(
        id=task_id,
        agent_name="parser",
        prompt=text,
        conversation_id="conv_deletion",
        status="running" if running else "finished",
        started_at=1000.0,
        finished_at=None if running else 1010.0,
    )
    tasks.records[task_id] = record


async def _save_on_disk(tasks, task_id: str, text: str, *, running: bool = False) -> None:
    _save(tasks, task_id, text, running=running)
    await tasks.save(tasks.records[task_id])


async def _finish_jobs(conn) -> None:
    """Do the work the runner would have done, so an erase is not blocked by the queue."""
    await conn.execute(
        """
        UPDATE jobs SET status = 'done', finished_at = now()
        WHERE status IN ('pending', 'running')
        """
    )


async def test_the_everything_preview_counts_exactly_what_the_erase_removes(demo_ledger):
    await _finish_jobs(demo_ledger)
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
    await _finish_jobs(demo_ledger)
    await deletion.erase_everything(demo_ledger)

    after = await deletion.preview(demo_ledger, "everything")

    assert after["removes"] == []
    assert await check_ledger(demo_ledger) == []
    assert await demo_ledger.fetchval("SELECT count(*) FROM movements") == 0
    assert await demo_ledger.fetchval("SELECT count(*) FROM preferences") == 0
    assert await demo_ledger.fetchval("SELECT count(*) FROM conversations") == 0


async def test_the_erase_keeps_the_passkey_and_session_so_the_owner_still_gets_in(
    authed_client, demo_ledger
):
    await _finish_jobs(demo_ledger)
    erased = authed_client.post("/api/deletion/everything", json={"confirm": "delete everything"})
    assert erased.status_code == 200, erased.text
    assert erased.json()["total"] > 0

    assert authed_client.get("/api/accounts").status_code == 200
    assert authed_client.get("/api/settings").status_code == 200
    assert await demo_ledger.fetchval("SELECT count(*) FROM users") == 1
    assert await demo_ledger.fetchval("SELECT count(*) FROM webauthn_credentials") >= 1
    assert await demo_ledger.fetchval("SELECT count(*) FROM sessions") >= 1
    assert await check_ledger(demo_ledger) == []


async def test_the_everything_preview_counts_parser_task_records_and_the_erase_removes_them(
    authed_client, db, _task_folder
):
    tasks = authed_client.app.state.tasks
    assert tasks.directory == _task_folder
    await _save_on_disk(tasks, "a" * 32, "HDFC statement, salary 100000")
    await _save_on_disk(tasks, "b" * 32, "SBI statement, rent 25000")
    assert "HDFC statement" in (_task_folder / f"{'a' * 32}.json").read_text(encoding="utf-8")
    await _finish_jobs(db)

    preview = authed_client.get("/api/deletion/preview?scope=everything")

    assert preview.status_code == 200, preview.text
    assert {"table": "parser_task_records", "count": 2} in preview.json()["removes"]
    erased = authed_client.post("/api/deletion/everything", json={"confirm": "delete everything"})
    assert erased.status_code == 200, erased.text
    assert erased.json()["removes"] == preview.json()["removes"]
    assert erased.json()["total"] == sum(row["count"] for row in preview.json()["removes"])
    assert not list(_task_folder.glob("*.json"))
    assert tasks.records == {}


async def test_a_running_parser_task_blocks_the_erase_until_the_task_finishes(
    authed_client, db, _task_folder
):
    tasks = authed_client.app.state.tasks
    await _save_on_disk(tasks, "c" * 32, "Union Bank statement", running=True)
    await db.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('Takes the 7:10 train', 'user')"
    )
    before = _counts(await deletion.preview(db, "everything"))

    refused = authed_client.post("/api/deletion/everything", json={"confirm": "delete everything"})

    assert refused.status_code == 409, refused.text
    assert "statement import is still running" in refused.json()["detail"]
    assert _counts(await deletion.preview(db, "everything")) == before
    assert [path.name for path in _task_folder.glob("*.json")] == [f"{'c' * 32}.json"]

    record = tasks.records["c" * 32]
    record.status = "finished"
    record.finished_at = 1010.0
    await tasks.save(record)
    await _finish_jobs(db)

    erased = authed_client.post("/api/deletion/everything", json={"confirm": "delete everything"})
    assert erased.status_code == 200, erased.text
    assert not list(_task_folder.glob("*.json"))


async def test_a_running_job_blocks_the_erase_until_it_finishes(authed_client, db):
    await db.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('Takes the 7:10 train', 'user')"
    )
    await db.execute(
        "INSERT INTO jobs (key, kind, status) VALUES ('deletion-test:running', 'risk_check', "
        "'running')"
    )
    before = _counts(await deletion.preview(db, "everything"))

    refused = authed_client.post("/api/deletion/everything", json={"confirm": "delete everything"})

    assert refused.status_code == 409, refused.text
    assert "running" in refused.json()["detail"]
    assert _counts(await deletion.preview(db, "everything")) == before

    await _finish_jobs(db)

    erased = authed_client.post("/api/deletion/everything", json={"confirm": "delete everything"})
    assert erased.status_code == 200, erased.text
    assert await db.fetchval("SELECT count(*) FROM jobs") == 0
    assert await db.fetchval("SELECT count(*) FROM preferences") == 0


async def test_a_queued_job_does_not_block_the_erase_and_is_erased_with_the_rest(authed_client, db):
    await db.execute(
        "INSERT INTO jobs (key, kind, status) VALUES ('deletion-test:queued', 'risk_check', "
        "'pending')"
    )

    erased = authed_client.post("/api/deletion/everything", json={"confirm": "delete everything"})

    assert erased.status_code == 200, erased.text
    assert await db.fetchval("SELECT count(*) FROM jobs WHERE key = 'deletion-test:queued'") == 0


async def test_a_stale_passkey_session_is_refused_like_an_export_and_deletes_nothing(
    client, setup_token, settings, demo_ledger
):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200
    before = _counts(await deletion.preview(demo_ledger, "everything"))

    refused = client.post("/api/deletion/everything", json={"confirm": "delete everything"})

    assert refused.status_code == 403
    assert refused.headers["x-arno-step-up"] == "passkey"
    assert _counts(await deletion.preview(demo_ledger, "everything")) == before


async def test_a_wrong_confirmation_phrase_is_refused_and_the_ledger_is_unchanged(
    authed_client, demo_ledger
):
    before = _counts(await deletion.preview(demo_ledger, "everything"))

    refused = authed_client.post("/api/deletion/everything", json={"confirm": "delete all"})

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


async def test_the_preview_route_answers_for_one_item_and_refuses_an_unknown_one(authed_client, db):
    memory = await db.fetchrow(
        "INSERT INTO preferences (body, created_by) VALUES ('Takes the 7:10 train', 'user') "
        "RETURNING *"
    )

    found = authed_client.get(f"/api/deletion/preview?scope=memory&id={memory['id']}")

    assert found.status_code == 200, found.text
    assert _counts(found.json()) == {"preferences": 1}
    missing = authed_client.get("/api/deletion/preview?scope=memory&id=prf_missing")
    assert missing.status_code == 404
