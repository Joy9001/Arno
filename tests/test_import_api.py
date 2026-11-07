"""The imports REST surface."""

from __future__ import annotations

import hashlib
from datetime import date

from fin_agent.importers.passwords import PASSWORDS
from fin_agent.importers.pipeline import _run_import
from fin_agent.importers.reconcile import insert_lines, reconcile, resolve_line
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import new_id
from tests.import_fixtures import SBI_PARSER, sbi_csv


async def _seed_rule(conn, pattern: str, category: str = "Food & Dining") -> None:
    row = await conn.fetchrow("SELECT id FROM categories WHERE name = $1", category)
    await conn.execute(
        """
        INSERT INTO category_rules (merchant_pattern, category_id, source)
        VALUES ($1, $2, 'user') ON CONFLICT (merchant_pattern) DO NOTHING
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


async def test_upload_is_capped_and_start_import_returns_202(authed_client, ledger):
    too_big = authed_client.post(
        "/api/attachments",
        files={"file": ("big.bin", b"x" * (25 * 1024 * 1024 + 1), "application/octet-stream")},
    )
    assert too_big.status_code == 413

    account = await _account(ledger)
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


async def _assumed_zero_card_review(conn, rows: list[dict], closing: int) -> tuple[str, dict]:
    card = await create_account(
        conn,
        kind="card",
        name="Slice card",
        institution="Slice",
        last4="4242",
        credit_limit_paise=500000,
        statement_day=25,
        due_day=5,
    )
    raw_account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", card["id"])
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             opening_balance_paise, closing_balance_paise, check_result,
                             commit_groups)
        VALUES ($1, 'hash-api-opening', 'review', $2, '2026-04-01', '2026-04-30',
                0, $3, $4, '[]'::jsonb)
        """,
        import_id,
        card["id"],
        closing,
        {
            "status": "VERIFIED",
            "opening_source": "printed",
            "opening_paise": 0,
            "closing_paise": closing,
            "chain_ok": None,
            "rows": len(rows),
        },
    )
    await insert_lines(
        conn,
        import_id=import_id,
        account=raw_account,
        parsed_account={"rows": rows},
    )
    await reconcile(
        conn,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    return import_id, card


async def test_opening_api_accepts_a_manual_zero_and_labels_it_manual(authed_client, ledger):
    import_id, _ = await _assumed_zero_card_review(
        ledger,
        [{"date": "2026-04-01", "description": "Blinkit", "debit": 42800, "credit": 0}],
        closing=-42800,
    )

    blocked = authed_client.post(f"/api/imports/{import_id}/commit")
    assert blocked.status_code == 409, blocked.text
    assert "opening" in blocked.json()["detail"].lower()

    response = authed_client.post(f"/api/imports/{import_id}/opening", json={"opening_paise": 0})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "review"
    assert body["check"] == "VERIFIED"
    assert body["opening_source"] == "manual"
    assert body["opening_paise"] == 0
    assert body["opening_evidenced"] is True
    assert body["opening_required"] is False

    committed = authed_client.post(f"/api/imports/{import_id}/commit")
    assert committed.status_code == 200, committed.text
    assert committed.json()["status"] == "committed"


async def test_opening_api_accepts_a_signed_card_balance_honestly(authed_client, ledger):
    import_id, _ = await _assumed_zero_card_review(
        ledger,
        [
            {"date": "2026-04-01", "description": "Blinkit", "debit": 42800, "credit": 0},
            {"date": "2026-04-02", "description": "Merchant refund", "debit": 0, "credit": 10395},
        ],
        closing=-132405,
    )

    response = authed_client.post(
        f"/api/imports/{import_id}/opening", json={"opening_paise": -100000}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["check"] == "VERIFIED"
    assert body["opening_source"] == "manual"
    assert body["opening_paise"] == -100000
    assert body["opening_required"] is False


async def test_opening_api_recheck_preserves_card_credit_decisions(authed_client, ledger):
    import_id, _ = await _assumed_zero_card_review(
        ledger,
        [
            {"date": "2026-04-01", "description": "Blinkit", "debit": 42800, "credit": 0},
            {"date": "2026-04-02", "description": "Merchant refund", "debit": 0, "credit": 10395},
        ],
        closing=-32405,
    )
    food = await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Food & Dining'")
    credit = await ledger.fetchrow(
        """
        SELECT id FROM statement_lines
        WHERE import_id = $1 AND amount_paise > 0
        """,
        import_id,
    )
    await resolve_line(
        ledger,
        import_id=import_id,
        line_id=credit["id"],
        resolution="classify_credit",
        credit_kind="refund",
        category_id=str(food["id"]),
    )

    response = authed_client.post(f"/api/imports/{import_id}/opening", json={"opening_paise": 0})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["check"] == "VERIFIED"
    assert body["opening_source"] == "manual"

    stored = await ledger.fetchrow(
        "SELECT match_state, movement_id, match_detail FROM statement_lines WHERE id = $1",
        credit["id"],
    )
    assert stored["match_state"] == "new"
    assert stored["movement_id"] is None
    assert stored["match_detail"]["credit_kind"] == "refund"
    assert stored["match_detail"]["credit_classified_by"] == "user"
