"""The imports REST surface."""

from __future__ import annotations

import hashlib
from datetime import date

from arno.agent.parser import submit_parser_code
from arno.importers.passwords import PASSWORDS
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from tests.import_fixtures import SBI_PARSER, sbi_csv


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


async def test_upload_is_capped_and_accepts_a_statement(authed_client, ledger):
    del ledger
    too_big = authed_client.post(
        "/api/attachments",
        files={"file": ("big.bin", b"x" * (25 * 1024 * 1024 + 1), "application/octet-stream")},
    )
    assert too_big.status_code == 413

    upload = authed_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    attachment = upload.json()
    assert attachment["detected_kind"] == "text"


async def test_delete_statement_file_purges_it(authed_client, db):
    upload = authed_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    attachment_id = upload.json()["id"]

    response = authed_client.delete(f"/api/attachments/{attachment_id}")

    assert response.status_code == 200, response.text
    assert response.json()["purged"] is True
    row = await db.fetchrow("SELECT data, purged_at FROM attachments WHERE id = $1", attachment_id)
    assert row["data"] is None
    assert row["purged_at"] is not None


async def test_password_is_write_only_and_never_stored(authed_client, ledger):
    account = await _account(ledger)
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
        VALUES ($1, 'hash', 'needs_password', $2, $3, '[]'::jsonb)
        """,
        import_id,
        attachment_id,
        account["id"],
    )

    secret = "ARJU0101-unique-secret"
    response = authed_client.post(f"/api/imports/{import_id}/password", json={"password": secret})
    assert response.status_code == 202, response.text
    assert secret not in response.text
    assert PASSWORDS.get(import_id) == secret

    row = await ledger.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    assert secret not in str(dict(row))
    assert (
        await ledger.fetchval(
            "SELECT count(*) AS n FROM jobs WHERE payload::text LIKE $1", f"%{secret}%"
        )
        == 0
    )
    assert authed_client.get(f"/api/imports/{import_id}/password").status_code == 405
    PASSWORDS.clear()


async def test_review_commit_and_undo_over_rest(authed_client, ledger):
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
    await ledger.execute(
        "UPDATE attachments SET import_id = $1 WHERE id = $2", import_id, attachment_id
    )

    await submit_parser_code(ledger, import_ids=[import_id], code=SBI_PARSER)

    detail = authed_client.get(f"/api/imports/{import_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "review"

    committed = authed_client.post(f"/api/imports/{import_id}/commit")
    assert committed.status_code == 200, committed.text
    assert committed.json()["status"] == "committed"

    undone = authed_client.post(f"/api/imports/{import_id}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "rejected"

    history = authed_client.get("/api/imports").json()
    assert history["items"][0]["import_id"] == import_id
