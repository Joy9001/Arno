from __future__ import annotations

from fin_agent.api import provenance
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.movements import get_movement, record_expense


def test_movement_detail_exposes_only_real_chat_provenance(ledger):
    bank = create_account(
        ledger, kind="bank", name="Provenance bank", opening_balance_paise=100_000
    )
    ledger.execute("INSERT INTO conversations (id, title) VALUES ('cnv_prov', 'Recorded lunch')")
    ledger.execute(
        """
        INSERT INTO messages (id, conversation_id, client_id, role, content)
        VALUES ('msg_prov', 'cnv_prov', 'client_prov', 'user', '{}'::jsonb)
        """
    )
    movement = record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=5_000,
        description="Lunch",
        capture={"kind": "chat", "ref": "msg_prov"},
    )

    result = provenance.attach_sources(ledger, movement["id"], get_movement(ledger, movement["id"]))

    assert result["sources"] == [
        {
            "kind": "chat",
            "ref": "msg_prov",
            "message_id": "msg_prov",
            "conversation_id": "cnv_prov",
        }
    ]


def test_movement_detail_has_no_invented_source(ledger):
    bank = create_account(ledger, kind="bank", name="No source bank", opening_balance_paise=100_000)
    movement = record_expense(
        ledger, account_id=bank["id"], amount_paise=5_000, description="Lunch"
    )

    result = provenance.attach_sources(ledger, movement["id"], get_movement(ledger, movement["id"]))

    assert result["sources"] == []
