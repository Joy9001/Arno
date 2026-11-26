"""Dedupe, reconciliation, identifiers, outside text, and baselines."""

from __future__ import annotations

from datetime import date

from arno.importers.baselines import recompute_baselines
from arno.importers.commit import commit_import, reject_import, undo_import
from arno.importers.identifiers import find_upi_ref, match_hash, scrub, scrub_account_number
from arno.importers.reconcile import insert_lines, reconcile, resolve_line
from arno.ledger import movements as movements_mod
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from arno.ledger.schedule_payments import link_payment
from arno.ledger.schedules import create_schedule
from arno.tools.projection import load_inputs
from tests.fixtures import import_helpers as helpers
from tests.import_fixtures import wallet_statement


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


async def _counts(conn, import_id: str) -> dict:
    row = await conn.fetchrow("SELECT counts FROM imports WHERE id = $1", import_id)
    return dict(row["counts"] or {})


def _parsed(row: dict) -> dict:
    return {"rows": [row], "headers": ["Date", "Narration", "Debit", "Credit", "Balance"]}


def test_identifiers_keep_only_last4_upi_ids_and_keyed_hashes():
    safe, hashes = scrub(
        "UPI/DR/123456789012/ramesh@okaxis/AC 50100123456789/other 912345678901",
        own_last4={"6789"},
    )
    assert safe is not None
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
    await reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    line = await ledger.fetchrow("SELECT * FROM statement_lines WHERE import_id = $1", import_id)
    assert line["match_state"] == "certain"
    assert line["movement_id"] == movement["id"]
    assert line["outside_text"] is True
    assert (await _counts(ledger, import_id))["certain"] == 1


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
    assert (await _counts(ledger, import_id))["ambiguous"] == 1
    assert len(review["ambiguous"][0]["candidates"]) == 2
    assert (await _counts(ledger, import_id))["logged_not_on_statement"] == 1


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
    await reconcile(
        ledger,
        import_id=second,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    assert (await _counts(ledger, second))["ignored"] == 1
    line = await ledger.fetchrow(
        "SELECT match_state FROM statement_lines WHERE import_id = $1", second
    )
    assert line["match_state"] == "ignored"


async def test_a_keyed_identifier_hash_matches_a_logged_payee(ledger):
    from arno.importers.identifiers import match_hash
    from arno.ledger import payees

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
    await reconcile(
        ledger,
        import_id=import_id,
        account=account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    assert (await _counts(ledger, import_id))["new"] == 2


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
    # The ledger covers March 31 to April 30: 30 days, not the nominal 90.
    assert row["window_days"] == 30


async def test_a_schedule_paid_rent_is_left_out_of_the_baseline(ledger):
    account = await create_account(
        ledger,
        kind="bank",
        name="Rent bank",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=date(2026, 4, 1),
    )
    rent = await ledger.fetchrow("SELECT id FROM categories WHERE name = 'Rent'")
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="House rent",
        amount_paise=2_800_000,
        account_id=account["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 4, 5),
    )
    paid = await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=2_800_000,
        description="House rent",
        category_id=rent["id"],
        date=date(2026, 4, 5),
    )
    await link_payment(
        ledger, schedule_id=schedule["id"], due_date=date(2026, 4, 5), movement_id=paid["id"]
    )
    for day, amount in ((6, 50_000), (7, 40_000), (8, 30_000)):
        await movements_mod.record_expense(
            ledger,
            account_id=account["id"],
            amount_paise=amount,
            description="Flat maintenance",
            category_id=rent["id"],
            date=date(2026, 4, day),
        )
    repair = await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=1_200_000,
        description="Roof repair",
        category_id=rent["id"],
        date=date(2026, 4, 12),
    )
    await movements_mod.correct_movement(ledger, repair["id"], changes={"one_off": True})

    result = await recompute_baselines(ledger, as_of=date(2026, 4, 30))

    assert result["computed"] == 1
    row = await ledger.fetchrow("SELECT * FROM baselines WHERE category_id = $1", rent["id"])
    assert row["source"] == "computed"
    assert row["window_days"] == 29
    # Only the 120,000 of maintenance: the rent paid a schedule and the repair is marked one-off.
    assert row["amount_per_day_paise"] == round(120_000 / 29)
    # The projection derives its own baseline from the same rows, so the two must agree.
    inputs = await load_inputs(ledger, as_of=date(2026, 4, 30))
    simulated = next(category for category in inputs.categories if category.name == "Rent")
    assert simulated.baseline_per_day_paise == row["amount_per_day_paise"]


