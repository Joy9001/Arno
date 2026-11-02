"""The imports REST surface."""

from __future__ import annotations

import hashlib
from datetime import date

from fin_agent.importers.passwords import PASSWORDS
from fin_agent.importers.pipeline import _run_import
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import new_id
from tests.import_fixtures import SBI_PARSER, sbi_csv


def _seed_rule(conn, pattern: str, category: str = "Food & Dining") -> None:
    row = conn.execute("SELECT id FROM categories WHERE name = %s", (category,)).fetchone()
    conn.execute(
        """
        INSERT INTO category_rules (merchant_pattern, category_id, source)
        VALUES (%s, %s, 'user') ON CONFLICT (merchant_pattern) DO NOTHING
        """,
        (pattern, row["id"]),
    )


def _account(conn) -> dict:
    return create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


def test_upload_is_capped_and_start_import_returns_202(authed_client, ledger):
    too_big = authed_client.post(
        "/api/attachments",
        files={"file": ("big.bin", b"x" * (25 * 1024 * 1024 + 1), "application/octet-stream")},
    )
    assert too_big.status_code == 413

    account = _account(ledger)
    upload = authed_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    attachment = upload.json()
    assert attachment["detected_kind"] == "text"

    started = authed_client.post(
        "/api/imports",
        json={"attachment_id": attachment["id"], "account_id": account["id"]},
    )
    assert started.status_code == 202, started.text
    payload = started.json()
    assert payload["status"] == "parsing"
    assert payload["lines"] == []

    repeated = authed_client.post(
        "/api/imports",
        json={"attachment_id": attachment["id"], "account_id": account["id"]},
    )
    assert repeated.status_code == 202
    assert repeated.json()["import_id"] == payload["import_id"]


def test_password_is_write_only_and_never_stored(authed_client, ledger):
    account = _account(ledger)
    payload = sbi_csv()
    attachment_id = new_id(ledger, "att")
    ledger.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES (%s, 'statement', 'sbi.csv', 'text/csv', %s, %s, %s)
        """,
        (attachment_id, len(payload), hashlib.sha256(payload).hexdigest(), payload),
    )
    import_id = new_id(ledger, "imp")
    ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, account_id, commit_groups)
        VALUES (%s, 'hash', 'needs_password', %s, %s, '[]'::jsonb)
        """,
        (import_id, attachment_id, account["id"]),
    )
    ledger.commit()

    secret = "ARJU0101-unique-secret"
    response = authed_client.post(f"/api/imports/{import_id}/password", json={"password": secret})
    assert response.status_code == 202, response.text
    assert secret not in response.text
    assert PASSWORDS.get(import_id) == secret

    row = ledger.execute("SELECT * FROM imports WHERE id = %s", (import_id,)).fetchone()
    assert secret not in str(dict(row))
    assert (
        ledger.execute(
            "SELECT count(*) AS n FROM jobs WHERE payload::text LIKE %s", (f"%{secret}%",)
        ).fetchone()["n"]
        == 0
    )
    assert authed_client.get(f"/api/imports/{import_id}/password").status_code == 405
    PASSWORDS.clear()


async def test_review_commit_and_undo_over_rest(authed_client, ledger):
    account = _account(ledger)
    for pattern in ("swiggy", "acme", "amazon"):
        _seed_rule(ledger, pattern)
    ledger.commit()
    payload = sbi_csv()
    attachment_id = new_id(ledger, "att")
    ledger.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES (%s, 'statement', 'sbi.csv', 'text/csv', %s, %s, %s)
        """,
        (attachment_id, len(payload), hashlib.sha256(payload).hexdigest(), payload),
    )
    import_id = new_id(ledger, "imp")
    ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, account_id, commit_groups)
        VALUES (%s, 'hash-rest', 'parsing', %s, %s, '[]'::jsonb)
        """,
        (import_id, attachment_id, account["id"]),
    )
    ledger.commit()
    ledger.execute(
        "UPDATE attachments SET import_id = %s WHERE id = %s", (import_id, attachment_id)
    )
    ledger.commit()

    async def fake_parse(doc, context, *, account_name=None):
        from fin_agent.agent.parse_run import ParseResult

        del doc, context, account_name
        return ParseResult(status="parser_submitted", script=SBI_PARSER)

    await _run_import(import_id, parser_runner=fake_parse)

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
