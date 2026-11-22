"""The gate: fresh sandbox, checks, and a replay on every approved stored statement."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from arno.importers.commit import commit_import
from arno.importers.gate import run_gate
from arno.importers.layout import detect_fingerprint
from arno.importers.layouts import layout_for, rollback_layout, save_version
from arno.importers.monty import ScriptContext
from arno.importers.readers import Doc, open_documents
from arno.importers.reconcile import insert_lines
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from tests.fixtures import import_helpers as helpers
from tests.import_fixtures import CARD_PARSER, SBI_PARSER, card_statement, sbi_csv


async def _active_layout(ledger, fingerprint: str) -> dict:
    active = await layout_for(ledger, fingerprint, institution="SBI", kind="bank")
    assert active is not None
    return active


ROWS = [
    {
        "date": "2026-04-01",
        "description": "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
        "debit": 45000,
        "credit": 0,
        "balance": 24522890,
    },
    {
        "date": "2026-04-05",
        "description": "NEFT CR/ACME PAYROLL/SALARY APRIL",
        "debit": 0,
        "credit": 10000000,
        "balance": 34522890,
    },
    {
        "date": "2026-04-12",
        "description": "UPI/DR/223456789012/AMAZON PAY INDIA/amazon@okhdfcbank",
        "debit": 120000,
        "credit": 0,
        "balance": 34402890,
    },
]


async def _attachment(conn, payload: bytes) -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', 'sbi.csv', 'text/csv', $2, $3, $4)
        """,
        attachment_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


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


async def _import(
    conn,
    *,
    account: dict,
    attachment_id: str,
    status: str,
    fingerprint: str,
) -> str:
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports
            (id, file_hash, status, attachment_id, account_id, institution, fingerprint,
             period_start, period_end, opening_balance_paise, closing_balance_paise,
             commit_groups)
        VALUES ($1, $2, $3, $4, $5, 'SBI', $6, '2026-04-01', '2026-04-30',
                24567890, 34402890, '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        status,
        attachment_id,
        account["id"],
        fingerprint,
    )
    return import_id


async def _committed(conn, account: dict, fingerprint: str) -> str:
    attachment_id = await _attachment(conn, sbi_csv())
    import_id = await _import(
        conn,
        account=account,
        attachment_id=attachment_id,
        status="committed",
        fingerprint=fingerprint,
    )
    raw_account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    await insert_lines(
        conn,
        import_id=import_id,
        account=raw_account,
        parsed_account={"rows": ROWS},
    )
    return import_id


def _context(import_id: str, fingerprint: str) -> ScriptContext:
    return ScriptContext(
        period=("2026-04-01", "2026-04-30"),
        ledger_opening_paise=24567890,
        fingerprint=fingerprint,
        expected_fingerprint=fingerprint,
        allow_vision=False,
        import_id=import_id,
    )