# A second copy of a row never books twice, and a released row comes back ---------------------


async def test_the_same_row_on_two_accounts_is_not_a_duplicate(ledger):
    first = await helpers.bank(ledger, name="SBI savings", last4="6789")
    second = await helpers.bank(ledger, name="Axis savings", last4="1111")
    row = helpers.row("SWIGGY", debit=45000)
    original = await helpers.review_import(ledger, first, [row])
    await commit_import(ledger, original)

    import_id = await helpers.review_import(ledger, second, [row])

    line = await ledger.fetchrow(
        "SELECT match_state FROM statement_lines WHERE import_id = $1", import_id
    )
    assert line["match_state"] == "new"
    await commit_import(ledger, import_id)
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 2


async def test_a_near_duplicate_merges_and_two_identical_purchases_both_stay(ledger):
    account = await helpers.bank(ledger)
    logged = await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="swiggy",
        date=date(2026, 4, 2),
        capture={"kind": "chat", "ref": "msg_1"},
    )
    import_id = await helpers.review_import(
        ledger,
        account,
        [
            # Different payee spelling, one day off: still the same movement.
            helpers.row(
                "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
                debit=45000,
                day="2026-04-03",
            ),
            # A second, genuinely separate purchase of the same amount the same day.
            helpers.row(
                "UPI/DR/123456789013/SWIGGY BANGALORE/ramesh@okaxis",
                debit=45000,
                day="2026-04-03",
            ),
        ],
    )

    states = [
        row["match_state"]
        for row in await ledger.fetch(
            "SELECT match_state FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
            import_id,
        )
    ]
    assert states == ["certain", "new"]
    await commit_import(ledger, import_id)
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 2
    assert (
        await ledger.fetchval("SELECT count(*) FROM captures WHERE movement_id = $1", logged["id"])
        == 2
    )


async def test_an_overlapping_reimport_of_the_same_account_ignores_the_second_copy(ledger):
    account = await helpers.bank(ledger)
    row = helpers.row("SWIGGY", debit=45000)
    first = await helpers.review_import(ledger, account, [row])
    await commit_import(ledger, first)

    overlapping = await helpers.review_import(
        ledger, account, [row, helpers.row("BLINKIT", debit=20000, day="2026-04-20")]
    )

    counts = (await ledger.fetchrow("SELECT counts FROM imports WHERE id = $1", overlapping))[
        "counts"
    ]
    assert counts["ignored"] == 1
    assert counts["new"] == 1


async def test_rejecting_an_import_releases_the_row_it_suppressed(ledger):
    bank = await helpers.bank(ledger)
    first = await helpers.queued_import(
        ledger, bank, [helpers.row("SWIGGY BENGALURU", debit=45000)]
    )
    second = await helpers.queued_import(
        ledger,
        bank,
        [
            helpers.row("SWIGGY BENGALURU", debit=45000),
            helpers.row("BLINKIT", debit=20000, day="2026-04-02"),
        ],
    )
    assert await _states(ledger, second) == ["ignored", "new"]
    kept = await _nth_line(ledger, second, 1)
    await resolve_line(ledger, import_id=second, line_id=kept["id"], resolution="ignore")
    assert await _states(ledger, second) == ["ignored", "ignored"]

    await reject_import(ledger, first, "not my statement")

    assert await _states(ledger, second) == ["new", "ignored"]
    counts = await ledger.fetchrow("SELECT counts FROM imports WHERE id = $1", second)
    assert counts["counts"]["new"] == 1
    assert counts["counts"]["ignored"] == 1


