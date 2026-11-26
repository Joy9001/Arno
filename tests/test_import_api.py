"""The imports REST surface."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from arno.importers.readers import is_locked
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from tests.fixtures import import_helpers as helpers
from tests.import_fixtures import sbi_csv

LOCKED_PDF = (Path(__file__).parent / "fixtures" / "locked-1234.pdf").read_bytes()


async def _seed_rule(conn, pattern: str, category: str = "Food & Dining") -> None:
    row = await conn.fetchrow("SELECT id FROM categories WHERE name = $1", category)
    await conn.execute(
        """
        INSERT INTO category_rules (match, pattern, category_id, source)
        VALUES ('equals', $1, $2, 'user') ON CONFLICT (match, pattern) DO NOTHING
        """,
        pattern,
        row["id"],
    )


async def _account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


async def test_upload_is_capped_and_accepts_a_statement(session_client, ledger):
    del ledger
    too_big = session_client.post(
        "/api/attachments",
        files={"file": ("big.bin", b"x" * (25 * 1024 * 1024 + 1), "application/octet-stream")},
    )
    assert too_big.status_code == 413

    upload = session_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    attachment = upload.json()
    assert attachment["detected_kind"] == "text"


async def test_delete_statement_file_purges_it(session_client, db):
    upload = session_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    attachment_id = upload.json()["id"]

    response = session_client.delete(f"/api/attachments/{attachment_id}")

    assert response.status_code == 200, response.text
    assert response.json()["purged"] is True
    row = await db.fetchrow("SELECT data, purged_at FROM attachments WHERE id = $1", attachment_id)
    assert row["data"] is None
    assert row["purged_at"] is not None


async def test_an_upload_opens_its_import_and_starts_extraction(session_client, db, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(
        "arno.agent.extractor.spawn", lambda coro, *, name: (coro.close(), started.append(name))
    )

    upload = session_client.post(
        "/api/attachments", files={"file": ("sbi.csv", sbi_csv(), "text/csv")}
    )

    assert upload.status_code == 201, upload.text
    import_id = upload.json()["import_id"]
    assert await db.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "parsing"
    assert started == [f"extract-{import_id}"]

    # The same bytes again land on the same import and start nothing new.
    again = session_client.post(
        "/api/attachments", files={"file": ("sbi.csv", sbi_csv(), "text/csv")}
    )
    assert again.json()["import_id"] == import_id
    assert len(started) == 1


async def test_a_photo_upload_opens_no_import(session_client, db):
    upload = session_client.post(
        "/api/attachments",
        files={"file": ("receipt.png", b"\x89PNG\r\n\x1a\nrest", "image/png")},
        data={"purpose": "photo"},
    )
    assert upload.status_code == 201, upload.text
    assert "import_id" not in upload.json()
    assert await db.fetchval("SELECT count(*) FROM imports") == 0


async def test_a_locked_upload_waits_for_its_password_and_the_right_one_unlocks_it(
    session_client, db, monkeypatch
):
    started: list[str] = []
    monkeypatch.setattr(
        "arno.agent.extractor.spawn", lambda coro, *, name: (coro.close(), started.append(name))
    )
    upload = session_client.post(
        "/api/attachments", files={"file": ("locked.pdf", LOCKED_PDF, "application/pdf")}
    )
    assert upload.status_code == 201, upload.text
    import_id = upload.json()["import_id"]
    assert (
        await db.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "needs_password"
    )
    assert started == []

    wrong = session_client.post(f"/api/imports/{import_id}/password", json={"password": "0000"})
    assert wrong.status_code == 422
    assert (
        await db.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "needs_password"
    )

    secret = "1234"
    right = session_client.post(f"/api/imports/{import_id}/password", json={"password": secret})
    assert right.status_code == 200, right.text
    assert right.json()["status"] == "parsing"
    assert started == [f"extract-{import_id}"]
    row = await db.fetchrow(
        "SELECT a.data, a.unlocked_at, i.* FROM imports i JOIN attachments a "
        "ON a.id = i.attachment_id WHERE i.id = $1",
        import_id,
    )
    assert row["unlocked_at"] is not None
    assert not is_locked(bytes(row["data"]))
    # The password is used once and kept nowhere.
    assert secret not in str({key: value for key, value in dict(row).items() if key != "data"})
    assert secret not in right.text

    # Nothing is waiting for a password any more.
    again = session_client.post(f"/api/imports/{import_id}/password", json={"password": secret})
    assert again.status_code == 409


async def test_retry_reads_a_failed_import_again(session_client, db, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(
        "arno.agent.extractor.spawn", lambda coro, *, name: (coro.close(), started.append(name))
    )
    import_id = await helpers.upload(db, sbi_csv())
    assert session_client.post(f"/api/imports/{import_id}/retry").status_code == 409

    await db.execute(
        "UPDATE imports SET status = 'failed', reason = 'The model could not read it.' "
        "WHERE id = $1",
        import_id,
    )
    detail = session_client.get(f"/api/imports/{import_id}").json()
    assert detail["can_retry_parse"] is True

    retried = session_client.post(f"/api/imports/{import_id}/retry")

    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "parsing"
    assert retried.json()["reason"] is None
    assert started == [f"extract-{import_id}"]
    assert session_client.post("/api/imports/imp_missing/retry").status_code == 404

    await db.execute(
        "UPDATE attachments SET data = NULL, purged_at = now() "
        "WHERE id IN (SELECT attachment_id FROM imports WHERE id = $1)",
        import_id,
    )
    await db.execute("UPDATE imports SET status = 'failed' WHERE id = $1", import_id)
    assert session_client.post(f"/api/imports/{import_id}/retry").status_code == 409


async def test_review_commit_and_undo_over_rest(session_client, ledger):
    account = await _account(ledger)
    for pattern in ("swiggy", "acme", "amazon"):
        await _seed_rule(ledger, pattern)
    payload = sbi_csv()
    attachment_id = await new_id(ledger, "att")
    await ledger.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', 'sbi.csv', 'text/csv', $2, $3, $4)
        """,
        attachment_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, account_id, commit_groups)
        VALUES ($1, 'hash-rest', 'parsing', $2, $3, '[]'::jsonb)
        """,
        import_id,
        attachment_id,
        account["id"],
    )

    await helpers.read_csv(ledger, import_id)

    detail = session_client.get(f"/api/imports/{import_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "review"

    committed = session_client.post(f"/api/imports/{import_id}/commit")
    assert committed.status_code == 200, committed.text
    assert committed.json()["status"] == "committed"

    undone = session_client.post(f"/api/imports/{import_id}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "rejected"

    history = session_client.get("/api/imports").json()
    assert history["items"][0]["import_id"] == import_id
