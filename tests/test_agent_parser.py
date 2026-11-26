"""The import gate helpers: transfer suggestions and the committed-import guard."""

from __future__ import annotations

from datetime import date

from arno.agent.deps import AgentDeps
from arno.agent.parser import names_account, submit_parser_code, suggest_transfers
from arno.importers.commit import commit_import
from arno.importers.pipeline import create_import
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from tests.agent_helpers import run_context
from tests.import_fixtures import SBI_PARSER, sbi_csv


async def _att(conn, payload: bytes, name: str = "statement.csv") -> str:
    import hashlib

    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', $2, 'text/csv', $3, $4, $5)
        """,
        attachment_id,
        name,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


async def _sbi_account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


async def _sbi_account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


async def test_own_account_suggestions_land_on_the_lines(ledger):
    account = await _sbi_account(ledger)
    # The suggested row names this account ("AMAZON PAY"), so the host accepts it.
    other = await create_account(
        ledger,
        kind="wallet",
        name="Amazon Pay",
        institution="Amazon",
        opening_balance_paise=100000,
        opening_balance_as_of=date(2026, 3, 31),
    )
    attachment_id = await _att(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    submitted = await submit_parser_code(ledger, import_ids=[row["id"]], code=SBI_PARSER)
    assert submitted["status"] == "parser_submitted", submitted
    result = await suggest_transfers(
        run_context(AgentDeps(conversation_id="conv_suggest")),
        import_id=row["id"],
        suggestions=[{"row": 2, "account_id": other["id"]}],
    )

    assert result["status"] == "suggested"
    lines = await ledger.fetch(
        "SELECT match_detail FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
        row["id"],
    )
    assert lines[2]["match_detail"]["suggested_transfer_account_id"] == other["id"]
    assert all("suggested_category" not in line["match_detail"] for line in lines)


def test_a_transfer_suggestion_needs_the_row_to_name_its_account():
    card = {"name": "Slice Credit card", "institution": None, "kind": "card", "last4": None}
    repayment = "UPI/DR/1/slice sm/NESF/ccrepaymen/UPI"
    own_name = "UPI/DR/2/JOY MRIDHA/PPIW/ID-1"
    assert names_account(repayment, card, only_card=True)
    # The user's own name names no account: it may be a wallet or a bank the ledger lacks.
    assert not names_account(own_name, card, only_card=True)
    # A card-payment phrase alone names a card only when the user has one card.
    other_card = {"name": "HDFC Regalia", "institution": "HDFC", "kind": "card", "last4": None}
    assert names_account("CC PAYMENT 1234", other_card, only_card=True)
    assert not names_account("CC PAYMENT 1234", other_card, only_card=False)
    # A UPI narration prints the payee's bank code; it does not name the user's HDFC card.
    hdfc = {"name": "HDFC Regalia", "institution": "HDFC", "kind": "card", "last4": None}
    assert not names_account("UPI/DR/1/SWIGGY/HDFC/swiggy@hdfc", hdfc, only_card=False)
    assert names_account("HDFC CC PAYMENT", hdfc, only_card=False)
    bank = {"name": "SBI main", "institution": "SBI", "kind": "bank", "last4": "6789"}
    assert names_account("NEFT/TRANSFER TO A/C XX6789", bank, only_card=False)


async def test_a_committed_import_cannot_be_parsed_and_booked_again(ledger):
    account = await _sbi_account(ledger)
    attachment_id = await _att(ledger, sbi_csv(), "sbi.csv")
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, account_id, commit_groups)
        VALUES ($1, 'hash-resubmit', 'parsing', $2, $3, '[]'::jsonb)
        """,
        import_id,
        attachment_id,
        account["id"],
    )
    first = await submit_parser_code(ledger, import_ids=[import_id], code=SBI_PARSER)
    assert first["status"] == "parser_submitted", first
    await commit_import(ledger, import_id)
    before = await ledger.fetch(
        "SELECT id, movement_id FROM statement_lines WHERE import_id = $1 ORDER BY id", import_id
    )
    assert len(before) == 3

    again = await submit_parser_code(ledger, import_ids=[import_id], code=SBI_PARSER)

    assert again["status"] == "failed"
    assert "already committed" in again["reason"]
    after = await ledger.fetch(
        "SELECT id, movement_id FROM statement_lines WHERE import_id = $1 ORDER BY id", import_id
    )
    assert after == before
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == (
        "committed"
    )
    assert await ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'expense'") == 2