async def test_undoing_an_import_releases_the_row_it_suppressed(ledger):
    bank = await helpers.bank(ledger)
    committed = await helpers.review_import(
        ledger, bank, [helpers.row("SWIGGY BENGALURU", debit=45000)]
    )
    await commit_import(ledger, committed)
    waiting = await helpers.queued_import(
        ledger, bank, [helpers.row("SWIGGY BENGALURU", debit=45000)]
    )
    assert await _states(ledger, waiting) == ["ignored"]

    await undo_import(ledger, committed)

    assert await _states(ledger, waiting) == ["new"]


async def _states(conn, import_id: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT match_state FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
        import_id,
    )
    return [row["match_state"] for row in rows]


async def _nth_line(conn, import_id: str, index: int) -> dict:
    return (
        await conn.fetch(
            "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id", import_id
        )
    )[index]


async def test_a_reversed_logged_row_does_not_absorb_a_statement_line(ledger):
    from arno.ledger.audit import undo_group

    account = await helpers.bank(ledger)
    logged = await movements_mod.record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=45000,
        description="swiggy",
        date=date(2026, 4, 2),
        capture={"kind": "chat", "ref": "msg_1"},
    )
    deleted = await movements_mod.correct_movement(ledger, logged["id"], changes={"delete": True})
    import_id = await helpers.review_import(
        ledger, account, [helpers.row("SWIGGY", debit=45000, day="2026-04-03")]
    )

    line = await ledger.fetchrow(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    # The logged row was undone, so the statement's row is new money, not a merge.
    assert line["match_state"] == "new"
    assert line["movement_id"] is None

    # An undo of that undo makes the logged row effective again, and then it matches.
    await undo_group(ledger, deleted["undo_id"])
    await reconcile(ledger, import_id=import_id, account=account, period=helpers.PERIOD)
    line = await ledger.fetchrow(
        "SELECT match_state, movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    assert line["match_state"] == "certain"
    assert line["movement_id"] == logged["id"]


async def test_confirming_a_matched_transfer_keeps_its_one_movement(ledger):
    from arno.importers.reconcile import resolve_line

    here = await create_account(
        ledger,
        kind="bank",
        name="SBI Salary",
        institution="State Bank of India",
        last4="7710",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=date(2026, 3, 31),
    )
    other = await create_account(
        ledger,
        kind="bank",
        name="HDFC Savings",
        institution="HDFC Bank",
        last4="4821",
        opening_balance_paise=0,
    )
    # The user already logged the transfer between their own accounts.
    logged = await movements_mod.record_transfer(
        ledger,
        from_account_id=here["id"],
        to_account_id=other["id"],
        amount_paise=125000,
        description="TRANSFER TO SAVINGS 4821",
        date=date(2026, 4, 2),
        actor="user",
    )
    import_id = await helpers.upload(
        ledger,
        wallet_statement(
            institution="State Bank of India",
            period=("01/04/2026", "30/04/2026"),
            opening_paise=10_000_000,
            rows=[("02/04/2026", "TRANSFER TO SAVINGS 4821", 125000, 0)],
        ),
        account_id=here["id"],
    )
    assert await helpers.read_csv(ledger, import_id, "wallet") == "review"

    line = await ledger.fetchrow("SELECT * FROM statement_lines WHERE import_id = $1", import_id)
    assert line["match_state"] == "certain"
    assert line["movement_id"] == logged["id"]

    # The line's own text names the other account; the user confirms it on the review screen.
    assert line["match_detail"]["suggested_transfer_account_id"] == other["id"]
    await resolve_line(
        ledger, import_id=import_id, line_id=line["id"], resolution="confirm_transfer"
    )

    kept = await ledger.fetchrow("SELECT * FROM statement_lines WHERE id = $1", line["id"])
    assert kept["movement_id"] == logged["id"], "confirming the move must keep the matched record"
    assert kept["match_state"] == "certain"

    committed = await commit_import(ledger, import_id)
    assert committed["adjustment_paise"] == 0
    transfers = await ledger.fetch(
        """
        SELECT t.amount FROM pgledger_transfers t
        JOIN accounts a ON a.ledger_account_id = t.from_account_id
        WHERE a.id = $1 AND t.amount = 125000
        """,
        here["id"],
    )
    assert len(transfers) == 1, "one statement line, one movement out of the account"


# A review import that matched a logged row is not a ledger write -----------------------------


async def _review_import_with_line(ledger, bank, *, amount_paise: int) -> tuple[str, str]:
    import_id = await helpers.queued_import(
        ledger, bank, [helpers.row("SWIGGY BENGALURU", debit=amount_paise)]
    )
    line_id = await ledger.fetchval(
        "SELECT id FROM statement_lines WHERE import_id = $1", import_id
    )
    return import_id, line_id


async def test_a_certain_match_in_review_is_not_an_import_write(ledger):
    from arno.tools import write as write_tools
    from tests.invariants import check_ledger

    created = await create_account(
        ledger, kind="bank", name="Bank", institution="Bank", opening_balance_paise=10_000_000
    )
    bank = await ledger.fetchrow("SELECT * FROM v_accounts WHERE id = $1", created["id"])
    logged = await write_tools.record_movement(
        ledger,
        {
            "kind": "expense",
            "amount_rupees": "450.00",
            "account": "Bank",
            "description": "Swiggy order",
            "date": "2026-04-01",
        },
        message_id=None,
        conversation_id=None,
        not_duplicate=True,
    )
    assert logged["movement_id"]
    import_id, line_id = await _review_import_with_line(ledger, bank, amount_paise=45000)

    line = await ledger.fetchrow("SELECT * FROM statement_lines WHERE id = $1", line_id)
    assert line["match_state"] == "certain"
    assert line["movement_id"] == logged["movement_id"]

    codes = {violation.code for violation in await check_ledger(ledger)}
    assert "movement_from_uncommitted_import" not in codes, (
        "a review import that matched a logged row is not a ledger write"
    )


async def test_a_statement_line_that_created_its_own_movement_is_caught(ledger):
    from arno.ledger import movements as movements_mod
    from tests.invariants import check_ledger

    created = await create_account(
        ledger, kind="bank", name="Bank", institution="Bank", opening_balance_paise=10_000_000
    )
    bank = await ledger.fetchrow("SELECT * FROM v_accounts WHERE id = $1", created["id"])
    _import_id, line_id = await _review_import_with_line(ledger, bank, amount_paise=45000)
    created_movement = await movements_mod.record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=45000,
        description="Written before the import was committed",
        date=date(2026, 4, 1),
        idempotency_key=f"stmt:{line_id}",
    )
    await ledger.execute(
        "UPDATE statement_lines SET movement_id = $1 WHERE id = $2",
        created_movement["id"],
        line_id,
    )
    codes = {violation.code for violation in await check_ledger(ledger)}
    assert "movement_from_uncommitted_import" in codes


async def test_reconcile_issues_the_same_queries_for_any_number_of_lines(ledger, monkeypatch):
    """Two bulk preloads and one batch write, not a query per line."""
    account = await _account(ledger)
    for index in range(3):
        await movements_mod.record_expense(
            ledger,
            account_id=account["id"],
            amount_paise=10_000 + index,
            description=f"logged {index}",
            date=date(2026, 4, 2),
        )
    issued: list[str] = []
    for name in ("fetch", "fetchrow", "fetchval", "execute"):
        original = getattr(type(ledger), name)

        async def counted(self, query, *args, _original=original, **kwargs):
            issued.append(query)
            return await _original(self, query, *args, **kwargs)

        monkeypatch.setattr(type(ledger), name, counted)

    async def queries_for(count: int) -> int:
        import_id = await _import(ledger, account)
        rows = [
            {
                "date": "2026-04-02",
                "description": f"UPI/DR/{index}/shop{index}",
                "debit": 10_000 + index,
                "credit": 0,
            }
            for index in range(count)
        ]
        issued.clear()
        await insert_lines(
            ledger, import_id=import_id, account=account, parsed_account={"rows": rows}
        )
        await reconcile(
            ledger,
            import_id=import_id,
            account=account,
            period=(date(2026, 4, 1), date(2026, 4, 30)),
        )
        return len(issued)

    small = await queries_for(5)
    large = await queries_for(50)
    assert small == large
    assert large <= 12