async def test_the_gate_replays_a_committed_statement_and_passes(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    committed = await _committed(ledger, account, fingerprint)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["checked"] == 1
    assert result["replay"]["passed"] == 1
    assert result["replay"]["mismatch"] is None
    assert committed != current


async def test_a_replay_mismatch_blocks_and_reports_the_first_row(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    committed = await _committed(ledger, account, fingerprint)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )
    changed = SBI_PARSER.replace(
        "'description': str(row[1])", "'description': str(row[1]) + ' MUT'"
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=changed
    )

    assert result["status"] == "failed", result
    mismatch = result["replay"]["mismatch"]
    assert mismatch["import_id"] == committed
    assert mismatch["row"] == 0
    assert mismatch["expected"]["description"].startswith("UPI/DR/123456789012")
    assert mismatch["got"]["description"].endswith(" MUT")


async def test_an_undone_import_leaves_the_replay_set(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    committed = await _committed(ledger, account, fingerprint)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )
    await ledger.execute(
        "UPDATE imports SET status = 'rejected', undone_at = now() WHERE id = $1", committed
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["checked"] == 0


async def test_a_purged_file_leaves_the_replay_set(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    committed = await _committed(ledger, account, fingerprint)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )
    await ledger.execute(
        """
        UPDATE attachments SET data = NULL, purged_at = now()
        WHERE id = (SELECT attachment_id FROM imports WHERE id = $1)
        """,
        committed,
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["checked"] == 0


async def _review_with_parser(conn, account: dict, fingerprint: str, parser: str) -> str:
    attachment_id = await _attachment(conn, sbi_csv())
    import_id = await _import(
        conn,
        account=account,
        attachment_id=attachment_id,
        status="review",
        fingerprint=fingerprint,
    )
    await conn.execute(
        """
        UPDATE imports
        SET parser = $1, closing_balance_paise = NULL, check_result = $2,
            check_status = 'VERIFIED'
        WHERE id = $3
        """,
        parser,
        {
            "opening_source": "printed",
            "opening_paise": 24567890,
            "closing_paise": 34402890,
        },
        import_id,
    )
    return import_id


async def test_two_commits_append_two_versions_and_move_the_pointer(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    first = await _review_with_parser(ledger, account, fingerprint, SBI_PARSER)
    await commit_import(ledger, first)

    active = await _active_layout(ledger, fingerprint)
    assert active["version"] == 1

    second = await _review_with_parser(ledger, account, fingerprint, SBI_PARSER + "\n# v2")
    await commit_import(ledger, second)

    active = await _active_layout(ledger, fingerprint)
    assert active["version"] == 2
    versions = await ledger.fetch("SELECT version FROM parser_versions ORDER BY version")
    assert [row["version"] for row in versions] == [1, 2]


async def test_a_commit_appends_a_version_and_rollback_moves_the_pointer(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    first = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER,
    )
    assert first["version"] == 1

    second = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER + "\n# second",
    )
    assert second["version"] == 2
    active = await _active_layout(ledger, fingerprint)
    assert active["version"] == 2
    assert active["parser"].endswith("# second")
    assert fingerprint in active["variants"]

    await rollback_layout(ledger, layout_id=first["id"], version=1)
    active = await _active_layout(ledger, fingerprint)
    assert active["version"] == 1
    assert active["parser"] == SBI_PARSER


async def test_a_version_from_another_helper_is_needing_repair(ledger):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    saved = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER,
    )
    await ledger.execute(
        "UPDATE parser_versions SET helper_version = 'old' WHERE id = $1",
        saved["version_id"],
    )
    account = await _account(ledger)
    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )

    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["needs_repair"] is True
    # The same text under the current helper is a new version, not the stale one.
    again = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER,
    )
    assert again["version"] == 2


def test_an_unlocked_pdf_copy_opens_without_the_password():
    locked = (Path(__file__).parent / "fixtures" / "locked-1234.pdf").read_bytes()
    doc = open_documents(locked, password="1234")[0]
    unlocked = doc.readable_bytes()
    assert unlocked != locked
    assert open_documents(unlocked)[0].kind == "pdf"


async def test_a_statement_without_detectable_headers_still_saves_and_replays(ledger):
    # An SBI PDF's headers aren't found before a parser runs, so its fingerprint is None.
    saved = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=None,
        parser="result = 1",
        source_import_id=None,
    )
    assert saved["version"] == 1
    variants = await ledger.fetchval(
        "SELECT variants FROM statement_layouts WHERE id = $1", saved["id"]
    )
    assert list(variants) == []

    found = await layout_for(ledger, None, institution="SBI", kind="bank")
    assert found is not None and found["parser"] == "result = 1"
    assert await layout_for(ledger, None, institution="HDFC", kind="bank") is None
    assert await layout_for(ledger, None) is None


async def test_two_active_versions_for_one_layout_are_rejected_and_activation_swaps_atomically(
    ledger,
):
    import asyncpg
    import pytest

    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    first = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER,
    )
    second = await save_version(
        ledger,
        institution="SBI",
        kind="bank",
        fingerprint=fingerprint,
        parser=SBI_PARSER + "\n# v2",
    )

    rows = await ledger.fetch(
        "SELECT id, version, is_active FROM parser_versions WHERE layout_id = $1 ORDER BY version",
        first["id"],
    )
    assert [(r["version"], r["is_active"]) for r in rows] == [(1, False), (2, True)]

    with pytest.raises(asyncpg.UniqueViolationError) as exc:
        async with ledger.transaction():
            await ledger.execute(
                "UPDATE parser_versions SET is_active = true WHERE id = $1",
                first["version_id"],
            )
    assert getattr(exc.value, "constraint_name", None) == "parser_versions_layout_active_idx"

    # A layout with no active version remains representable and fails closed during lookup.
    await ledger.execute(
        "UPDATE parser_versions SET is_active = false WHERE layout_id = $1", first["id"]
    )
    assert await layout_for(ledger, fingerprint) is None

    # Failed rollback leaves the current active version intact because the swap is transactional.
    await rollback_layout(ledger, layout_id=first["id"], version=2)
    with pytest.raises(ValueError):
        await rollback_layout(ledger, layout_id=first["id"], version=99)
    active = await _active_layout(ledger, fingerprint)
    assert active is not None and active["version"] == 2
    assert active["version_id"] == second["version_id"]

    await rollback_layout(ledger, layout_id=first["id"], version=1)
    rows = await ledger.fetch(
        "SELECT id, version, is_active FROM parser_versions WHERE layout_id = $1 ORDER BY version",
        first["id"],
    )
    assert [(r["version"], r["is_active"]) for r in rows] == [(1, True), (2, False)]


