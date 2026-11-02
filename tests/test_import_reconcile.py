"""Dedupe, reconciliation, identifiers, outside text, and baselines."""

from __future__ import annotations

from datetime import date

from fin_agent.importers.baselines import recompute_baselines
from fin_agent.importers.identifiers import find_upi_ref, match_hash, scrub, scrub_account_number
from fin_agent.importers.reconcile import insert_lines, reconcile
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import new_id
from fin_agent.tools import write as write_tools


def _account(conn) -> dict:
    created = create_account(
        conn,
        kind="bank",
        name="SBI savings",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )
    return conn.execute("SELECT * FROM accounts WHERE id = %s", (created["id"],)).fetchone()


def _import(conn, account: dict) -> str:
    import_id = new_id(conn, "imp")
    conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             commit_groups)
        VALUES (%s, 'hash', 'review', %s, '2026-04-01', '2026-04-30', '[]'::jsonb)
        """,
        (import_id, account["id"]),
    )
    conn.commit()
    return import_id


def _parsed(row: dict) -> dict:
    return {"rows": [row], "headers": ["Date", "Narration", "Debit", "Credit", "Balance"]}


def test_identifiers_keep_only_last4_upi_ids_and_keyed_hashes():
    safe, hashes = scrub(
        "UPI/DR/123456789012/ramesh@okaxis/AC 50100123456789/other 912345678901",
        own_last4={"6789"},
    )
    assert "ramesh@okaxis" in safe
    assert "XXXX6789" in safe
    assert "912345678901" not in safe
    assert safe.count("ID-") == 1
    assert len(hashes) == 1
    assert find_upi_ref("UPI/DR/123456789012/x") == "123456789012"
    assert scrub_account_number("50100123456789") == "6789"
    assert match_hash("ABC 123") == match_hash("abc123")


def test_a_upi_reference_matches_certainly_and_the_statement_wins(ledger):
    account = _account(ledger)
    movement = movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="UPI swiggy",
        date=date(2026, 4, 1),
        capture={"kind": "chat", "ref": "msg_1", "upi_ref": "123456789012"},
    )
    ledger.commit()
    import_id = _import(ledger, account)
    insert_lines(
        ledger,
        import_id=import_id,
        account=account,
        parsed_account=_parsed(
            {
                "date": "2026-04-01",
                "description": "UPI/DR/123456789012/SWIGGY/ramesh@okaxis",
                "debit": 45000,
                "credit": 0,
                "balance": 24522890,
                "upi_ref": "123456789012",
            }
        ),
    )
    review = reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = ledger.execute(
        "SELECT * FROM statement_lines WHERE import_id = %s", (import_id,)
    ).fetchone()
    assert line["match_state"] == "certain"
    assert line["movement_id"] == movement["id"]
    assert line["outside_text"] is True
    assert review["counts"]["certain"] == 1


def test_two_candidates_are_ambiguous_and_logged_rows_are_listed(ledger):
    account = _account(ledger)
    for index in range(2):
        movements_mod.record_expense(
            ledger,
            account_id=account["id"],
            amount_paise=45000,
            description=f"swiggy {index}",
            date=date(2026, 4, 1),
            capture={"kind": "chat", "ref": f"msg_{index}"},
        )
    movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=90000,
        description="not on the statement",
        date=date(2026, 4, 20),
        capture={"kind": "chat", "ref": "msg_3"},
    )
    ledger.commit()
    import_id = _import(ledger, account)
    insert_lines(
        ledger,
        import_id=import_id,
        account=account,
        parsed_account=_parsed(
            {
                "date": "2026-04-02",
                "description": "UPI/SWIGGY",
                "debit": 45000,
                "credit": 0,
                "balance": 24522890,
            }
        ),
    )
    review = reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = ledger.execute(
        "SELECT * FROM statement_lines WHERE import_id = %s", (import_id,)
    ).fetchone()
    assert line["match_state"] == "ambiguous"
    assert review["counts"]["ambiguous"] == 1
    assert len(review["ambiguous"][0]["candidates"]) == 2
    assert review["counts"]["logged_not_on_statement"] == 1


def test_reimport_dedupes_by_content_hash(ledger):
    account = _account(ledger)
    first = _import(ledger, account)
    parsed = _parsed(
        {
            "date": "2026-04-01",
            "description": "SWIGGY",
            "debit": 45000,
            "credit": 0,
            "balance": 24522890,
        }
    )
    insert_lines(ledger, import_id=first, account=account, parsed_account=parsed)
    ledger.execute("UPDATE imports SET status = 'committed' WHERE id = %s", (first,))
    ledger.commit()
    second = _import(ledger, account)
    insert_lines(ledger, import_id=second, account=account, parsed_account=parsed)
    review = reconcile(
        ledger,
        import_id=second,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    assert review["counts"]["ignored"] == 1
    line = ledger.execute(
        "SELECT match_state FROM statement_lines WHERE import_id = %s", (second,)
    ).fetchone()
    assert line["match_state"] == "ignored"


def test_a_keyed_identifier_hash_matches_a_logged_payee(ledger):
    from fin_agent.importers.identifiers import match_hash
    from fin_agent.ledger import payees

    account = _account(ledger)
    payee = payees.create_payee(
        ledger,
        display_name="Ravi",
        match_hash=match_hash("912345678901"),
    )
    movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="NEFT to Ravi",
        payee_id=payee["id"],
        date=date(2026, 4, 2),
        capture={"kind": "chat", "ref": "msg_hash"},
    )
    ledger.commit()
    import_id = _import(ledger, account)
    insert_lines(
        ledger,
        import_id=import_id,
        account=account,
        parsed_account=_parsed(
            {
                "date": "2026-04-02",
                "description": "NEFT/912345678901/RAVI",
                "debit": 45000,
                "credit": 0,
                "balance": 24522890,
            }
        ),
    )
    reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = ledger.execute(
        "SELECT * FROM statement_lines WHERE import_id = %s", (import_id,)
    ).fetchone()
    assert line["match_state"] == "certain"
    assert (line["match_detail"] or {}).get("rule") == "identifier-hash"
    assert "912345678901" not in line["description_raw"]


def test_two_identical_rows_in_one_statement_are_kept(ledger):
    account = _account(ledger)
    import_id = _import(ledger, account)
    row = {
        "date": "2026-04-01",
        "description": "SMS CHARGE",
        "debit": 100,
        "credit": 0,
        "balance": 24522890,
    }
    lines = insert_lines(
        ledger,
        import_id=import_id,
        account=account,
        parsed_account={"rows": [row, dict(row)]},
    )
    assert len(lines) == 2
    review = reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    assert review["counts"]["new"] == 2


def test_baselines_are_computed_from_real_rows(ledger):
    account = _account(ledger)
    category = ledger.execute("SELECT id FROM categories WHERE name = 'Food & Dining'").fetchone()
    for day in (1, 5, 9):
        movements_mod.record_expense(
            ledger,
            account_id=account["id"],
            amount_paise=30000,
            description="swiggy",
            category_id=category["id"],
            date=date(2026, 4, day),
        )
    ledger.commit()
    result = recompute_baselines(ledger, as_of=date(2026, 4, 30))
    assert result["computed"] == 1
    row = ledger.execute(
        "SELECT * FROM baselines WHERE category_id = %s", (category["id"],)
    ).fetchone()
    assert row["source"] == "computed"
    assert row["amount_per_day_paise"] > 0
    assert row["window_days"] == 90


def test_import_document_returns_no_statement_text_and_enqueues_one_job(ledger):
    import hashlib

    from tests.import_fixtures import sbi_csv

    account = _account(ledger)
    del account
    payload = sbi_csv()
    attachment_id = new_id(ledger, "att")
    ledger.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES (%s, 'statement', 'sbi.csv', 'text/csv', %s, %s, %s)
        """,
        (attachment_id, len(payload), hashlib.sha256(payload).hexdigest(), payload),
    )
    ledger.execute("UPDATE attachments SET import_id = NULL")
    ledger.commit()
    result = write_tools.import_document(ledger, attachment_id=attachment_id)
    assert result["status"] == "parsing"
    assert result["import_id"].startswith("imp_")
    assert "SWIGGY" not in str(result)
    jobs = ledger.execute(
        "SELECT count(*) AS n FROM jobs WHERE kind = 'import_statement'"
    ).fetchone()
    assert jobs["n"] == 1
