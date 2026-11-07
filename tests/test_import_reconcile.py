"""Dedupe, reconciliation, identifiers, outside text, and baselines."""

from __future__ import annotations

from datetime import date

from fin_agent.importers.baselines import recompute_baselines
from fin_agent.importers.identifiers import find_upi_ref, match_hash, scrub, scrub_account_number
from fin_agent.importers.reconcile import insert_lines, reconcile, resolve_line
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import new_id


async def _account(conn) -> dict:
    created = await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )
    return await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", created["id"])


async def _import(conn, account: dict) -> str:
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             commit_groups)
        VALUES ($1, 'hash', 'review', $2, '2026-04-01', '2026-04-30', '[]'::jsonb)
        """,
        import_id,
        account["id"],
    )
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


async def test_a_upi_reference_matches_certainly_and_the_statement_wins(ledger):
    account = await _account(ledger)
    movement = await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="UPI swiggy",
        date=date(2026, 4, 1),
        capture={"kind": "chat", "ref": "msg_1", "upi_ref": "123456789012"},
    )
    import_id = await _import(ledger, account)
    await insert_lines(
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
    review = await reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = await ledger.fetchrow("SELECT * FROM statement_lines WHERE import_id = $1", import_id)
    assert line["match_state"] == "certain"
    assert line["movement_id"] == movement["id"]
    assert line["outside_text"] is True
    assert review["counts"]["certain"] == 1


async def test_two_candidates_are_ambiguous_and_logged_rows_are_listed(ledger):
    account = await _account(ledger)
    for index in range(2):
        await movements_mod.record_expense(
            ledger,
            account_id=account["id"],
            amount_paise=45000,
            description=f"swiggy {index}",
            date=date(2026, 4, 1),
            capture={"kind": "chat", "ref": f"msg_{index}"},
        )
    await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=90000,
        description="not on the statement",
        date=date(2026, 4, 20),
        capture={"kind": "chat", "ref": "msg_3"},
    )
    import_id = await _import(ledger, account)
    await insert_lines(
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
    review = await reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = await ledger.fetchrow("SELECT * FROM statement_lines WHERE import_id = $1", import_id)
    assert line["match_state"] == "ambiguous"
    assert review["counts"]["ambiguous"] == 1
    assert len(review["ambiguous"][0]["candidates"]) == 2
    assert review["counts"]["logged_not_on_statement"] == 1


async def test_reimport_dedupes_by_content_hash(ledger):
    account = await _account(ledger)
    first = await _import(ledger, account)
    parsed = _parsed(
        {
            "date": "2026-04-01",
            "description": "SWIGGY",
            "debit": 45000,
            "credit": 0,
            "balance": 24522890,
        }
    )
    await insert_lines(ledger, import_id=first, account=account, parsed_account=parsed)
    await ledger.execute("UPDATE imports SET status = 'committed' WHERE id = $1", first)
    second = await _import(ledger, account)
    await insert_lines(ledger, import_id=second, account=account, parsed_account=parsed)
    review = await reconcile(
        ledger,
        import_id=second,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    assert review["counts"]["ignored"] == 1
    line = await ledger.fetchrow(
        "SELECT match_state FROM statement_lines WHERE import_id = $1", second
    )
    assert line["match_state"] == "ignored"


async def test_a_keyed_identifier_hash_matches_a_logged_payee(ledger):
    from fin_agent.importers.identifiers import match_hash
    from fin_agent.ledger import payees

    account = await _account(ledger)
    payee = await payees.create_payee(
        ledger,
        display_name="Ravi",
        match_hash=match_hash("912345678901"),
    )
    await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="NEFT to Ravi",
        payee_id=payee["id"],
        date=date(2026, 4, 2),
        capture={"kind": "chat", "ref": "msg_hash"},
    )
    import_id = await _import(ledger, account)
    await insert_lines(
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
    await reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = await ledger.fetchrow("SELECT * FROM statement_lines WHERE import_id = $1", import_id)
    assert line["match_state"] == "certain"
    assert (line["match_detail"] or {}).get("rule") == "identifier-hash"
    assert "912345678901" not in line["description_raw"]


async def test_counterparty_survives_insert_and_reconcile(ledger):
    account = await _account(ledger)
    import_id = await _import(ledger, account)
    await insert_lines(
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
                "counterparty": "Swiggy",
            }
        ),
    )
    await reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = await ledger.fetchrow("SELECT * FROM statement_lines WHERE import_id = $1", import_id)
    assert line["match_state"] == "new"
    assert line["match_detail"]["counterparty"] == "Swiggy"
    assert "identifier_hashes" in line["match_detail"]
    assert "candidates" in line["match_detail"]


async def test_both_sides_of_one_transfer_match_their_own_statement(ledger):
    bank = await _account(ledger)
    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        last4="4242",
        opening_balance_paise=0,
        opening_balance_as_of=date(2026, 3, 31),
        credit_limit_paise=500000,
        statement_day=25,
        due_day=5,
    )
    card_row = await ledger.fetchrow("SELECT * FROM accounts WHERE id = $1", card["id"])
    transfer = await movements_mod.record_transfer(
        ledger,
        from_account_id=bank["id"],
        to_account_id=card["id"],
        amount_paise=42800,
        description="Card payment",
        date=date(2026, 4, 1),
    )
    bank_import = await _import(ledger, bank)
    await insert_lines(
        ledger,
        import_id=bank_import,
        account=bank,
        parsed_account=_parsed(
            {
                "date": "2026-04-01",
                "description": "UPI/DR/123456789012/SLICE CARD/self",
                "debit": 42800,
                "credit": 0,
                "balance": 24525090,
            }
        ),
    )
    await reconcile(
        ledger,
        import_id=bank_import,
        account=bank,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    bank_line = await ledger.fetchrow(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1",
        bank_import,
    )
    assert bank_line["match_state"] == "certain"
    assert bank_line["movement_id"] == transfer["id"]

    card_import = await _import(ledger, card_row)
    await insert_lines(
        ledger,
        import_id=card_import,
        account=card_row,
        parsed_account=_parsed(
            {
                "date": "2026-04-02",
                "description": "Repayment",
                "debit": 0,
                "credit": 42800,
                "balance": None,
            }
        ),
    )
    await reconcile(
        ledger,
        import_id=card_import,
        account=card_row,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    card_line = await ledger.fetchrow(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1",
        card_import,
    )
    assert card_line["match_state"] == "certain"
    assert card_line["movement_id"] == transfer["id"]


async def test_a_suggested_transfer_is_confirmed_or_rejected_by_the_user(ledger):
    account = await _account(ledger)
    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        last4="4242",
        opening_balance_paise=0,
        opening_balance_as_of=date(2026, 3, 31),
        credit_limit_paise=1500000,
        statement_day=25,
        due_day=9,
    )
    import_id = await _import(ledger, account)
    await insert_lines(
        ledger,
        import_id=import_id,
        account=account,
        parsed_account={
            "rows": [
                {
                    "date": "2026-04-01",
                    "description": "SLICE CARD",
                    "debit": 45000,
                    "credit": 0,
                    "balance": 24522890,
                },
                {
                    "date": "2026-04-02",
                    "description": "SLICE CARD",
                    "debit": 20000,
                    "credit": 0,
                    "balance": 24502890,
                },
            ]
        },
    )
    lines = await ledger.fetch(
        "SELECT id FROM statement_lines WHERE import_id = $1 ORDER BY date", import_id
    )
    for line in lines:
        await ledger.execute(
            "UPDATE statement_lines SET match_detail = $1 WHERE id = $2",
            {"suggested_transfer_account_id": card["id"]},
            line["id"],
        )

    confirmed = await resolve_line(
        ledger,
        import_id=import_id,
        line_id=lines[0]["id"],
        resolution="confirm_transfer",
    )
    assert confirmed["match_detail"]["transfer_confirmed"] is True
    rejected = await resolve_line(
        ledger,
        import_id=import_id,
        line_id=lines[1]["id"],
        resolution="reject_transfer",
    )
    assert "suggested_transfer_account_id" not in rejected["match_detail"]


async def test_two_identical_rows_in_one_statement_are_kept(ledger):
    account = await _account(ledger)
    import_id = await _import(ledger, account)
    row = {
        "date": "2026-04-01",
        "description": "SMS CHARGE",
        "debit": 100,
        "credit": 0,
        "balance": 24522890,
    }
    lines = await insert_lines(
        ledger,
        import_id=import_id,
        account=account,
        parsed_account={"rows": [row, dict(row)]},
    )
    assert len(lines) == 2
    review = await reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    assert review["counts"]["new"] == 2


async def test_baselines_are_computed_from_real_rows(ledger):
    account = await _account(ledger)
    category = await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Food & Dining'")
    for day in (1, 5, 9):
        await movements_mod.record_expense(
            ledger,
            account_id=account["id"],
            amount_paise=30000,
            description="swiggy",
            category_id=category["id"],
            date=date(2026, 4, day),
        )
    result = await recompute_baselines(ledger, as_of=date(2026, 4, 30))
    assert result["computed"] == 1
    row = await ledger.fetchrow("SELECT * FROM baselines WHERE category_id = $1", category["id"])
    assert row["source"] == "computed"
    assert row["amount_per_day_paise"] > 0
    assert row["window_days"] == 90