# Replay order, a new header variant, and a card parser ---------------------------------------


async def test_the_gate_compares_rows_in_parser_order_not_id_order(ledger, monkeypatch):
    fingerprint = detect_fingerprint(Doc(sbi_csv()))
    assert fingerprint
    account = await _account(ledger)
    import_id = await new_id(ledger, "imp")
    attachment_id = await _attachment(ledger, sbi_csv())
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, account_id, institution,
                             fingerprint, period_start, period_end, commit_groups)
        VALUES ($1, 'hash-order', 'committed', $2, $3, 'SBI', $4, '2026-04-01', '2026-04-30',
                '[]'::jsonb)
        """,
        import_id,
        attachment_id,
        account["id"],
        fingerprint,
    )

    # Two UUIDv7 values in one millisecond may sort newest-first; the gate must not care.
    state = {"n": 0}

    async def fake(_conn, _prefix):
        state["n"] += 1
        return f"stl_{1000 - state['n']:04d}"

    monkeypatch.setattr("arno.importers.reconcile.new_id", fake)
    await insert_lines(
        ledger,
        import_id=import_id,
        account=account,
        parsed_account={"rows": ROWS},
    )
    monkeypatch.undo()

    current = await _import(
        ledger,
        account=account,
        attachment_id=await _attachment(ledger, sbi_csv()),
        status="parsing",
        fingerprint=fingerprint,
    )
    result = await run_gate(
        doc=Doc(sbi_csv()), context=_context(current, fingerprint), code=SBI_PARSER
    )

    assert result["status"] == "parser_submitted", result
    assert result["replay"]["checked"] == 1
    assert result["replay"]["mismatch"] is None


async def test_saving_a_new_header_variant_of_a_known_layout_appends_it(ledger):
    first = await save_version(
        ledger,
        institution="PhonePe",
        kind="wallet",
        fingerprint="date|narration|chq ref no|debit|credit|balance",
        parser="rows = []\nrows",
    )
    # The next month's export normalizes one header differently: a second variant, not a crash.
    second = await save_version(
        ledger,
        institution="PhonePe",
        kind="wallet",
        fingerprint="date|narration|ref no|debit|credit|balance",
        parser="rows = []\nrows",
    )
    assert second["id"] == first["id"]
    layout = await ledger.fetchrow(
        "SELECT variants FROM statement_layouts WHERE id = $1", first["id"]
    )
    assert sorted(layout["variants"]) == [
        "date|narration|chq ref no|debit|credit|balance",
        "date|narration|ref no|debit|credit|balance",
    ]
    # The same variant twice stays one entry.
    await save_version(
        ledger,
        institution="PhonePe",
        kind="wallet",
        fingerprint="date|narration|ref no|debit|credit|balance",
        parser="rows = []\nrows",
    )
    layout = await ledger.fetchrow(
        "SELECT variants FROM statement_layouts WHERE id = $1", first["id"]
    )
    assert len(layout["variants"]) == 2


async def test_a_saved_card_parser_replays_its_committed_statement_unchanged(ledger):
    from arno.agent.parser import submit_parser_code
    from arno.importers.commit import commit_import

    card = await helpers.card(ledger, opening_paise=-100000)
    april = await helpers.upload(
        ledger,
        card_statement([("03/04/2026", "SWIGGY BANGALORE", "-350.00")], "1,350.00"),
        account_id=card["id"],
    )
    submitted = await submit_parser_code(ledger, import_ids=[april], code=CARD_PARSER)
    assert submitted["status"] == "parser_submitted", submitted
    await commit_import(ledger, april)
    before = [
        (row["date"], row["description_raw"], row["amount_paise"], row["balance_paise"])
        for row in await ledger.fetch(
            "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id", april
        )
    ]

    may = await helpers.upload(
        ledger,
        card_statement(
            [("04/05/2026", "AMAZON PAY INDIA", "-100.00")],
            "1,450.00",
            start="01/05/2026",
            end="31/05/2026",
        ),
        account_id=card["id"],
    )
    replayed = await submit_parser_code(ledger, import_ids=[may], code=CARD_PARSER)
    assert replayed["status"] == "parser_submitted", replayed
    assert replayed["replay"]["checked"] == 1
    assert replayed["replay"]["passed"] == 1
    assert replayed["replay"]["mismatch"] is None
    after = [
        (row["date"], row["description_raw"], row["amount_paise"], row["balance_paise"])
        for row in await ledger.fetch(
            "SELECT * FROM statement_lines WHERE import_id = $1 ORDER BY date, id", april
        )
    ]
    assert after == before
